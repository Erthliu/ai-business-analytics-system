"""Authoritative pandas analytics with no executable expressions."""
from __future__ import annotations

import hashlib
import math
from typing import Any

import pandas as pd

from analytics.metric_registry import MetricRegistry
from analytics.plan_validator import PlanSemanticValidator
from contracts.analysis_plan import AnalysisPlan, FilterOperator
from contracts.computed_analysis import ComputedAnalysis, ResultType


class AnalyticsEngine:
    """Execute only plans accepted by the shared semantic validator."""

    engine_version = "4.0.0"

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()
        self.validator = PlanSemanticValidator(self.registry)

    def _filter(self, frame: pd.DataFrame, filters: list[Any]) -> pd.DataFrame:
        for item in filters:
            series, value = frame[item.field], item.value
            if "date" in item.field.lower():
                series = pd.to_datetime(series, errors="raise")
                value = [pd.Timestamp(v) for v in value] if item.operator in {FilterOperator.BETWEEN, FilterOperator.IN} else pd.Timestamp(value)
            if item.operator == FilterOperator.EQ: mask = series == value
            elif item.operator == FilterOperator.NE: mask = series != value
            elif item.operator == FilterOperator.GT: mask = series > value
            elif item.operator == FilterOperator.GTE: mask = series >= value
            elif item.operator == FilterOperator.LT: mask = series < value
            elif item.operator == FilterOperator.LTE: mask = series <= value
            elif item.operator == FilterOperator.IN: mask = series.isin(value)
            else: mask = series.between(value[0], value[1])
            frame = frame[mask]
        return frame

    def _metric(self, frame: pd.DataFrame, name: str) -> float | int | None:
        definition = self.registry.get(name)
        if frame.empty:
            return None
        if definition.aggregation == "COUNT_DISTINCT":
            return int(frame[definition.source_fields[0]].nunique())
        numeric = pd.to_numeric(frame[definition.source_fields[0]], errors="raise").dropna()
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("Metric source contains infinity or non-finite values.")
        if numeric.empty:
            return None
        if definition.aggregation == "SUM":
            return float(numeric.sum())
        if definition.aggregation == "SUM_DIV_COUNT_DISTINCT":
            count = frame[definition.source_fields[1]].nunique()
            return None if count == 0 else float(numeric.sum() / count)
        raise ValueError("Unsupported metric definition.")

    @staticmethod
    def _dimension(value: object) -> object:
        """Represent missing group keys as JSON-safe nulls."""
        return None if pd.isna(value) else value

    @staticmethod
    def _changes(analysis: float | int | None, comparison: float | int | None) -> tuple[float | None, float | None]:
        if analysis is None or comparison is None:
            return None, None
        change = float(analysis - comparison)
        if comparison == 0:
            return change, 0.0 if analysis == 0 else None
        return change, float(change / comparison * 100)

    def execute(self, dataframe: pd.DataFrame, plan: AnalysisPlan, source_hash: str) -> ComputedAnalysis:
        """Validate then execute a plan without mutating its source DataFrame."""
        self.validator.validate(plan, dataframe=dataframe)
        frame = self._filter(dataframe.copy(deep=False), plan.filters)
        metric, warnings = plan.primary_metric, []
        if plan.analysis_period and plan.comparison_period is None:
            dates = pd.to_datetime(frame[plan.time_field], errors="raise")
            frame = frame[(dates.dt.date >= plan.analysis_period.start) & (dates.dt.date <= plan.analysis_period.end)]
        if frame.empty:
            warnings.append("No rows matched the selected filters or period; values are null.")
        if plan.analysis_period and plan.comparison_period:
            dates = pd.to_datetime(frame[plan.time_field], errors="raise")
            subset = lambda period: frame[(dates.dt.date >= period.start) & (dates.dt.date <= period.end)]
            if plan.dimensions:
                def grouped(period):
                    return {keys if isinstance(keys, tuple) else (keys,): self._metric(group, metric)
                            for keys, group in subset(period).groupby(plan.dimensions, dropna=False)}
                analysis_values, comparison_values = grouped(plan.analysis_period), grouped(plan.comparison_period)
                rows = []
                for keys in sorted(set(analysis_values) | set(comparison_values), key=str):
                    av, bv = analysis_values.get(keys), comparison_values.get(keys)
                    change, percentage = self._changes(av, bv)
                    rows.append({**dict(zip(plan.dimensions, map(self._dimension, keys))), "analysis_period_value": av,
                                 "comparison_period_value": bv, "absolute_change": change,
                                 "percentage_change": percentage})
                kind = ResultType.GROUPED_PERIOD_COMPARISON
            else:
                av, bv = self._metric(subset(plan.analysis_period), metric), self._metric(subset(plan.comparison_period), metric)
                change, percentage = self._changes(av, bv)
                rows = [{"analysis_period_value": av, "comparison_period_value": bv,
                         "absolute_change": change, "percentage_change": percentage}]
                kind = ResultType.PERIOD_COMPARISON
            if any(row.get("percentage_change") is None for row in rows):
                warnings.append("Percentage change is undefined for a zero, null, or absent comparison value.")
        elif plan.time_bucket:
            dates = pd.to_datetime(frame[plan.time_field], errors="raise")
            frequency = {"day": "D", "month": "M", "quarter": "Q", "year": "Y"}[plan.time_bucket]
            frame = frame.assign(period=dates.dt.to_period(frequency).astype(str))
            rows = [{"period": period, "value": self._metric(group, metric)} for period, group in frame.groupby("period", sort=True)]
            kind = ResultType.TIME_SERIES
        elif plan.dimensions:
            rows = []
            for keys, group in frame.groupby(plan.dimensions, dropna=False):
                values = keys if isinstance(keys, tuple) else (keys,)
                rows.append({**dict(zip(plan.dimensions, map(self._dimension, values))), metric: self._metric(group, metric)})
            if plan.sort:
                rows.sort(key=lambda row: (row[plan.sort] is not None, row[plan.sort]), reverse=plan.sort_descending)
            if plan.limit:
                rows = rows[:plan.limit]
            kind = ResultType.RANKING if plan.limit else ResultType.TABLE
        else:
            rows = [{metric: self._metric(frame, metric)}]
            kind = ResultType.SCALAR
        plan_hash = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
        return ComputedAnalysis(
            request_id=plan.request_id, plan_id=plan.plan_id, dataset_version_id=plan.dataset_version_id,
            profile_id=plan.profile_id, primary_metric=metric, result_type=kind,
            analysis_period=plan.analysis_period, comparison_period=plan.comparison_period,
            columns=list(rows[0]) if rows else [], rows=rows,
            summary_metrics={metric: self._metric(frame, metric)}, warnings=warnings, row_count=len(rows),
            source_dataset_hash=source_hash, plan_hash=plan_hash, engine_version=self.engine_version,
        )
