"""Authoritative semantic validation for structurally valid analysis plans."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Mapping

import pandas as pd

from analytics.metric_registry import MetricRegistry
from contracts.analysis_plan import AnalysisPlan, FilterOperator, OperationType
from contracts.dataset_profile import DatasetProfile


@dataclass(frozen=True, slots=True)
class PlanValidationIssue:
    code: str
    message: str


class PlanSemanticValidationError(ValueError):
    """Raised when a plan is structurally valid but unsafe or not executable."""

    def __init__(self, issues: list[PlanValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{item.code}: {item.message}" for item in issues))


_CODE_PATTERN = re.compile(
    r"(?:\bselect\b|\binsert\b|\bupdate\b|\bdelete\b|\bdrop\b|\bjoin\b|"
    r"\bimport\b|\bexec\b|\beval\b|__\w+__|\blambda\b|;|```|\$\(|\{|\})",
    re.IGNORECASE,
)
_NUMERIC_OPERATORS = {FilterOperator.GT, FilterOperator.GTE, FilterOperator.LT, FilterOperator.LTE}
_SUPPORTED_BUCKETS = {"day", "month", "quarter", "year"}
_MAX_LIMIT = 1000


def _kind(dtype: str, name: str) -> str:
    lowered = dtype.lower()
    if "datetime" in lowered or "date" in name.lower():
        return "date"
    if any(token in lowered for token in ("int", "float", "decimal", "number")):
        return "numeric"
    if any(token in lowered for token in ("object", "string", "category", "bool")):
        return "categorical"
    return "unsupported"


def _compatible(value: object, kind: str) -> bool:
    if kind == "numeric":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "date":
        try:
            pd.Timestamp(value)
            return True
        except (TypeError, ValueError):
            return False
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, (list, dict, tuple, set))


class PlanSemanticValidator:
    """Validate executability against a dataset schema and metric registry."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()

    def validate(
        self,
        plan: AnalysisPlan,
        profile: DatasetProfile | None = None,
        dataframe: pd.DataFrame | None = None,
    ) -> AnalysisPlan:
        """Return the same plan when valid, otherwise raise a domain error."""
        if profile is None and dataframe is None:
            raise ValueError("A DatasetProfile or DataFrame is required for semantic validation.")
        types: Mapping[str, str]
        if profile is not None:
            types = {column.name: column.dtype for column in profile.columns}
        else:
            assert dataframe is not None
            types = {name: str(dtype) for name, dtype in dataframe.dtypes.items()}
        issues: list[PlanValidationIssue] = []

        def reject(code: str, message: str) -> None:
            issues.append(PlanValidationIssue(code, message))

        if plan.metric_registry_version != self.registry.version:
            reject("REGISTRY_VERSION", f"Expected registry {self.registry.version}, got {plan.metric_registry_version}.")
        try:
            metric = self.registry.get(plan.primary_metric)
        except ValueError:
            metric = None
            reject("UNKNOWN_METRIC", f"Unknown metric: {plan.primary_metric}.")
        if metric:
            for field in metric.required_fields:
                if field not in types:
                    reject("MISSING_METRIC_FIELD", f"Metric field '{field}' is absent.")
            if metric.aggregation in {"SUM", "SUM_DIV_COUNT_DISTINCT"}:
                source = metric.source_fields[0]
                if source in types and _kind(types[source], source) != "numeric":
                    reject("METRIC_TYPE", f"Metric field '{source}' must be numeric.")

        if len(plan.dimensions) != len(set(plan.dimensions)):
            reject("DUPLICATE_DIMENSION", "Dimensions must be unique.")
        for dimension in plan.dimensions:
            if dimension not in types:
                reject("UNKNOWN_DIMENSION", f"Dimension '{dimension}' is absent.")
            elif _kind(types[dimension], dimension) == "unsupported":
                reject("UNGROUPABLE_DIMENSION", f"Dimension '{dimension}' is not groupable.")

        strings: list[str] = [plan.primary_metric, plan.time_field, *(plan.dimensions), *(plan.secondary_metrics), *(plan.assumptions)]
        for item in plan.filters:
            strings.extend([item.field, str(item.value)])
            if item.field not in types:
                reject("UNKNOWN_FILTER_FIELD", f"Filter field '{item.field}' is absent.")
                continue
            kind = _kind(types[item.field], item.field)
            values = item.value
            if item.operator == FilterOperator.IN:
                if not isinstance(values, (list, tuple)) or not values:
                    reject("INVALID_IN", "IN requires a non-empty collection.")
                elif not all(_compatible(value, kind) for value in values):
                    reject("FILTER_TYPE", f"IN values are incompatible with '{item.field}'.")
            elif item.operator == FilterOperator.BETWEEN:
                if not isinstance(values, (list, tuple)) or len(values) != 2:
                    reject("INVALID_BETWEEN", "BETWEEN requires exactly two values.")
                elif not all(_compatible(value, kind) for value in values):
                    reject("FILTER_TYPE", f"BETWEEN values are incompatible with '{item.field}'.")
            elif item.operator in _NUMERIC_OPERATORS and kind not in {"numeric", "date"}:
                reject("FILTER_OPERATOR", f"{item.operator} requires a numeric or date-compatible field.")
            elif not _compatible(values, kind):
                reject("FILTER_TYPE", f"Filter value is incompatible with '{item.field}'.")

        for value in strings:
            if _CODE_PATTERN.search(value):
                reject("EXECUTABLE_CONTENT", "SQL, code, expressions, joins, and arbitrary functions are forbidden.")
                break

        operation_types = [operation.type for operation in plan.operations]
        operation_set = set(operation_types)
        if len(operation_types) != len(operation_set):
            reject("DUPLICATE_OPERATION", "Operation types must be unique.")
        if not operation_types or OperationType.AGGREGATE not in operation_set:
            reject("MISSING_AGGREGATION", "Every plan requires an aggregation operation.")
        if OperationType.GROUP_BY in operation_set and not plan.dimensions:
            reject("GROUPING", "GROUP_BY requires at least one dimension.")
        if plan.dimensions and OperationType.GROUP_BY not in operation_set:
            reject("GROUPING", "Dimensions require GROUP_BY.")
        if OperationType.GROUPED_PERIOD_COMPARE in operation_set and not plan.dimensions:
            reject("GROUPED_COMPARISON", "Grouped comparison requires a dimension.")
        comparison = bool(operation_set & {OperationType.PERIOD_COMPARE, OperationType.GROUPED_PERIOD_COMPARE})
        if {OperationType.PERIOD_COMPARE, OperationType.GROUPED_PERIOD_COMPARE} <= operation_set:
            reject("COMPARISON_COMBINATION", "Scalar and grouped comparison cannot be combined.")
        if comparison and (plan.analysis_period is None or plan.comparison_period is None):
            reject("COMPARISON_PERIOD", "Comparison requires both analysis and comparison periods.")
        if not comparison and plan.comparison_period is not None:
            reject("COMPARISON_OPERATION", "A comparison period requires a comparison operation.")
        if OperationType.TREND in operation_set:
            if not plan.time_bucket:
                reject("TREND_BUCKET", "TREND requires a time bucket.")
            if plan.time_field not in types or _kind(types.get(plan.time_field, ""), plan.time_field) != "date":
                reject("TREND_DATE", f"TREND requires date-compatible field '{plan.time_field}'.")
            if comparison or OperationType.RANK in operation_set:
                reject("TREND_COMBINATION", "TREND cannot be combined with comparison or ranking.")
        if plan.time_bucket and plan.time_bucket not in _SUPPORTED_BUCKETS:
            reject("TIME_BUCKET", f"Unsupported time bucket: {plan.time_bucket}.")
        if plan.time_bucket and OperationType.TREND not in operation_set:
            reject("TIME_OPERATION", "A time bucket requires TREND.")
        if (plan.analysis_period or plan.comparison_period or plan.time_bucket) and plan.time_field not in types:
            reject("TIME_FIELD", f"Time field '{plan.time_field}' is absent.")
        for name, period in (("analysis", plan.analysis_period), ("comparison", plan.comparison_period)):
            if period and period.start > period.end:
                reject("PERIOD_ORDER", f"{name.title()} period start must be on or before end.")

        ranking = OperationType.RANK in operation_set
        if ranking:
            if not plan.dimensions or OperationType.GROUP_BY not in operation_set:
                reject("RANK_GROUPING", "Ranking requires grouped aggregation.")
            if plan.limit is None:
                reject("RANK_LIMIT", "Ranking requires a positive limit.")
            if plan.sort != plan.primary_metric:
                reject("RANK_TARGET", "Ranking must sort by the computed primary metric.")
            rank_index = operation_types.index(OperationType.RANK)
            aggregate_index = operation_types.index(OperationType.AGGREGATE) if OperationType.AGGREGATE in operation_set else rank_index + 1
            group_index = operation_types.index(OperationType.GROUP_BY) if OperationType.GROUP_BY in operation_set else rank_index + 1
            if rank_index < aggregate_index or rank_index < group_index:
                reject("RANK_ORDER", "Ranking must occur after aggregation and grouping.")
        if plan.limit is not None and (plan.limit <= 0 or plan.limit > _MAX_LIMIT):
            reject("INVALID_LIMIT", f"Limit must be between 1 and {_MAX_LIMIT}.")
        if plan.limit is not None and not ranking:
            reject("RANK_OPERATION", "A limit requires an explicit RANK operation.")
        if plan.sort is not None and (not plan.dimensions or plan.sort != plan.primary_metric):
            reject("SORT_TARGET", "Sorting requires grouped output and the computed primary metric.")
        if OperationType.FILTER in operation_set and not plan.filters:
            reject("FILTER_OPERATION", "FILTER requires at least one typed filter.")
        if OperationType.COUNT_DISTINCT in operation_set and (metric is None or metric.aggregation != "COUNT_DISTINCT"):
            reject("COUNT_DISTINCT_OPERATION", "COUNT_DISTINCT is valid only for a matching registry metric.")

        if issues:
            raise PlanSemanticValidationError(issues)
        return plan
