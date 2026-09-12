"""Reliable orchestration of deterministic V1 pipeline components."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Iterable, Mapping

from database.persistence import PipelineRunStore, create_database_engine
from pipeline.ingestion import hash_file, load_csv
from pipeline.sales_validation import validate_sales_dataframe
from pipeline.validation import ValidationResult, validate_dataframe
from storage.raw_storage import FileSystemRawStorage, RawStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineExecutionResult:
    """Outcome and durable identity of a pipeline invocation."""

    run_id: str
    status: str
    idempotent_replay: bool
    validation_result: ValidationResult | None
    dataset_version: str | None = None


def execute_pipeline(
    csv_path: str | Path,
    database_url: str,
    *,
    required_columns: Iterable[str] = (),
    numeric_columns: Iterable[str] = (),
    unique_columns: Iterable[str] = (),
    date_columns: Iterable[str] = (),
    numeric_ranges: Mapping[str, tuple[float | None, float | None]] | None = None,
    raw_storage: RawStorage | None = None,
    apply_sales_policy: bool = False,
) -> PipelineExecutionResult:
    """Run ingestion and validation once per source content hash.

    A repeat invocation with byte-identical source data returns the original
    completed run without creating duplicate metadata or lineage records.
    """
    started = perf_counter()
    source_path = Path(csv_path).expanduser()
    source_hash = hash_file(source_path)
    engine = create_database_engine(database_url)
    store = PipelineRunStore(engine)
    run, created = store.get_or_create_run(source_path, source_hash)
    if not created:
        if run.status == "running":
            run, version = store.wait_for_terminal_run(run.run_id)
        else:
            version = store.dataset_version_for_run(run.run_id)
        logger.info(
            "event=pipeline_replayed pipeline_run_id=%s status=%s replayed=true duration=%.3f",
            run.run_id,
            run.status,
            perf_counter() - started,
        )
        return PipelineExecutionResult(
            run.run_id,
            run.status,
            True,
            None,
            version.version_id if version is not None else None,
        )

    try:
        dataframe = load_csv(source_path)
        validation = validate_dataframe(
            dataframe,
            required_columns=required_columns,
            numeric_columns=numeric_columns,
            unique_columns=unique_columns,
            date_columns=date_columns,
            numeric_ranges=numeric_ranges,
        )
        if apply_sales_policy:
            validation = validate_sales_dataframe(dataframe, validation)
        storage = raw_storage or FileSystemRawStorage(source_path.parent.parent / "raw")
        storage_uri = storage.retain(source_path, source_hash)
        schema = json.dumps(
            [{"name": name, "dtype": str(dtype)} for name, dtype in dataframe.dtypes.items()],
            sort_keys=True,
        )
        dataset_version = store.add_dataset_version(
            run.run_id, source_path, source_hash, storage_uri,
            hashlib.sha256(schema.encode("utf-8")).hexdigest(), len(dataframe),
        )
        store.complete_run(
            run.run_id,
            row_count=len(dataframe),
            validation_passed=validation.passed,
            validation_statistics=validation.statistics,
            source_path=source_path,
            source_hash=source_hash,
            status={
                "PASS": "completed",
                "WARN": "completed",
                "REJECT": "rejected",
                "QUARANTINE": "quarantined",
            }[validation.outcome],
        )
    except Exception as error:
        logger.error(
            "event=pipeline_failed pipeline_run_id=%s status=failed error_type=%s duration=%.3f",
            run.run_id,
            type(error).__name__,
            perf_counter() - started,
        )
        store.fail_run(run.run_id, "Pipeline execution failed; inspect secure application logs.")
        raise

    logger.info(
        "event=pipeline_completed pipeline_run_id=%s dataset_version_id=%s "
        "status=%s replayed=false row_count=%d duration=%.3f",
        run.run_id,
        dataset_version.version_id,
        validation.outcome,
        len(dataframe),
        perf_counter() - started,
    )
    return PipelineExecutionResult(
        run.run_id,
        "completed" if validation.passed else validation.outcome.lower(),
        False,
        validation,
        dataset_version.version_id,
    )
