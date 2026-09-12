"""Deterministic and optional-enrichment profile tests."""

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts.dataset_profile import DatasetProfile
from database.models import DatasetProfileRecord
from database.persistence import create_database_engine, initialize_test_database
from pipeline.execution import execute_pipeline
from profiling.exceptions import DatasetNotProfileableError
from profiling.profiler import profile_dataframe, profile_dataset_version, sanitize_profile_metadata
from storage.raw_storage import FileSystemRawStorage


def _prepared_version(tmp_path: Path, csv: str) -> tuple[str, str, FileSystemRawStorage]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "sales.csv"
    source.write_text(csv, encoding="utf-8")
    url = f"sqlite+pysqlite:///{tmp_path / 'profiles.db'}"
    initialize_test_database(create_database_engine(url))
    storage = FileSystemRawStorage(tmp_path / "raw")
    result = execute_pipeline(source, url, raw_storage=storage, apply_sales_policy=True)
    assert result.dataset_version
    return result.dataset_version, url, storage


def test_numeric_categorical_dates_nulls_and_identifier_heuristic() -> None:
    """Authoritative profile contains required deterministic statistics."""
    dataframe = pd.DataFrame({
        "order_id": ["A", "B", "C", "D"], "amount": [0.0, 10.0, -2.0, 8.0],
        "region": ["North", "North", None, "South"], "event_date": ["2026-01-01", "bad", "2026-01-03", None],
    })
    profile = profile_dataframe(dataframe, "version", "run", "fingerprint", "2.0.0")
    by_name = {column.name: column for column in profile.columns}

    assert by_name["order_id"].likely_identifier
    assert by_name["amount"].numeric.negative_count == 1
    assert by_name["region"].null_count == 1
    assert by_name["region"].top_values[0].value == "North"
    assert by_name["event_date"].invalid_date_count == 1


def test_profile_persistence_idempotency_and_new_version(tmp_path: Path) -> None:
    """Same dataset/profile version reuses metadata; a version bump creates a new record."""
    version, url, _ = _prepared_version(tmp_path, "order_id,order_date,region,quantity,unit_price,revenue\nO1,2026-01-01,North,1,10,10\n")
    first = profile_dataset_version(version, url, "2.0.0")
    replay = profile_dataset_version(version, url, "2.0.0")
    next_version = profile_dataset_version(version, url, "2.1.0")

    assert first.profile_id == replay.profile_id
    assert first.profile_id != next_version.profile_id
    with Session(create_database_engine(url)) as session:
        assert len(session.scalars(select(DatasetProfileRecord)).all()) == 2


def test_rejected_and_quarantined_datasets_are_blocked(tmp_path: Path) -> None:
    """Fatal and suspicious validation outcomes cannot enter profiling."""
    rejected, rejected_url, _ = _prepared_version(tmp_path / "rejected", "order_id,order_date,region,quantity,unit_price,revenue\nO1,2026-01-01,North,1,10,9\n")
    quarantined, quarantine_url, _ = _prepared_version(tmp_path / "quarantined", "order_id,order_date,region,quantity,unit_price,revenue\nO1,2026-01-01,Moon,1,10,10\n")
    with pytest.raises(DatasetNotProfileableError):
        profile_dataset_version(rejected, rejected_url)
    with pytest.raises(DatasetNotProfileableError):
        profile_dataset_version(quarantined, quarantine_url)


class _GoodProvider:
    name = "mock"
    model_name = "mock-1"
    def generate_structured(self, metadata: dict[str, object]) -> list[str]:
        return ["Possible concentration in a category."]


class _BadProvider(_GoodProvider):
    def generate_structured(self, metadata: dict[str, object]) -> list[str]:
        return ["valid", 42]  # type: ignore[list-item]


class _BrokenProvider(_GoodProvider):
    def generate_structured(self, metadata: dict[str, object]) -> list[str]:
        raise TimeoutError("provider unavailable")


def test_optional_llm_enrichment_and_failure_fallbacks(tmp_path: Path) -> None:
    """Provider failures or malformed output never prevent deterministic profiling."""
    version, url, _ = _prepared_version(tmp_path, "order_id,order_date,region,quantity,unit_price,revenue\nO1,2026-01-01,North,1,10,10\n")
    good = profile_dataset_version(version, url, "good", _GoodProvider())
    bad = profile_dataset_version(version, url, "bad", _BadProvider())
    broken = profile_dataset_version(version, url, "broken", _BrokenProvider())

    assert good.inferred_findings == ["Possible concentration in a category."]
    assert bad.inferred_findings == []
    assert broken.inferred_findings == []


def test_schema_and_sensitive_metadata_sanitization() -> None:
    """The contract validates and sensitive categorical values do not reach providers."""
    profile = profile_dataframe(
        pd.DataFrame({"customer_email": ["person@example.com"], "value": [1]}),
        "v", "r", "f", "2.0.0",
    )
    assert DatasetProfile.model_validate(profile.model_dump()).profile_id == profile.profile_id
    metadata = sanitize_profile_metadata(profile)
    email_column = next(item for item in metadata["columns"] if item["name"] == "customer_email")
    assert email_column["top_values"] == []
