"""Tests for generic DataFrame validation."""

import pandas as pd

from pipeline.validation import validate_dataframe


def test_empty_dataframe_fails_validation() -> None:
    """An empty DataFrame is invalid."""
    result = validate_dataframe(pd.DataFrame())

    assert not result.passed
    assert "Dataframe is empty." in result.errors


def test_missing_required_columns_fails_validation() -> None:
    """Missing required columns are reported as errors."""
    result = validate_dataframe(pd.DataFrame({"order_id": ["ORD-1"]}), ["order_id", "revenue"])

    assert not result.passed
    assert "Missing required columns: revenue." in result.errors


def test_duplicate_rows_are_reported() -> None:
    """Duplicate rows are returned as a structured warning and statistic."""
    dataframe = pd.DataFrame({"order_id": ["ORD-1", "ORD-1"], "revenue": [10.0, 10.0]})

    result = validate_dataframe(dataframe)

    assert result.passed
    assert result.statistics["duplicate_rows"] == 1
    assert "Detected 1 duplicate row(s)." in result.warnings


def test_null_percentage_is_reported() -> None:
    """Null values over the threshold are exposed in warnings and statistics."""
    dataframe = pd.DataFrame({"order_id": ["ORD-1", None], "revenue": [10.0, 20.0]})

    result = validate_dataframe(dataframe)

    assert result.passed
    assert result.statistics["null_percentage_by_column"]["order_id"] == 50.0
    assert any("order_id" in warning and "50.00%" in warning for warning in result.warnings)


def test_non_numeric_requested_column_fails_validation() -> None:
    """Requested numeric columns must have numeric dtypes."""
    result = validate_dataframe(pd.DataFrame({"revenue": ["unknown"]}), numeric_columns=["revenue"])

    assert not result.passed
    assert result.statistics["invalid_numeric_columns"] == ["revenue"]


def test_duplicate_business_key_fails_validation() -> None:
    """Duplicate identifiers are blocking even when rows differ."""
    dataframe = pd.DataFrame({"order_id": ["ORD-1", "ORD-1"], "revenue": [10.0, 20.0]})

    result = validate_dataframe(dataframe, unique_columns=["order_id"])

    assert not result.passed
    assert result.statistics["duplicate_key_counts"] == {"order_id": 2}


def test_invalid_date_and_negative_numeric_values_fail_validation() -> None:
    """Date parsing and configured numeric bounds reject invalid values."""
    dataframe = pd.DataFrame({"order_date": ["not-a-date"], "revenue": [-1.0]})

    result = validate_dataframe(
        dataframe,
        date_columns=["order_date"],
        numeric_ranges={"revenue": (0, None)},
    )

    assert not result.passed
    assert result.statistics["invalid_date_counts"] == {"order_date": 1}
    assert result.statistics["numeric_range_violations"] == {"revenue": 1}
