"""Semantic safety, provider, and deterministic operation tests for V4."""
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import ValidationError

from agents.analytics_engine import AnalyticsEngine
from agents.analytics_planner import AnalyticsPlanner, PlannerProviderError
from analytics.metric_registry import MetricRegistry
from analytics.plan_validator import PlanSemanticValidationError, PlanSemanticValidator
from contracts.analysis_plan import AnalysisPlan, FilterExpression, FilterOperator, Operation, OperationType
from contracts.analysis_request import AnalysisRequest, DateRange, RequestStatus
from contracts.dataset_profile import ColumnProfile, DatasetProfile, NumericStatistics
from contracts.planner_candidate import PlannerCandidate


def profile() -> DatasetProfile:
    numeric = lambda: NumericStatistics(zero_count=0, negative_count=0)
    columns = [
        ColumnProfile(name="revenue", dtype="float64", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=1, numeric=numeric()),
        ColumnProfile(name="quantity", dtype="int64", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=1, numeric=numeric()),
        ColumnProfile(name="order_id", dtype="object", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=1),
        ColumnProfile(name="customer_id", dtype="object", null_count=0, null_percentage=0, unique_count=3, uniqueness_ratio=.75),
        ColumnProfile(name="region", dtype="object", null_count=0, null_percentage=0, unique_count=2, uniqueness_ratio=.5),
        ColumnProfile(name="product_id", dtype="object", null_count=0, null_percentage=0, unique_count=3, uniqueness_ratio=.75),
        ColumnProfile(name="order_date", dtype="object", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=1, minimum_date="2024-01-01", maximum_date="2024-08-31"),
    ]
    return DatasetProfile(profile_id="p", dataset_version_id="v", pipeline_run_id="run", row_count=4,
                          column_count=len(columns), schema_fingerprint="schema", profiler_version="2", columns=columns)


def frame() -> pd.DataFrame:
    return pd.DataFrame({
        "revenue": [0.0, 10.0, 30.0, 20.0], "quantity": [1, 2, 3, 4],
        "order_id": ["o1", "o2", "o3", "o4"], "customer_id": ["c1", "c1", "c2", "c3"],
        "region": ["N", "N", "S", "S"], "product_id": ["p1", "p2", "p1", "p3"],
        "order_date": ["2024-01-01", "2024-07-01", "2024-08-01", "2024-08-02"],
    })


def period(start: str, end: str) -> DateRange:
    return DateRange(start=date.fromisoformat(start), end=date.fromisoformat(end), source="test")


def plan(metric: str = "revenue", **changes) -> AnalysisPlan:
    values = dict(request_id="r", dataset_version_id="v", profile_id="p", primary_metric=metric,
                  operations=[Operation(type=OperationType.AGGREGATE, field=metric)])
    values.update(changes)
    if values.get("filters") and not any(item.type == OperationType.FILTER for item in values["operations"]):
        values["operations"] = [*values["operations"], Operation(type=OperationType.FILTER)]
    return AnalysisPlan(**values)


@pytest.mark.parametrize("candidate", [
    plan(),
    plan(dimensions=["region"], operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY")]),
    plan(analysis_period=period("2024-08-01", "2024-08-31"), comparison_period=period("2024-07-01", "2024-07-31"), operations=[Operation(type="AGGREGATE"), Operation(type="PERIOD_COMPARE")]),
    plan(dimensions=["region"], analysis_period=period("2024-08-01", "2024-08-31"), comparison_period=period("2024-07-01", "2024-07-31"), operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="GROUPED_PERIOD_COMPARE")]),
    plan(time_bucket="month", operations=[Operation(type="AGGREGATE"), Operation(type="TREND")]),
    plan(dimensions=["product_id"], limit=2, sort="revenue", operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="RANK")]),
])
def test_valid_plan_shapes(candidate):
    assert PlanSemanticValidator().validate(candidate, profile=profile()) is candidate


