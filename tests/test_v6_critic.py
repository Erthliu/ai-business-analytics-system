"""Deterministic V6 critic checks and provider boundary tests."""
import math
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.critic_agent import CriticAgent
from agents.analytics_engine import AnalyticsEngine
from contracts.analysis_plan import Operation, OperationType
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.critic import CriticReview, ProviderCritique, ReviewStatus
from contracts.investigation import ContributionDirection
from test_v5_investigation import investigation, profile, sources
from test_v4_semantics import frame as v4frame, period as v4period, plan as v4plan, profile as v4profile


def review_v4():
    request, plan, analysis = sources()
    return CriticAgent().review(request, plan, analysis, profile(), "source"), request, plan, analysis


def review_v5():
    inv_plan, inv, request, plan, analysis = investigation()
    return CriticAgent().review(request, plan, analysis, profile(), "source", inv_plan, inv), inv_plan, inv, request, plan, analysis


def codes(review):
    return {item.issue_code for item in review.issues}


def test_clean_v4_only_and_v5_chain_pass():
    v4, *_ = review_v4()
    v5, *_ = review_v5()
    assert v4.overall_status == ReviewStatus.PASS and v4.downstream_eligible
    assert v5.overall_status == ReviewStatus.PASS and v5.downstream_eligible
    assert all(item.status in {"PASS", "SKIPPED"} for item in v5.deterministic_checks)


@pytest.mark.parametrize(("target", "value", "expected"), [
    ("request", "other", "REQUEST_ID_MISMATCH"),
    ("plan", "other", "PLAN_ID_MISMATCH"),
    ("dataset", "other", "DATASET_VERSION_MISMATCH"),
    ("profile", "other", "PROFILE_ID_MISMATCH"),
    ("source", "other", "SOURCE_HASH_MISMATCH"),
])
def test_provenance_mismatches_fail(target, value, expected):
    _, request, plan, analysis = review_v4()
    if target == "request": analysis.request_id = value
    elif target == "plan": analysis.plan_id = value
    elif target == "dataset": analysis.dataset_version_id = value
    elif target == "profile": analysis.profile_id = value
    source = value if target == "source" else "source"
    reviewed = CriticAgent().review(request, plan, analysis, profile(), source)
    assert reviewed.overall_status == "FAIL" and expected in codes(reviewed)


def test_status_and_plan_hash_failures():
    _, request, plan, analysis = review_v4()
    request.status = "NEEDS_CLARIFICATION"
    analysis.execution_status = "FAILED"
    analysis.plan_hash = "wrong"
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source")
    assert {"REQUEST_NOT_READY", "ANALYSIS_INCOMPLETE", "PLAN_HASH_MISMATCH"} <= codes(reviewed)
    assert reviewed.overall_status == "FAIL"


def test_numeric_change_percentage_and_row_count_corruption():
    _, request, plan, analysis = review_v4()
    analysis.rows[0]["absolute_change"] = 999
    analysis.rows[0]["percentage_change"] = 999
    analysis.row_count = 2
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source")
    assert {"ABSOLUTE_CHANGE_INVALID", "PERCENTAGE_CHANGE_INVALID", "ROW_COUNT_MISMATCH"} <= codes(reviewed)


@pytest.mark.parametrize("invalid", [float("inf"), float("nan")])
def test_non_finite_values_fail(invalid):
    _, request, plan, analysis = review_v4()
    analysis.rows[0]["analysis_period_value"] = invalid
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source")
    assert "NON_FINITE_VALUE" in codes(reviewed) and reviewed.overall_status == "FAIL"


def test_zero_denominator_policy_is_checked():
    _, request, plan, analysis = review_v4()
    row = analysis.rows[0]
    row.update(analysis_period_value=10, comparison_period_value=0, absolute_change=10, percentage_change=None)
    assert "PERCENTAGE_CHANGE_INVALID" not in codes(CriticAgent().review(request, plan, analysis, profile(), "source"))
    row["percentage_change"] = 0
    assert "PERCENTAGE_CHANGE_INVALID" in codes(CriticAgent().review(request, plan, analysis, profile(), "source"))


