"""V5 contribution, safety, concentration, and provider tests."""
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import ValidationError

from agents.analytics_engine import AnalyticsEngine
from agents.investigation_agent import InvestigationAgent, InvestigationProviderError
from analytics.metric_registry import MetricAdditivity, MetricRegistry
from contracts.analysis_plan import AnalysisPlan, FilterExpression, Operation, OperationType
from contracts.analysis_request import AnalysisRequest, DateRange, RequestStatus
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import ColumnProfile, DatasetProfile, NumericStatistics
from contracts.investigation import InvestigationCandidate, InvestigationPlan, InvestigationResult, InvestigationType, MaterialityThresholds
from investigation.engine import InvestigationEngine, InvestigationExecutionError
from investigation.validator import InvestigationSemanticValidator, InvestigationValidationError


def period(start, end):
    return DateRange(start=date.fromisoformat(start), end=date.fromisoformat(end), source="test")


def data():
    return pd.DataFrame({
        "order_id": ["j1", "j2", "j3", "a1", "a2", "a3"],
        "customer_id": ["c1", "c2", "c3", "c4", "c5", "c6"],
        "order_date": ["2024-07-01", "2024-07-02", "2024-07-03", "2024-08-01", "2024-08-02", "2024-08-03"],
        "region": ["North", "South", "Old", "North", "South", "Bangkok"],
        "product_id": ["p1", "p2", "p3", "p1", "p2", "p4"],
        "quantity": [5, 5, 2, 1, 3, 1], "revenue": [50., 50., 20., 10., 30., 10.],
    })


def profile():
    numeric = NumericStatistics(zero_count=0, negative_count=0)
    columns = [
        ColumnProfile(name="revenue", dtype="float64", null_count=0, null_percentage=0, unique_count=5, uniqueness_ratio=.83, numeric=numeric),
        ColumnProfile(name="quantity", dtype="int64", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=.67, numeric=numeric),
        ColumnProfile(name="region", dtype="object", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=.67),
        ColumnProfile(name="product_id", dtype="object", null_count=0, null_percentage=0, unique_count=4, uniqueness_ratio=.67, likely_identifier=True),
        ColumnProfile(name="order_id", dtype="object", null_count=0, null_percentage=0, unique_count=6, uniqueness_ratio=1, likely_identifier=True),
        ColumnProfile(name="customer_id", dtype="object", null_count=0, null_percentage=0, unique_count=6, uniqueness_ratio=1, likely_identifier=True),
        ColumnProfile(name="order_date", dtype="object", null_count=0, null_percentage=0, unique_count=6, uniqueness_ratio=1, minimum_date="2024-07-01", maximum_date="2024-08-31"),
    ]
    return DatasetProfile(profile_id="profile", dataset_version_id="version", pipeline_run_id="run", row_count=6,
                          column_count=len(columns), schema_fingerprint="schema", profiler_version="2", columns=columns)


def sources(metric="revenue", reverse=False):
    analysis_period = period("2024-07-01", "2024-07-31") if reverse else period("2024-08-01", "2024-08-31")
    comparison_period = period("2024-08-01", "2024-08-31") if reverse else period("2024-07-01", "2024-07-31")
    request = AnalysisRequest(request_id="request", original_question="compare", primary_metric=metric,
                              analysis_period=analysis_period, comparison_period=comparison_period,
                              dataset_version_id="version", profile_id="profile", status=RequestStatus.READY,
                              agent_version="3", prompt_version="r1")
    plan = AnalysisPlan(plan_id="plan", request_id="request", dataset_version_id="version", profile_id="profile",
                        primary_metric=metric, analysis_period=analysis_period, comparison_period=comparison_period,
                        operations=[Operation(type=OperationType.AGGREGATE), Operation(type=OperationType.PERIOD_COMPARE)])
    analysis = AnalyticsEngine().execute(data(), plan, "source")
    analysis.analysis_id = "analysis"
    return request, plan, analysis


def investigation(metric="revenue", dimensions=None, reverse=False, materiality=None, kind=InvestigationType.CHANGE_DECOMPOSITION):
    request, analysis_plan, analysis = sources(metric, reverse)
    inv_plan = InvestigationAgent().build(request, analysis_plan, analysis, profile(),
                                          selected_dimensions=dimensions or ["region"],
                                          materiality=materiality, investigation_type=kind)
    result = InvestigationEngine().execute(data(), inv_plan, request, analysis_plan, analysis, profile(), "source")
    return inv_plan, result, request, analysis_plan, analysis


def test_revenue_decline_contributions_offsets_and_reconciliation():
    _, result, *_ = investigation()
    groups = {item.value: item for item in result.dimension_results[0].groups}
    assert groups["North"].absolute_change == -40
    assert groups["North"].contribution_percent == pytest.approx(57.142857)
    assert groups["North"].contribution_direction == "DRIVES_CHANGE"
    assert groups["Bangkok"].absolute_change == 10
    assert groups["Bangkok"].contribution_direction == "OFFSETS_CHANGE"
    assert result.reconciliation[0].reconciled
    assert result.reconciliation[0].decomposed_total_change == -70


