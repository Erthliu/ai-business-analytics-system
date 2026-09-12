"""Tests for durable, idempotent pipeline execution."""

from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from database.models import DatasetLineage, PipelineRun
from database.persistence import create_database_engine, initialize_test_database
from pipeline.execution import execute_pipeline


def test_same_source_is_processed_once_and_records_lineage(tmp_path: Path) -> None:
    """A source hash maps to one run and one lineage edge across replays."""
    csv_file = tmp_path / "sales.csv"
    csv_file.write_text(
        "order_id,order_date,revenue\nORD-1,2026-01-01,100.0\n",
        encoding="utf-8",
    )
    database_url = f"sqlite+pysqlite:///{tmp_path / 'pipeline.db'}"
    initialize_test_database(create_database_engine(database_url))

    first = execute_pipeline(
        csv_file,
        database_url,
        required_columns=["order_id", "order_date", "revenue"],
        numeric_columns=["revenue"],
        unique_columns=["order_id"],
        date_columns=["order_date"],
        numeric_ranges={"revenue": (0, None)},
    )
    replay = execute_pipeline(csv_file, database_url)

    assert first.status == "completed"
    assert not first.idempotent_replay
    assert first.validation_result is not None
    assert first.validation_result.passed
    assert replay.idempotent_replay
    assert replay.run_id == first.run_id

    engine = create_engine(database_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PipelineRun)) == 1
        assert session.scalar(select(func.count()).select_from(DatasetLineage)) == 1
