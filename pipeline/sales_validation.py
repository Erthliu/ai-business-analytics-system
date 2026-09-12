"""Sales-specific policies, intentionally separate from generic validation."""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from pipeline.validation import ValidationResult


@dataclass(frozen=True, slots=True)
class SalesValidationPolicy:
    """Rules for the synthetic sales domain."""

    accepted_regions: frozenset[str] = frozenset({"North", "South", "East", "West"})
    earliest_order_date: date = date(2020, 1, 1)
    latest_order_date: date = date(2100, 1, 1)
    revenue_tolerance: float = 0.01
    quarantine_unknown_regions: bool = True


def validate_sales_dataframe(dataframe: pd.DataFrame, result: ValidationResult, policy: SalesValidationPolicy = SalesValidationPolicy()) -> ValidationResult:
    """Apply positive-value, date, region, and revenue-arithmetic rules."""
    required = {"order_date", "region", "quantity", "unit_price", "revenue"}
    if not required.issubset(dataframe.columns):
        return result
    unknown = set(dataframe["region"].dropna()) - policy.accepted_regions
    if unknown:
        message = f"Unknown region value(s): {', '.join(sorted(unknown))}."
        (result.quarantine if policy.quarantine_unknown_regions else result.reject)(message)
    dates = pd.to_datetime(dataframe["order_date"], errors="coerce")
    outside = int(((dates.dt.date < policy.earliest_order_date) | (dates.dt.date > policy.latest_order_date)).sum())
    if outside:
        result.reject(f"Order date is outside the accepted range in {outside} row(s).")
    for name in ("quantity", "unit_price", "revenue"):
        if pd.api.types.is_numeric_dtype(dataframe[name]):
            count = int((dataframe[name] <= 0).sum())
            if count:
                result.reject(f"Column '{name}' must be positive in {count} row(s).")
    if all(pd.api.types.is_numeric_dtype(dataframe[name]) for name in ("quantity", "unit_price", "revenue")):
        expected = dataframe["quantity"] * dataframe["unit_price"]
        count = int((dataframe["revenue"].sub(expected).abs() > policy.revenue_tolerance).sum())
        if count:
            result.reject(f"Revenue differs from quantity × unit_price by more than {policy.revenue_tolerance:.2f} in {count} row(s).")
    return result
