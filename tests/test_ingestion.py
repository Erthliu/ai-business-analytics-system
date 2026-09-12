"""Tests for CSV ingestion."""

import pandas as pd
import pytest

from pipeline.ingestion import load_csv


def test_load_csv_reads_valid_file(tmp_path) -> None:
    """A valid temporary CSV is loaded into a DataFrame."""
    csv_file = tmp_path / "sales.csv"
    csv_file.write_text("order_id,revenue\nORD-1,100.0\n", encoding="utf-8")

    result = load_csv(csv_file)

    assert list(result.columns) == ["order_id", "revenue"]
    assert result.iloc[0]["revenue"] == 100.0


def test_load_csv_raises_for_missing_file(tmp_path) -> None:
    """A missing CSV produces an explicit FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="CSV file was not found"):
        load_csv(tmp_path / "missing.csv")