def v4_review_inputs(plan):
    request = AnalysisRequest(request_id=plan.request_id, original_question="review", primary_metric=plan.primary_metric,
                              dataset_version_id=plan.dataset_version_id, profile_id=plan.profile_id,
                              status=RequestStatus.READY, agent_version="3", prompt_version="r1",
                              analysis_period=plan.analysis_period, comparison_period=plan.comparison_period)
    analysis = AnalyticsEngine().execute(v4frame(), plan, "hash")
    return request, analysis


def test_grouped_comparison_arithmetic_and_duplicate_keys():
    plan = v4plan(dimensions=["region"], analysis_period=v4period("2024-08-01", "2024-08-31"),
                  comparison_period=v4period("2024-07-01", "2024-07-31"),
                  operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="GROUPED_PERIOD_COMPARE")])
    request, analysis = v4_review_inputs(plan)
    assert CriticAgent().review(request, plan, analysis, v4profile(), "hash").overall_status == "PASS"
    analysis.rows[0]["absolute_change"] = 999
    assert "ABSOLUTE_CHANGE_INVALID" in codes(CriticAgent().review(request, plan, analysis, v4profile(), "hash"))
    analysis.rows.append(dict(analysis.rows[1])); analysis.row_count += 1
    assert "DUPLICATE_GROUP_KEY" in codes(CriticAgent().review(request, plan, analysis, v4profile(), "hash"))


def test_ranking_and_trend_order_are_checked():
    ranking = v4plan(dimensions=["product_id"], limit=3, sort="revenue",
                     operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="RANK")])
    request, analysis = v4_review_inputs(ranking)
    analysis.rows.reverse()
    assert "RANK_ORDER_INVALID" in codes(CriticAgent().review(request, ranking, analysis, v4profile(), "hash"))
    trend = v4plan(time_bucket="month", operations=[Operation(type="AGGREGATE"), Operation(type="TREND")])
    request, analysis = v4_review_inputs(trend)
    analysis.rows.reverse()
    assert "TREND_ORDER_INVALID" in codes(CriticAgent().review(request, trend, analysis, v4profile(), "hash"))


@pytest.mark.parametrize(("mutation", "expected"), [
    ("fake_flag", "RECONCILIATION_FLAG_INVALID"),
    ("ratio", "CONTRIBUTION_RATIO_INVALID"),
    ("direction", "CONTRIBUTION_DIRECTION_INVALID"),
    ("expected", "EXPECTED_TOTAL_INVALID"),
    ("decomposed", "DECOMPOSED_TOTAL_INVALID"),
    ("rank", "CONTRIBUTION_RANK_INVALID"),
])
def test_investigation_values_are_independently_verified(mutation, expected):
    _, inv_plan, inv, request, plan, analysis = review_v5()
    if mutation == "fake_flag":
        inv.reconciliation[0].decomposed_total_change += 5
        inv.reconciliation[0].reconciliation_difference = 5
        inv.reconciliation[0].reconciled = True
    elif mutation == "ratio": inv.dimension_results[0].groups[0].contribution_ratio = 999
    elif mutation == "direction": inv.dimension_results[0].groups[0].contribution_direction = ContributionDirection.OFFSETS_CHANGE
    elif mutation == "expected": inv.reconciliation[0].expected_total_change = 999
    elif mutation == "decomposed": inv.reconciliation[0].decomposed_total_change = 999
    else: inv.dimension_results[0].groups[0].rank = 2
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source", inv_plan, inv)
    assert expected in codes(reviewed) and reviewed.overall_status == "FAIL"


def test_non_additive_investigation_is_critical():
    _, inv_plan, inv, request, plan, analysis = review_v5()
    for artifact in (plan, analysis, inv_plan, inv):
        if hasattr(artifact, "primary_metric"): artifact.primary_metric = "average_order_value"
        if hasattr(artifact, "target_metric"): artifact.target_metric = "average_order_value"
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source", inv_plan, inv)
    assert "NON_ADDITIVE_DECOMPOSITION" in codes(reviewed)


