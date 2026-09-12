"""Tests executed only when a real PostgreSQL DATABASE_URL is supplied."""

import os
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Base, DatasetLineage, DatasetVersion, PipelineRun
from pipeline.execution import execute_pipeline
from profiling.profiler import profile_dataset_version
from orchestration.orchestrator import WorkflowOrchestrator
from orchestration.store import StaleWorkflowVersionError, WorkflowStore
from storage.raw_storage import FileSystemRawStorage

DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DATABASE_URL.startswith("postgresql"),
        reason="requires a real PostgreSQL DATABASE_URL",
    ),
]


def test_postgres_server_and_migration_head() -> None:
    """Prove this test is connected to PostgreSQL with the complete migration chain."""
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        assert connection.dialect.name == "postgresql"
        assert connection.dialect.server_version_info >= (16,)
        assert MigrationContext.configure(connection).get_current_revision() == "20260911_09"
        database = inspect(connection)
        assert set(database.get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
        for table_name, table in Base.metadata.tables.items():
            assert {column["name"] for column in database.get_columns(table_name)} == set(
                table.columns.keys()
            )


def test_postgres_persistence_lineage_and_idempotency(tmp_path: Path) -> None:
    """Exercise migrated PostgreSQL schema, unique hash, replay, and lineage."""
    source = tmp_path / "sales.csv"
    source.write_text(
        f"order_id,order_date,revenue\n{tmp_path.parent.name}-{tmp_path.name},2026-01-01,10\n",
        encoding="utf-8",
    )
    storage = FileSystemRawStorage(tmp_path / "retained")

    first = execute_pipeline(source, DATABASE_URL, raw_storage=storage)
    replay = execute_pipeline(source, DATABASE_URL, raw_storage=storage)

    assert first.status == "completed"
    assert replay.idempotent_replay
    assert replay.run_id == first.run_id
    with Session(create_engine(DATABASE_URL)) as session:
        run = session.scalar(select(PipelineRun).where(PipelineRun.run_id == first.run_id))
        assert run is not None
        assert len(session.scalars(
            select(DatasetVersion).where(DatasetVersion.pipeline_run_id == run.id)
        ).all()) == 1
        assert len(session.scalars(
            select(DatasetLineage).where(DatasetLineage.pipeline_run_id == run.id)
        ).all()) == 1


def test_postgres_concurrent_duplicate_ingestion(tmp_path: Path) -> None:
    """Concurrent identical source requests converge on one idempotent run."""
    source = tmp_path / "concurrent.csv"
    source.write_text(
        f"order_id,order_date,revenue\n{tmp_path.parent.name}-{tmp_path.name},2026-01-01,10\n",
        encoding="utf-8",
    )
    storage = FileSystemRawStorage(tmp_path / "retained")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: execute_pipeline(source, DATABASE_URL, raw_storage=storage), range(2)
        ))

    assert {result.run_id for result in results} == {results[0].run_id}
    assert sum(result.idempotent_replay for result in results) == 1
    assert {result.status for result in results} == {"completed"}
    assert len({result.dataset_version for result in results}) == 1


def test_postgres_unique_constraint_rollback_is_clean(tmp_path: Path) -> None:
    """A PostgreSQL uniqueness failure rolls back without retaining the losing row."""
    source_hash = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()
    engine = create_engine(DATABASE_URL)
    with Session(engine) as session:
        session.add(PipelineRun(
            run_id=f"winner-{source_hash[:28]}", source_hash=source_hash,
            source_path="test://winner", status="completed",
        ))
        session.commit()
        session.add(PipelineRun(
            run_id=f"loser-{source_hash[:29]}", source_hash=source_hash,
            source_path="test://loser", status="running",
        ))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(session.scalars(
            select(PipelineRun).where(PipelineRun.source_hash == source_hash)
        ).all()) == 1


def test_postgres_concurrent_workflow_compare_and_swap(tmp_path: Path) -> None:
    """Two PostgreSQL mutations of one snapshot produce one winner and one stale writer."""
    source = tmp_path / "workflow.csv"
    source.write_text(
        f"order_id,order_date,revenue\n{tmp_path.parent.name}-PG-WORKFLOW,2024-08-01,10\n",
        encoding="utf-8",
    )
    ingestion = execute_pipeline(
        source, DATABASE_URL, raw_storage=FileSystemRawStorage(tmp_path / "retained")
    )
    profile = profile_dataset_version(ingestion.dataset_version, DATABASE_URL)
    orchestrator = WorkflowOrchestrator(DATABASE_URL)
    workflow = orchestrator.create_workflow(profile.dataset_version_id, "total revenue")
    snapshots = [orchestrator.get(workflow.workflow_run_id) for _ in range(2)]
    barrier = Barrier(2)

    def save(snapshot):
        barrier.wait()
        try:
            with Session(create_engine(DATABASE_URL)) as session:
                WorkflowStore(session).save(snapshot, snapshot.version)
            return "saved"
        except StaleWorkflowVersionError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, snapshots))

    assert sorted(outcomes) == ["saved", "stale"]
    assert orchestrator.get(workflow.workflow_run_id).version == workflow.version + 1