@pytest.mark.parametrize(("candidate", "code"), [
    (plan("profit"), "UNKNOWN_METRIC"),
    (plan(dimensions=["country"], operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY")]), "UNKNOWN_DIMENSION"),
    (plan(filters=[FilterExpression(field="country", operator="EQ", value="US")]), "UNKNOWN_FILTER_FIELD"),
    (plan(filters=[FilterExpression(field="region", operator="GT", value="N")]), "FILTER_OPERATOR"),
    (plan(filters=[FilterExpression(field="quantity", operator="BETWEEN", value=[1])]), "INVALID_BETWEEN"),
    (plan(filters=[FilterExpression(field="region", operator="IN", value=[])]), "INVALID_IN"),
    (plan(analysis_period=period("2024-08-31", "2024-08-01")), "PERIOD_ORDER"),
    (plan(operations=[Operation(type="AGGREGATE"), Operation(type="PERIOD_COMPARE")], analysis_period=period("2024-08-01", "2024-08-31")), "COMPARISON_PERIOD"),
    (plan(time_bucket="week", operations=[Operation(type="AGGREGATE"), Operation(type="TREND")]), "TIME_BUCKET"),
    (plan(limit=3, sort="revenue"), "RANK_OPERATION"),
    (plan(assumptions=["SELECT * FROM sales"]), "EXECUTABLE_CONTENT"),
    (plan(assumptions=["import os"]), "EXECUTABLE_CONTENT"),
])
def test_invalid_plan_shapes(candidate, code):
    with pytest.raises(PlanSemanticValidationError) as caught:
        PlanSemanticValidator().validate(candidate, profile=profile())
    assert code in {issue.code for issue in caught.value.issues}


def test_missing_and_wrong_metric_fields():
    missing = profile().model_copy(update={"columns": [column for column in profile().columns if column.name != "revenue"]})
    with pytest.raises(PlanSemanticValidationError, match="MISSING_METRIC_FIELD"):
        PlanSemanticValidator().validate(plan(), profile=missing)
    wrong = profile()
    wrong.columns[0].dtype = "object"
    with pytest.raises(PlanSemanticValidationError, match="METRIC_TYPE"):
        PlanSemanticValidator().validate(plan(), profile=wrong)


@pytest.mark.parametrize(("operator", "value", "expected"), [
    ("EQ", "N", 10), ("NE", "N", 50), ("GT", 1, 60), ("GTE", 3, 50),
    ("LT", 3, 10), ("LTE", 2, 10), ("IN", ["S"], 50), ("BETWEEN", [2, 3], 40),
])
def test_all_filter_operators(operator, value, expected):
    field = "region" if operator in {"EQ", "NE", "IN"} else "quantity"
    filtered = plan(filters=[FilterExpression(field=field, operator=operator, value=value)])
    assert AnalyticsEngine().execute(frame(), filtered, "hash").rows[0]["revenue"] == expected


def test_date_between_and_multiple_grouping():
    filtered = plan(filters=[FilterExpression(field="order_date", operator="BETWEEN", value=["2024-08-01", "2024-08-31"])])
    assert AnalyticsEngine().execute(frame(), filtered, "hash").rows[0]["revenue"] == 50
    grouped = plan(dimensions=["region", "product_id"], operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY")])
    assert AnalyticsEngine().execute(frame(), grouped, "hash").row_count == 4


def test_registry_metrics_and_unknown():
    engine = AnalyticsEngine()
    expected = {"revenue": 60, "units_sold": 10, "order_count": 4, "customer_count": 3, "average_order_value": 15}
    for metric, value in expected.items():
        assert engine.execute(frame(), plan(metric), "hash").rows[0][metric] == value
    with pytest.raises(ValueError, match="Unknown metric"):
        MetricRegistry().get("profit")


def test_comparison_zero_policies_and_outer_groups():
    compared = plan(analysis_period=period("2024-08-01", "2024-08-31"), comparison_period=period("2024-01-01", "2024-01-31"), operations=[Operation(type="AGGREGATE"), Operation(type="PERIOD_COMPARE")])
    row = AnalyticsEngine().execute(frame(), compared, "hash").rows[0]
    assert row["absolute_change"] == 50 and row["percentage_change"] is None
    grouped = compared.model_copy(update={"dimensions": ["region"], "operations": [Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="GROUPED_PERIOD_COMPARE")]})
    rows = AnalyticsEngine().execute(frame(), grouped, "hash").rows
    assert any(row["analysis_period_value"] is None for row in rows)
    assert any(row["comparison_period_value"] is None for row in rows)


@pytest.mark.parametrize(("analysis", "comparison", "change", "percentage"), [
    (20, 10, 10, 100), (10, 20, -10, -50), (0, 0, 0, 0), (10, 0, 10, None),
])
def test_percentage_change_policies(analysis, comparison, change, percentage):
    assert AnalyticsEngine._changes(analysis, comparison) == (change, percentage)


def test_empty_filter_and_non_finite_policies():
    empty = plan(filters=[FilterExpression(field="region", operator="EQ", value="missing")])
    result = AnalyticsEngine().execute(frame(), empty, "hash")
    assert result.rows[0]["revenue"] is None and result.warnings
    invalid = frame(); invalid.loc[0, "revenue"] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        AnalyticsEngine().execute(invalid, plan(), "hash")


def test_trend_requires_date_and_rank_requires_aggregation():
    without_date = profile().model_copy(update={"columns": [column for column in profile().columns if column.name != "order_date"]})
    with pytest.raises(PlanSemanticValidationError, match="TREND_DATE"):
        PlanSemanticValidator().validate(plan(time_bucket="month", operations=[Operation(type="AGGREGATE"), Operation(type="TREND")]), profile=without_date)
    with pytest.raises(PlanSemanticValidationError, match="MISSING_AGGREGATION"):
        PlanSemanticValidator().validate(plan(dimensions=["region"], limit=2, sort="revenue", operations=[Operation(type="GROUP_BY"), Operation(type="RANK")]), profile=profile())
    with pytest.raises(PlanSemanticValidationError, match="INVALID_LIMIT"):
        PlanSemanticValidator().validate(plan(dimensions=["region"], limit=0, sort="revenue", operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="RANK")]), profile=profile())


