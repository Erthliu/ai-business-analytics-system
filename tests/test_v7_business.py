"""V7 evidence binding, rendering, gating, and provider tests."""
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.analytics_engine import AnalyticsEngine
from agents.business_interpretation_agent import BusinessInterpretationAgent
from agents.critic_agent import CriticAgent
from business.evidence import EvidenceBinder, EvidenceBindingError
from business.validator import BusinessInterpretationError, BusinessInterpretationValidator
from contracts.analysis_plan import Operation
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.business import BusinessClaim, BusinessInsight, ClaimType, ProviderBusinessInterpretation
from test_v4_semantics import frame as v4frame, plan as v4plan, profile as v4profile, period
from test_v6_critic import review_v4, review_v5


def test_pass_gate_and_period_change_rendering():
    review, _, _, analysis = review_v4()
    insight = BusinessInterpretationAgent().interpret(analysis, review)
    assert insight.status == "COMPLETE"
    assert "decreased by 70" in insight.headline and "58.33%" in insight.headline
    assert insight.evidence and all(item.evidence_refs for item in insight.key_insights)


def test_pass_with_warnings_preserves_caveats_and_fail_blocks():
    review, _, _, analysis = review_v4()
    review.overall_status = "PASS_WITH_WARNINGS"
    review.warnings = ["Partial period coverage detected."]
    insight = BusinessInterpretationAgent().interpret(analysis, review)
    assert insight.status == "COMPLETE_WITH_CAVEATS"
    assert review.warnings[0] in insight.caveats and review.warnings[0] in insight.key_insights[0].caveats
    review.overall_status = "FAIL"
    with pytest.raises(BusinessInterpretationError, match="FAIL"):
        BusinessInterpretationAgent().interpret(analysis, review)


def test_v5_contributor_offset_and_concentration_claims():
    review, _, investigation, _, _, analysis = review_v5()
    insight = BusinessInterpretationAgent().interpret(analysis, review, investigation)
    types = {claim.claim_type for claim in insight.claims}
    assert {ClaimType.METRIC_CHANGE, ClaimType.TOP_CONTRIBUTOR,
            ClaimType.OFFSETTING_CONTRIBUTOR, ClaimType.CONCENTRATION} <= types
    assert any("accounting for" in item.statement for item in insight.key_insights)
    assert any("offset" in item.statement for item in insight.key_insights)


def _insight_for(plan):
    profile = v4profile()
    request = AnalysisRequest(request_id=plan.request_id, original_question="business interpretation",
                              primary_metric=plan.primary_metric, dataset_version_id=plan.dataset_version_id,
                              profile_id=plan.profile_id, status=RequestStatus.READY,
                              agent_version="3", prompt_version="r1",
                              analysis_period=plan.analysis_period, comparison_period=plan.comparison_period)
    analysis = AnalyticsEngine().execute(v4frame(), plan, "hash")
    review = CriticAgent().review(request, plan, analysis, profile, "hash")
    assert review.overall_status == "PASS"
    return BusinessInterpretationAgent().interpret(analysis, review)


def test_scalar_grouped_trend_and_ranking_rendering():
    scalar = _insight_for(v4plan())
    assert scalar.claims[0].claim_type == ClaimType.METRIC_VALUE
    grouped_plan = v4plan(dimensions=["region"], operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY")])
    grouped = _insight_for(grouped_plan)
    assert "among region groups" in grouped.headline
    comparison = v4plan(dimensions=["region"], analysis_period=period("2024-08-01", "2024-08-31"),
                        comparison_period=period("2024-07-01", "2024-07-31"),
                        operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="GROUPED_PERIOD_COMPARE")])
    assert "undefined" in _insight_for(comparison).headline
    trend = v4plan(time_bucket="month", operations=[Operation(type="AGGREGATE"), Operation(type="TREND")])
    assert _insight_for(trend).claims[0].claim_type == ClaimType.TREND_DIRECTION
    ranking = v4plan(dimensions=["product_id"], limit=3, sort="revenue",
                     operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="RANK")])
    assert "ranked highest" in _insight_for(ranking).headline


def test_binder_rejects_nonexistent_reference_wrong_group_and_artifact():
    review, _, _, analysis = review_v4()
    catalog = EvidenceBinder().build(analysis, review)
    for reference in ("analysis:wrong:row:0", f"analysis:{analysis.analysis_id}:group:region:Moon"):
        claim = BusinessClaim(claim_type="METRIC_VALUE", metric="revenue", evidence_refs=[reference])
        with pytest.raises(EvidenceBindingError): EvidenceBinder.bind([claim], catalog)


