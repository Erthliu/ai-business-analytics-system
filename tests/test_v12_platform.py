"""V1.2 migration, retention, outcome, and artifact-contract tests."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.analysis_artifact import AnalysisArtifact
from database.models import DatasetVersion, PipelineRun
from database.persistence import create_database_engine, initialize_test_database
from pipeline.execution import execute_pipeline
from pipeline.sales_validation import validate_sales_dataframe
from pipeline.validation import ValidationOutcome, validate_dataframe
from storage.raw_storage import FileSystemRawStorage


def test_initial_migration_creates_all_tables(tmp_path: Path) -> None:
    """The Alembic revision, rather than create_all, creates the schema."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    assert {"pipeline_runs", "dataset_versions", "dataset_lineage"}.issubset(
        inspect(create_engine(database_url)).get_table_names()
    )


def test_unknown_migration_revision_fails_explicitly(tmp_path: Path) -> None:
    """A bad migration target fails loudly instead of silently changing schema."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{tmp_path / 'bad-target.db'}")

    with pytest.raises(CommandError):
        command.upgrade(config, "does_not_exist")


def test_unknown_region_is_quarantined() -> None:
    """Suspicious-but-retainable domain data is quarantined, not analyzed."""
    dataframe = pd.DataFrame({
        "order_date": ["2026-01-01"], "region": ["Moon"], "quantity": [1],
        "unit_price": [10.0], "revenue": [10.0],
    })
    result = validate_sales_dataframe(dataframe, validate_dataframe(dataframe))

    assert result.outcome == ValidationOutcome.QUARANTINE
    assert not result.passed


def test_sales_revenue_mismatch_is_rejected() -> None:
    """Fatal integrity failures cannot pass to analytics."""
    dataframe = pd.DataFrame({
        "order_date": ["2026-01-01"], "region": ["North"], "quantity": [2],
        "unit_price": [10.0], "revenue": [19.0],
    })
    result = validate_sales_dataframe(dataframe, validate_dataframe(dataframe))

    assert result.outcome == ValidationOutcome.REJECT
    assert not result.passed


def test_raw_retention_and_changed_bytes_create_distinct_versions(tmp_path: Path) -> None:
    """Retention is immutable by content hash and changed input becomes a new version."""
    source = tmp_path / "sales.csv"
    source.write_text("order_id,order_date,revenue\nORD-1,2026-01-01,10\n", encoding="utf-8")
    database_url = f"sqlite+pysqlite:///{tmp_path / 'platform.db'}"
    initialize_test_database(create_database_engine(database_url))
    storage = FileSystemRawStorage(tmp_path / "retained")

    first = execute_pipeline(source, database_url, raw_storage=storage)
    retained = list((tmp_path / "retained").glob("*.csv"))
    source.write_text("order_id,order_date,revenue\nORD-1,2026-01-01,11\n", encoding="utf-8")
    second = execute_pipeline(source, database_url, raw_storage=storage)

    assert first.dataset_version != second.dataset_version
    assert len(retained) == 1
    assert len(list((tmp_path / "retained").glob("*.csv"))) == 2
    with Session(create_engine(database_url)) as session:
        assert len(session.scalars(select(DatasetVersion)).all()) == 2


def test_unique_source_hash_rolls_back_duplicate_insert(tmp_path: Path) -> None:
    """The database enforces duplicate-source safety as a transaction boundary."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'rollback.db'}"
    engine = create_database_engine(database_url)
    initialize_test_database(engine)
    with Session(engine) as session:
        session.add(PipelineRun(run_id="one", source_hash="a" * 64, source_path="file:///a", status="running"))
        session.commit()
        session.add(PipelineRun(run_id="two", source_hash="a" * 64, source_path="file:///b", status="running"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(session.scalars(select(PipelineRun)).all()) == 1


def test_artifact_contract_is_schema_validatable() -> None:
    """Valid data builds an artifact and invalid confidence is rejected."""
    artifact = AnalysisArtifact(
        pipeline_run_id="run-1", dataset_version="version-1", question="What changed?",
        metrics={"revenue": 100.0}, findings=["Revenue increased."],
        evidence=[{"source": "dataset", "detail": "sales.csv"}], assumptions=[],
        limitations=["Synthetic data"], confidence=0.8,
        producer_name="future-analytics-agent", producer_version="1.0",
    )

    assert AnalysisArtifact.json_schema()["title"] == "AnalysisArtifact"
    assert artifact.confidence == 0.8
    with pytest.raises(ValueError):
        AnalysisArtifact(
            pipeline_run_id="run-1", dataset_version="version-1", question="x",
            metrics={}, findings=[], evidence=[], assumptions=[], limitations=[],
            confidence=2, producer_name="test", producer_version="1",
        )