@pytest.mark.parametrize("bucket", ["day", "month", "quarter", "year"])
def test_trends_are_chronological(bucket):
    trended = plan(time_bucket=bucket, operations=[Operation(type="AGGREGATE"), Operation(type="TREND")])
    rows = AnalyticsEngine().execute(frame(), trended, "hash").rows
    assert [row["period"] for row in rows] == sorted(row["period"] for row in rows)


def test_top_and_bottom_ranking():
    base = dict(dimensions=["product_id"], limit=2, sort="revenue", operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="RANK")])
    assert AnalyticsEngine().execute(frame(), plan(**base), "hash").rows[0]["product_id"] == "p1"
    assert AnalyticsEngine().execute(frame(), plan(**base, sort_descending=False), "hash").rows[0]["product_id"] == "p2"


class Provider:
    name = "mock"
    model_name = "mock-1"
    def __init__(self, payload): self.payload, self.calls = payload, 0
    def generate_structured(self, metadata): self.calls += 1; return self.payload


def ready(question="revenue"):
    return AnalysisRequest(request_id="r", original_question=question, primary_metric="revenue", dataset_version_id="v",
                           profile_id="p", status=RequestStatus.READY, agent_version="3", prompt_version="r1")


def test_deterministic_planner_does_not_call_provider():
    provider = Provider({})
    AnalyticsPlanner(provider=provider).build(ready(), profile())
    assert provider.calls == 0


def test_valid_provider_candidate_and_safe_failures():
    payload = {"primary_metric": "revenue", "operation_types": ["AGGREGATE"]}
    result = AnalyticsPlanner(provider=Provider(payload)).build(ready(), profile(), use_provider=True)
    assert result.provider_name == "mock"
    for bad in (
        {"primary_metric": "profit", "operation_types": ["AGGREGATE"]},
        {"primary_metric": "revenue", "dimensions": ["country"], "operation_types": ["AGGREGATE", "GROUP_BY"]},
        {"primary_metric": "revenue", "operation_types": ["SQL"]},
        {"primary_metric": "revenue", "operation_types": ["AGGREGATE"], "sql": "SELECT 1"},
        "malformed",
    ):
        with pytest.raises(PlannerProviderError):
            AnalyticsPlanner(provider=Provider(bad)).build(ready(), profile(), use_provider=True)
    with pytest.raises(PlannerProviderError):
        AnalyticsPlanner().build(ready(), profile(), use_provider=True)


def test_provider_exception_and_contract_schema():
    provider = Mock(name="provider")
    provider.name, provider.model_name = "mock", "mock-1"
    provider.generate_structured.side_effect = TimeoutError()
    with pytest.raises(PlannerProviderError):
        AnalyticsPlanner(provider=provider).build(ready(), profile(), use_provider=True)
    schema = PlannerCandidate.model_json_schema()
    assert schema["additionalProperties"] is False
    assert AnalysisPlan.model_json_schema()["additionalProperties"] is False
