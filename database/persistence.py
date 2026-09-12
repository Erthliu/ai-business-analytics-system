"""Database setup and idempotent pipeline-run persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database.models import Base, DatasetLineage, DatasetVersion, PipelineRun

logger = logging.getLogger(__name__)


def create_database_engine(
    database_url: str, connect_timeout_seconds: int | None = None
) -> Engine:
    """Create an engine with an explicit connection timeout at the database boundary."""
    if connect_timeout_seconds is None:
        raw_timeout = os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10")
        try:
            connect_timeout_seconds = int(raw_timeout)
        except ValueError as error:
            raise ValueError(
                "DATABASE_CONNECT_TIMEOUT_SECONDS must be a positive integer."
            ) from error
    if connect_timeout_seconds <= 0:
        raise ValueError("connect_timeout_seconds must be positive.")
    backend = make_url(database_url).get_backend_name()
    connect_args: dict[str, object] = {}
    if backend == "postgresql":
        connect_args["connect_timeout"] = connect_timeout_seconds
    elif backend == "sqlite":
        connect_args["timeout"] = float(connect_timeout_seconds)
    return create_engine(database_url, future=True, pool_pre_ping=True, connect_args=connect_args)


def initialize_test_database(engine: Engine) -> None:
    """Create schema only for isolated unit tests; production uses Alembic."""
    Base.metadata.create_all(engine)


class PipelineRunStore:
    """Persist runs and lineage while preventing duplicate source processing."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def get_or_create_run(self, source_path: Path, source_hash: str) -> tuple[PipelineRun, bool]:
        """Return a run for a source hash and whether this call created it."""
        with self._sessions() as session:
            existing = session.scalar(select(PipelineRun).where(PipelineRun.source_hash == source_hash))
            if existing is not None:
                return existing, False

            run = PipelineRun(
                run_id=str(uuid4()),
                source_hash=source_hash,
                source_path=str(source_path),
                status="running",
            )
            session.add(run)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(PipelineRun).where(PipelineRun.source_hash == source_hash))
                if existing is None:
                    raise
                return existing, False
            logger.info(
                "event=pipeline_run_created pipeline_run_id=%s status=running", run.run_id
            )
            return run, True

    def dataset_version_for_run(self, run_id: str) -> DatasetVersion | None:
        """Return the immutable dataset version associated with a pipeline run."""
        with self._sessions() as session:
            return session.scalar(
                select(DatasetVersion)
                .join(PipelineRun, DatasetVersion.pipeline_run_id == PipelineRun.id)
                .where(PipelineRun.run_id == run_id)
            )

    def wait_for_terminal_run(
        self, run_id: str, timeout_seconds: float = 10.0
    ) -> tuple[PipelineRun, DatasetVersion | None]:
        """Wait briefly for a concurrent winner, never indefinitely."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        deadline = monotonic() + timeout_seconds
        while True:
            with self._sessions() as session:
                run = session.scalar(select(PipelineRun).where(PipelineRun.run_id == run_id))
                if run is None:
                    raise LookupError(f"Pipeline run not found: {run_id}")
                version = session.scalar(
                    select(DatasetVersion).where(DatasetVersion.pipeline_run_id == run.id)
                )
                if run.status != "running":
                    return run, version
            if monotonic() >= deadline:
                raise TimeoutError(
                    f"Pipeline run {run_id} is still running after {timeout_seconds:.1f}s."
                )
            sleep(0.05)

    def complete_run(
        self,
        run_id: str,
        *,
        row_count: int,
        validation_passed: bool,
        validation_statistics: dict[str, object],
        source_path: Path,
        source_hash: str,
        status: str = "completed",
    ) -> PipelineRun:
        """Record a completed run and its input-to-dataframe lineage edge."""
        with self._sessions.begin() as session:
            run = session.scalar(select(PipelineRun).where(PipelineRun.run_id == run_id))
            if run is None:
                raise LookupError(f"Pipeline run not found: {run_id}")
            run.status = status
            run.completed_at = datetime.now(timezone.utc)
            run.input_row_count = row_count
            run.validation_passed = validation_passed
            run.validation_statistics = validation_statistics
            session.add(
                DatasetLineage(
                    pipeline_run_id=run.id,
                    source_uri=source_path.resolve().as_uri(),
                    source_hash=source_hash,
                    output_name="ingested_dataframe",
                    transformation="csv_ingestion_and_validation",
                )
            )
        return run

    def fail_run(self, run_id: str, message: str) -> None:
        """Mark a run failed with an operational error that must not contain secrets."""
        with self._sessions.begin() as session:
            run = session.scalar(select(PipelineRun).where(PipelineRun.run_id == run_id))
            if run is None:
                raise LookupError(f"Pipeline run not found: {run_id}")
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = message

    def add_dataset_version(
        self, run_id: str, source_path: Path, content_hash: str, storage_uri: str,
        schema_fingerprint: str, row_count: int,
    ) -> DatasetVersion:
        """Persist the immutable source identity after retention and inspection."""
        with self._sessions.begin() as session:
            run = session.scalar(select(PipelineRun).where(PipelineRun.run_id == run_id))
            if run is None:
                raise LookupError(f"Pipeline run not found: {run_id}")
            version = DatasetVersion(
                dataset_id=str(uuid5(NAMESPACE_URL, source_path.resolve().as_uri())),
                version_id=str(uuid4()),
                content_hash=content_hash,
                source_uri=source_path.resolve().as_uri(),
                storage_uri=storage_uri,
                schema_fingerprint=schema_fingerprint,
                row_count=row_count,
                pipeline_run_id=run.id,
            )
            session.add(version)
        return version