def test_revenue_growth_and_negative_offset():
    _, result, *_ = investigation(reverse=True)
    groups = {item.value: item for item in result.dimension_results[0].groups}
    assert groups["North"].absolute_change == 40
    assert groups["Bangkok"].absolute_change == -10
    assert groups["Bangkok"].contribution_direction == "OFFSETS_CHANGE"


def test_reconciliation_tolerance_and_forced_failure():
    plan, _, request, analysis_plan, analysis = investigation()
    analysis.rows[0]["absolute_change"] += 1e-12
    assert InvestigationEngine().execute(data(), plan, request, analysis_plan, analysis, profile(), "source").reconciliation[0].reconciled
    analysis.rows[0]["absolute_change"] += 1
    failed = InvestigationEngine().execute(data(), plan, request, analysis_plan, analysis, profile(), "source")
    assert failed.status == "WARNING" and not failed.reconciliation[0].reconciled
    assert "not authoritative" in failed.warnings[0]


def test_group_presence_concentration_and_dimension_scan():
    _, result, *_ = investigation(dimensions=["region", "product_id"], kind=InvestigationType.DIMENSION_SCAN)
    regions = {item.value: item for item in result.dimension_results[0].groups}
    assert regions["Old"].analysis_value == 0
    assert regions["Bangkok"].comparison_value == 0
    concentration = result.concentration_metrics[0]
    assert concentration.top_1_share == pytest.approx(50)
    assert concentration.top_3_share == pytest.approx(100)
    assert concentration.cumulative_contribution_share == sorted(concentration.cumulative_contribution_share)
    assert {item.dimension for item in result.dimension_results} == {"region", "product_id"}
    assert all(item.total_absolute_movement > 0 for item in result.dimension_results)


@pytest.mark.parametrize("kind", list(InvestigationType))
def test_all_supported_investigation_types(kind):
    dimensions = ["region", "product_id"] if kind == InvestigationType.DIMENSION_SCAN else ["region"]
    _, result, *_ = investigation(kind=kind, dimensions=dimensions)
    assert result.investigation_type == kind


def test_zero_total_change_has_undefined_contributions():
    request, analysis_plan, analysis = sources()
    analysis.rows[0]["absolute_change"] = 0
    plan = InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])
    result = InvestigationEngine().execute(data(), plan, request, analysis_plan, analysis, profile(), "source")
    assert result.status == "WARNING"
    assert all(group.contribution_ratio is None for group in result.dimension_results[0].groups)


def test_materiality_thresholds():
    thresholds = MaterialityThresholds(minimum_absolute_change=15, minimum_contribution_percent=20, minimum_group_size=1)
    _, result, *_ = investigation(materiality=thresholds)
    groups = {item.value: item for item in result.dimension_results[0].groups}
    assert groups["North"].material and not groups["Bangkok"].material
    assert result.dimension_results[0].materially_changed_groups == 3


def test_metric_additivity_policy():
    registry = MetricRegistry()
    assert registry.get("revenue").additivity == MetricAdditivity.ADDITIVE
    assert registry.get("units_sold").additivity == MetricAdditivity.ADDITIVE
    assert registry.get("order_count").additivity == MetricAdditivity.SEMI_ADDITIVE
    assert registry.get("average_order_value").additivity == MetricAdditivity.DERIVED
    investigation("units_sold")
    for metric in ("order_count", "customer_count", "average_order_value"):
        request, analysis_plan, analysis = sources(metric)
        with pytest.raises(InvestigationValidationError, match="NON_ADDITIVE_METRIC"):
            InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])


def test_dimension_eligibility():
    validator = InvestigationSemanticValidator()
    assert validator.eligible_dimensions(profile())[:2] == ["product_id", "region"]
    request, analysis_plan, analysis = sources()
    for dimension in ("order_id", "customer_id"):
        with pytest.raises(InvestigationValidationError, match="INELIGIBLE_DIMENSION"):
            InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=[dimension])
    with pytest.raises(InvestigationValidationError, match="UNKNOWN_DIMENSION"):
        InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["country"])
    high = profile().model_copy(deep=True)
    high.row_count = 2000
    high.columns.append(ColumnProfile(name="campaign_code", dtype="object", null_count=0, null_percentage=0,
                                      unique_count=1900, uniqueness_ratio=.95, likely_identifier=True))
    with pytest.raises(InvestigationValidationError, match="INELIGIBLE_DIMENSION"):
        InvestigationAgent().build(request, analysis_plan, analysis, high, selected_dimensions=["campaign_code"])


class Provider:
    name = "mock"
    model_name = "mock-1"
    def __init__(self, payload): self.payload, self.calls = payload, 0
    def generate_structured(self, metadata): self.calls += 1; return self.payload