def test_coverage_warning_preserved_and_suppressed_warning_detected():
    _, request, plan, analysis = review_v4()
    covered_profile = profile().model_copy(deep=True)
    covered_profile.warnings = ["Partial period coverage detected."]
    reviewed = CriticAgent().review(request, plan, analysis, covered_profile, "source")
    assert reviewed.overall_status == "PASS_WITH_WARNINGS"
    assert "COVERAGE_WARNING_SUPPRESSED" in codes(reviewed)
    analysis.warnings.append("Partial period coverage detected.")
    assert "COVERAGE_WARNING_SUPPRESSED" not in codes(CriticAgent().review(request, plan, analysis, covered_profile, "source"))


def test_evidence_and_causality_guards():
    _, inv_plan, inv, request, plan, analysis = review_v5()
    inv.deterministic_findings.append("Nowhere represented 999.9% of revenue.")
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source", inv_plan, inv)
    assert "UNSUPPORTED_FINDING" in codes(reviewed)
    inv.deterministic_findings[-1] = "North caused the decline."
    reviewed = CriticAgent().review(request, plan, analysis, profile(), "source", inv_plan, inv)
    assert "UNSUPPORTED_CAUSAL_LANGUAGE" in codes(reviewed)
    inv.deterministic_findings[-1] = "North contributed to the observed decline."
    assert "UNSUPPORTED_CAUSAL_LANGUAGE" not in codes(CriticAgent().review(request, plan, analysis, profile(), "source", inv_plan, inv))


class Provider:
    name = "mock"
    model_name = "mock-1"
    def __init__(self, payload): self.payload = payload
    def generate_structured(self, metadata): return self.payload


def critique(artifact_id="analysis"):
    return {"issues": [{"issue_code": "WORDING", "severity": "LOW", "message": "Add a caveat.",
                         "artifact_type": "ComputedAnalysis", "artifact_id": artifact_id}],
            "interpretation_risks": [], "missing_caveats": ["Descriptive only."], "confidence": "MEDIUM"}


def test_valid_provider_is_advisory_and_cannot_override_fail():
    _, request, plan, analysis = review_v4()
    valid = CriticAgent(provider=Provider(critique())).review(request, plan, analysis, profile(), "source")
    assert valid.overall_status == "PASS" and len(valid.provider_findings) == 1
    analysis.rows[0]["absolute_change"] = 999
    failed = CriticAgent(provider=Provider({"issues": [], "interpretation_risks": [], "missing_caveats": [], "confidence": "HIGH"})).review(request, plan, analysis, profile(), "source")
    assert failed.overall_status == "FAIL"


@pytest.mark.parametrize("payload", ["malformed", critique("hallucinated"), {"confidence": "HIGH", "replacement_value": 999}])
def test_provider_failure_warns_without_failing(payload):
    _, request, plan, analysis = review_v4()
    reviewed = CriticAgent(provider=Provider(payload)).review(request, plan, analysis, profile(), "source")
    assert reviewed.overall_status == "PASS_WITH_WARNINGS"
    assert reviewed.review_confidence == "MEDIUM" and "unavailable" in reviewed.warnings[-1]


def test_provider_hallucinated_evidence_reference_is_rejected():
    _, request, plan, analysis = review_v4()
    payload = critique()
    payload["issues"][0]["evidence_reference"] = "unknown-check"
    reviewed = CriticAgent(provider=Provider(payload)).review(request, plan, analysis, profile(), "source")
    assert reviewed.overall_status == "PASS_WITH_WARNINGS" and not reviewed.provider_findings


def test_provider_timeout_and_exception():
    _, request, plan, analysis = review_v4()
    provider = Mock(); provider.name = "mock"; provider.model_name = "model"; provider.generate_structured.side_effect = TimeoutError()
    reviewed = CriticAgent(provider=provider).review(request, plan, analysis, profile(), "source")
    assert reviewed.overall_status == "PASS_WITH_WARNINGS"


def test_contracts_and_decision_gate():
    assert CriticReview.model_json_schema()["additionalProperties"] is False
    assert ProviderCritique.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError): ProviderCritique.model_validate({"confidence": "HIGH", "new_fact": "invented"})
    review, *_ = review_v4()
    assert review.downstream_eligible
