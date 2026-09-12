"""Extensible generic DataFrame validation with explicit outcomes."""

from dataclasses import dataclass, field
from enum import StrEnum
import logging
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

logger = logging.getLogger(__name__)


class ValidationOutcome(StrEnum):
    """Disposition determining whether data may enter downstream analytics."""

    PASS = "PASS"
    WARN = "WARN"
    REJECT = "REJECT"
    QUARANTINE = "QUARANTINE"


@dataclass(slots=True)
class ValidationResult:
    """Machine-readable validation outcome and evidence."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quarantine_reasons: list[str] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> ValidationOutcome:
        """Return the strictest disposition represented by the issues."""
        if self.errors:
            return ValidationOutcome.REJECT
        if self.quarantine_reasons:
            return ValidationOutcome.QUARANTINE
        if self.warnings:
            return ValidationOutcome.WARN
        return ValidationOutcome.PASS

    @property
    def passed(self) -> bool:
        """Retain V1.1 compatibility: only PASS and WARN may proceed."""
        return self.outcome in {ValidationOutcome.PASS, ValidationOutcome.WARN}

    def reject(self, message: str) -> None:
        """Add a fatal integrity failure."""
        self.errors.append(message)

    def warn(self, message: str) -> None:
        """Add a non-blocking quality warning."""
        self.warnings.append(message)

    def quarantine(self, message: str) -> None:
        """Block analytics while retaining data for review."""
        self.quarantine_reasons.append(message)


def validate_dataframe(
    dataframe: pd.DataFrame,
    required_columns: Iterable[str] = (),
    numeric_columns: Iterable[str] = (),
    unique_columns: Iterable[str] = (),
    date_columns: Iterable[str] = (),
    numeric_ranges: Mapping[str, tuple[float | None, float | None]] | None = None,
    max_null_percentage: float = 0.0,
) -> ValidationResult:
    """Run generic checks independent of any business domain."""
    if not 0.0 <= max_null_percentage <= 100.0:
        raise ValueError("max_null_percentage must be between 0 and 100")
    result = ValidationResult()
    missing = sorted(set(required_columns) - set(dataframe.columns))
    if dataframe.empty:
        result.reject("Dataframe is empty.")
    if missing:
        result.reject(f"Missing required columns: {', '.join(missing)}.")
    duplicate_rows = int(dataframe.duplicated().sum())
    if duplicate_rows:
        result.warn(f"Detected {duplicate_rows} duplicate row(s).")
    null_percentages = {name: round(float(value), 2) for name, value in (dataframe.isna().mean() * 100).items()}
    for name, percentage in null_percentages.items():
        if percentage > max_null_percentage:
            result.warn(f"Column '{name}' has {percentage:.2f}% null values, above the {max_null_percentage:.2f}% threshold.")
    invalid_numeric: list[str] = []
    for name in numeric_columns:
        if name not in dataframe.columns:
            result.reject(f"Expected numeric column is missing: {name}.")
        elif not is_numeric_dtype(dataframe[name]):
            invalid_numeric.append(name)
    if invalid_numeric:
        result.reject(f"Expected numeric column(s) have non-numeric values or dtype: {', '.join(invalid_numeric)}.")
    range_violations: dict[str, int] = {}
    for name, (minimum, maximum) in (numeric_ranges or {}).items():
        if name not in dataframe.columns or not is_numeric_dtype(dataframe[name]):
            continue
        values = dataframe[name].dropna()
        invalid = ~np.isfinite(values)
        if minimum is not None:
            invalid |= values < minimum
        if maximum is not None:
            invalid |= values > maximum
        count = int(invalid.sum())
        if count:
            range_violations[name] = count
            result.reject(f"Column '{name}' has {count} out-of-range or non-finite value(s).")
    duplicate_keys: dict[str, int] = {}
    for name in unique_columns:
        if name not in dataframe.columns:
            result.reject(f"Expected unique column is missing: {name}.")
            continue
        count = int(dataframe[name].duplicated(keep=False).sum())
        if count:
            duplicate_keys[name] = count
            result.reject(f"Column '{name}' has {count} duplicate key value(s).")
    invalid_dates: dict[str, int] = {}
    for name in date_columns:
        if name not in dataframe.columns:
            result.reject(f"Expected date column is missing: {name}.")
            continue
        count = int(pd.to_datetime(dataframe[name].dropna(), errors="coerce").isna().sum())
        if count:
            invalid_dates[name] = count
            result.reject(f"Column '{name}' has {count} invalid date value(s).")
    result.statistics = {
        "row_count": len(dataframe), "column_count": len(dataframe.columns),
        "duplicate_rows": duplicate_rows, "null_percentage_by_column": null_percentages,
        "invalid_numeric_columns": invalid_numeric, "numeric_range_violations": range_violations,
        "duplicate_key_counts": duplicate_keys, "invalid_date_counts": invalid_dates,
    }
    logger.info(
        "event=validation_completed status=%s row_count=%d errors=%d warnings=%d quarantines=%d",
        result.outcome,
        len(dataframe),
        len(result.errors),
        len(result.warnings),
        len(result.quarantine_reasons),
    )
    return result