def test_validator_rejects_wrong_number_metric_dimension_and_causal_language():
    review, _, investigation, _, _, analysis = review_v5()
    agent = BusinessInterpretationAgent()
    insight = agent.interpret(analysis, review, investigation)
    catalog = agent.binder.build(analysis, review, investigation)
    wrong_number = insight.model_copy(deep=True); wrong_number.key_insights[0].statement = "Revenue decreased by 999%."
    with pytest.raises(BusinessInterpretationError, match="numeric"):
        agent.validator.validate(wrong_number, catalog, review)
    wrong_metric = insight.model_copy(deep=True); wrong_metric.claims[0].metric = "profit"
    with pytest.raises(BusinessInterpretationError, match="Unknown metric"):
        agent.validator.validate(wrong_metric, catalog, review)
    wrong_dimension = insight.model_copy(deep=True); wrong_dimension.claims[1].dimension = "country"
    with pytest.raises(BusinessInterpretationError, match="dimension"):
        agent.validator.validate(wrong_dimension, catalog, review)
    for phrase in ("North caused the decline.", "Revenue fell because customers left.", "The change led to lower sales."):
        unsafe = insight.model_copy(deep=True); unsafe.headline = phrase
        with pytest.raises(BusinessInterpretationError, match="causal"):
            agent.validator.validate(unsafe, catalog, review)


@pytest.mark.parametrize("phrase", ["Increase marketing spend.", "Cut prices now.", "Target North customers.", "Discontinue Product A."])
def test_recommendations_are_rejected(phrase):
    review, _, _, analysis = review_v4()
    agent = BusinessInterpretationAgent(); insight = agent.interpret(analysis, review)
    insight.unanswered_questions = [phrase]
    with pytest.raises(BusinessInterpretationError, match="recommendation"):
        agent.validator.validate(insight, agent.binder.build(analysis, review), review)


def test_neutral_followup_question_is_allowed():
    review, _, _, analysis = review_v4()
    insight = BusinessInterpretationAgent().interpret(analysis, review)
    insight.unanswered_questions = ["Which validated dimensions warrant further investigation?"]
    BusinessInterpretationValidator().validate(insight, EvidenceBinder().build(analysis, review), review)


class Provider:
    name = "mock"
    model_name = "mock-1"
    def __init__(self, payload): self.payload = payload
    def generate_structured(self, metadata): return self.payload


def valid_provider_payload(analysis, review):
    deterministic = BusinessInterpretationAgent().interpret(analysis, review)
    return {"claims": [item.model_dump(mode="json") for item in deterministic.claims],
            "headline": deterministic.headline, "executive_summary": deterministic.executive_summary,
            "caveats": [], "unanswered_questions": ["What validated evidence could extend this analysis?"]}


def test_valid_provider_candidate_uses_bound_claims():
    review, _, _, analysis = review_v4()
    insight = BusinessInterpretationAgent(provider=Provider(valid_provider_payload(analysis, review))).interpret(analysis, review, use_provider=True)
    assert insight.provider_name == "mock" and insight.status == "COMPLETE"


@pytest.mark.parametrize("mutate", ["malformed", "hallucinated", "number", "recommendation", "causal"])
def test_provider_failures_use_deterministic_fallback(mutate):
    review, _, _, analysis = review_v4()
    payload = valid_provider_payload(analysis, review)
    if mutate == "malformed": payload = "bad"
    elif mutate == "hallucinated": payload["claims"][0]["evidence_refs"] = ["invented"]
    elif mutate == "number": payload["headline"] = "Revenue fell 999%."
    elif mutate == "recommendation": payload["unanswered_questions"] = ["Increase marketing spend."]
    else: payload["executive_summary"] = "North caused the decline."
    insight = BusinessInterpretationAgent(provider=Provider(payload)).interpret(analysis, review, use_provider=True)
    assert insight.status == "COMPLETE_WITH_CAVEATS"
    assert "deterministic rendering used" in insight.caveats[-1]
    assert "999" not in insight.headline and "caused" not in insight.executive_summary


def test_provider_timeout_absence_and_contract_schemas():
    review, _, _, analysis = review_v4()
    provider = Mock(); provider.name = "mock"; provider.model_name = "model"; provider.generate_structured.side_effect = TimeoutError()
    assert BusinessInterpretationAgent(provider=provider).interpret(analysis, review, use_provider=True).status == "COMPLETE_WITH_CAVEATS"
    assert BusinessInterpretationAgent().interpret(analysis, review, use_provider=True).status == "COMPLETE_WITH_CAVEATS"
    assert BusinessInsight.model_json_schema()["additionalProperties"] is False
    assert ProviderBusinessInterpretation.model_json_schema()["additionalProperties"] is False
    assert BusinessClaim.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError): ProviderBusinessInterpretation.model_validate({"claims": [], "new_fact": "x"})


def test_provider_external_caveat_and_declarative_question_fall_back():
    review, _, _, analysis = review_v4()
    for field, value in (("caveats", ["A competitor changed prices."]),
                         ("unanswered_questions", ["A competitor changed prices."])):
        payload = valid_provider_payload(analysis, review)
        payload[field] = value
        insight = BusinessInterpretationAgent(provider=Provider(payload)).interpret(
            analysis, review, use_provider=True
        )
        assert insight.status == "COMPLETE_WITH_CAVEATS"
        assert value[0] not in insight.caveats + insight.unanswered_questions


def test_qa_caveat_claim_renders_only_bound_review_evidence():
    review, _, _, analysis = review_v4()
    review.warnings = ["Partial period coverage detected."]
    catalog = EvidenceBinder().build(analysis, review)
    reference = f"review:{review.review_id}:warning:0"
    claim = BusinessClaim(claim_type="QA_CAVEAT", evidence_refs=[reference])
    assert BusinessInterpretationAgent().renderer.render(claim, catalog, analysis) == (
        "Caveat: Partial period coverage detected."
    )