def provider_payload(**changes):
    payload = {"investigation_type": "CHANGE_DECOMPOSITION", "candidate_dimensions": ["region"],
               "selected_dimensions": ["region"], "methods": ["GROUP_CHANGE", "RECONCILIATION"]}
    payload.update(changes)
    return payload


def test_deterministic_path_avoids_provider_and_valid_candidate():
    request, analysis_plan, analysis = sources()
    provider = Provider(provider_payload())
    plan = InvestigationAgent(provider=provider).build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])
    assert provider.calls == 0 and plan.provider_name is None
    plan = InvestigationAgent(provider=provider).build(request, analysis_plan, analysis, profile(), use_provider=True)
    assert provider.calls == 1 and plan.provider_name == "mock"


@pytest.mark.parametrize("payload", [
    "malformed",
    provider_payload(selected_dimensions=["country"], candidate_dimensions=["country"]),
    provider_payload(investigation_type="CAUSAL_ANALYSIS"),
    provider_payload(sql="SELECT * FROM sales"),
    provider_payload(assumptions=["Region caused the decline"]),
])
def test_provider_failures_are_safe(payload):
    request, analysis_plan, analysis = sources()
    with pytest.raises(InvestigationProviderError):
        InvestigationAgent(provider=Provider(payload)).build(request, analysis_plan, analysis, profile(), use_provider=True)


def test_provider_timeout_exception_and_absence():
    request, analysis_plan, analysis = sources()
    provider = Mock(); provider.name = "mock"; provider.model_name = "model"; provider.generate_structured.side_effect = TimeoutError()
    with pytest.raises(InvestigationProviderError):
        InvestigationAgent(provider=provider).build(request, analysis_plan, analysis, profile(), use_provider=True)
    with pytest.raises(InvestigationProviderError):
        InvestigationAgent().build(request, analysis_plan, analysis, profile(), use_provider=True)


def test_source_validation_failures():
    request, analysis_plan, analysis = sources()
    request.status = RequestStatus.NEEDS_CLARIFICATION
    with pytest.raises(InvestigationValidationError, match="REQUEST_STATUS"):
        InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])
    request.status = RequestStatus.READY; analysis.execution_status = "FAILED"
    with pytest.raises(InvestigationValidationError, match="ANALYSIS_STATUS"):
        InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])
    analysis.execution_status = "COMPLETED"; analysis.dataset_version_id = "other"
    with pytest.raises(InvestigationValidationError, match="DATASET_MISMATCH"):
        InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])


def test_invalid_period_unsafe_content_and_empty_period():
    inv_plan, _, request, analysis_plan, analysis = investigation()
    invalid = inv_plan.model_copy(deep=True); invalid.analysis_period.start, invalid.analysis_period.end = invalid.analysis_period.end, invalid.analysis_period.start
    with pytest.raises(InvestigationValidationError, match="PERIOD"):
        InvestigationSemanticValidator().validate(invalid, request, analysis_plan, analysis, profile())
    unsafe = inv_plan.model_copy(update={"assumptions": ["SELECT * FROM sales"]})
    with pytest.raises(InvestigationValidationError, match="UNSAFE_CONTENT"):
        InvestigationSemanticValidator().validate(unsafe, request, analysis_plan, analysis, profile())
    unsafe_python = inv_plan.model_copy(update={"limitations": ["import os"]})
    with pytest.raises(InvestigationValidationError, match="UNSAFE_CONTENT"):
        InvestigationSemanticValidator().validate(unsafe_python, request, analysis_plan, analysis, profile())
    empty_data = data()[data().order_date.str.startswith("2024-08")]
    with pytest.raises(InvestigationExecutionError, match="Comparison period"):
        InvestigationEngine().execute(empty_data, inv_plan, request, analysis_plan, analysis, profile(), "source")


def test_missing_period_incomplete_analysis_and_filter_mismatch():
    request, analysis_plan, analysis = sources()
    analysis_plan.comparison_period = None
    with pytest.raises(InvestigationValidationError, match="MISSING_PERIOD"):
        InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])
    request, analysis_plan, analysis = sources()
    plan = InvestigationAgent().build(request, analysis_plan, analysis, profile(), selected_dimensions=["region"])
    analysis.rows = []
    with pytest.raises(InvestigationExecutionError, match="no total absolute change"):
        InvestigationEngine().execute(data(), plan, request, analysis_plan, analysis, profile(), "source")
    analysis = sources()[2]
    plan = plan.model_copy(update={"filters": [FilterExpression(field="region", operator="EQ", value="North")]})
    with pytest.raises(InvestigationValidationError, match="FILTER_MISMATCH"):
        InvestigationSemanticValidator().validate(plan, request, analysis_plan, analysis, profile())


def test_contract_schemas_forbid_extra_fields():
    assert InvestigationPlan.model_json_schema()["additionalProperties"] is False
    assert InvestigationResult.model_json_schema()["additionalProperties"] is False
    assert InvestigationCandidate.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        InvestigationCandidate.model_validate(provider_payload(contribution_values=[1, 2]))
