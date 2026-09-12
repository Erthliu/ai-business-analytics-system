"""Release-level configuration, migration, logging, and sample-data checks."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect

from config.logging_config import SecretRedactionFilter
from config.settings import load_settings
from database.models import Base
from pipeline.sales_validation import validate_sales_dataframe
from pipeline.validation import validate_dataframe


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_migrated_schema_matches_model_tables_and_columns(tmp_path: Path) -> None:
    """Fresh migrations and SQLAlchemy metadata must not materially drift."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'schema.db'}"
    command.upgrade(_config(database_url), "head")
    database = inspect(create_engine(database_url))
    migrated_tables = set(database.get_table_names()) - {"alembic_version"}
    assert migrated_tables == set(Base.metadata.tables)
    for table_name, table in Base.metadata.tables.items():
        migrated_columns = {column["name"] for column in database.get_columns(table_name)}
        assert migrated_columns == set(table.columns.keys())


def test_recent_migration_downgrade_and_reupgrade(tmp_path: Path) -> None:
    """The most recent migration boundary can be rolled back and restored."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'boundary.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260911_08")
    assert "workflow_runs" not in inspect(create_engine(database_url)).get_table_names()
    command.upgrade(config, "head")
    assert "workflow_runs" in inspect(create_engine(database_url)).get_table_names()


def test_invalid_settings_fail_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(RuntimeError, match="LOG_LEVEL"):
        load_settings()
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="DATABASE_CONNECT_TIMEOUT_SECONDS"):
        load_settings()


def test_secret_redaction_filter() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactionFilter())
    logger = logging.getLogger("release-redaction-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info(
        "database=postgresql+psycopg://user:secret@localhost/db api_key=abc123"
    )
    output = stream.getvalue()
    assert "secret" not in output
    assert "abc123" not in output
    assert output.count("[REDACTED]") == 2


def test_public_sample_supports_golden_demo() -> None:
    dataframe = pd.read_csv(PROJECT_ROOT / "data" / "sample" / "sales.csv")
    dates = pd.to_datetime(dataframe["order_date"])
    result = validate_sales_dataframe(dataframe, validate_dataframe(
        dataframe,
        required_columns=(
            "order_id", "order_date", "customer_id", "product_id", "region",
            "quantity", "unit_price", "revenue",
        ),
        numeric_columns=("quantity", "unit_price", "revenue"),
        unique_columns=("order_id",),
        date_columns=("order_date",),
    ))
    assert result.passed
    assert set(dates.dt.to_period("M").astype(str)) == {"2024-07", "2024-08"}
    assert dataframe["region"].nunique() == 4
    assert dataframe["product_id"].nunique() >= 5
    assert dataframe.groupby(dates.dt.month)["revenue"].sum().nunique() == 2
