"""V8 contracts, presentation choices, assembly, traceability, and safety tests."""
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.analytics_engine import AnalyticsEngine
from agents.business_interpretation_agent import BusinessInterpretationAgent
from agents.critic_agent import CriticAgent
from agents.report_agent import ReportAgent
from contracts.analysis_plan import Operation
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.reporting import (
    Audience, ChartSpec, ChartType, ProviderReportProposal, ReportArtifact,
    ReportSpec, TableSpec,
)
from contracts.reporting import ReportStatus
from reporting.assembler import ReportAssembler
from reporting.validator import ReportValidationError
from test_v4_semantics import frame as v4frame, plan as v4plan, profile as v4profile, period
from test_v6_critic import review_v4, review_v5


def _chain(plan=None):
    if plan is None:
        review, _, _, analysis = review_v4()
    else:
        profile = v4profile()
        request = AnalysisRequest(
            request_id=plan.request_id, original_question="report presentation",
            primary_metric=plan.primary_metric, dataset_version_id=plan.dataset_version_id,
            profile_id=plan.profile_id, status=RequestStatus.READY,
            agent_version="3", prompt_version="r1", analysis_period=plan.analysis_period,
            comparison_period=plan.comparison_period,
        )
        analysis = AnalyticsEngine().execute(v4frame(), plan, "hash")
        review = CriticAgent().review(request, plan, analysis, profile, "hash")
    insight = BusinessInterpretationAgent().interpret(analysis, review)
    spec = ReportAgent().create_spec(insight, review)
    return insight, review, spec, ReportAssembler().assemble(spec, insight, review)


def test_complete_and_caveated_gates_and_critic_fail():
    insight, review, _, report = _chain()
    assert report.status == ReportStatus.COMPLETE
    blocked = insight.model_copy(update={"status": "BLOCKED"})
    with pytest.raises(ReportValidationError, match="BLOCKED"):
        ReportAgent().create_spec(blocked, review)
    review, _, _, analysis = review_v4()
    review.overall_status = "PASS_WITH_WARNINGS"
    review.warnings = ["Partial period coverage detected."]
    caveated = BusinessInterpretationAgent().interpret(analysis, review)
    spec = ReportAgent().create_spec(caveated, review)
    report = ReportAssembler().assemble(spec, caveated, review)
    assert report.status == ReportStatus.COMPLETE_WITH_CAVEATS
    assert report.caveats == caveated.caveats
    assert any(section.section_type == "QA_CAVEATS" for section in report.rendered_sections)
    missing_caveat_section = spec.model_copy(deep=True)
    missing_caveat_section.sections = [
        section for section in missing_caveat_section.sections if section.section_type != "QA_CAVEATS"
    ]
    with pytest.raises(ReportValidationError, match="visible QA caveat"):
        ReportAssembler().assemble(missing_caveat_section, caveated, review)
    review.overall_status = "FAIL"
    with pytest.raises(ReportValidationError, match="FAIL"):
        ReportAgent().create_spec(insight, review)
def test_audience_sections_and_factual_consistency():
    review, _, _, analysis = review_v4()
    insight = BusinessInterpretationAgent().interpret(analysis, review)
    reports = {}
    for audience in Audience:
        spec = ReportAgent().create_spec(insight, review, audience)
        reports[audience] = ReportAssembler().assemble(spec, insight, review)
    assert any(item.section_type == "METHODOLOGY" for item in reports[Audience.ANALYST].rendered_sections)
    assert not any(item.section_type == "METHODOLOGY" for item in reports[Audience.EXECUTIVE].rendered_sections)
    statements = lambda report: {item.text for section in report.rendered_sections for item in section.statements}
    assert len({frozenset(statements(report)) for report in reports.values()}) == 1


def test_missing_claim_evidence_mismatch_and_orphan_prose_rejected():
    insight, review, spec, report = _chain()
    missing = spec.model_copy(deep=True)
    missing.sections[0].claim_ids = ["claim:999"]
    with pytest.raises(ReportValidationError, match="claim"):
        ReportAssembler().assemble(missing, insight, review)
    mismatch = spec.model_copy(deep=True)
    mismatch.sections[1].evidence_refs = list(spec.sections[0].evidence_refs)
    mismatch.sections[1].claim_ids = ["claim:0"]
    mismatch.sections[1].evidence_refs.append("missing")
    with pytest.raises(ReportValidationError, match="evidence"):
        ReportAssembler().assemble(mismatch, insight, review)
    orphan = report.model_copy(deep=True)
    orphan.rendered_sections[0].statements[0].text = "Revenue changed by 999%."
    with pytest.raises(ReportValidationError, match="orphan"):
        ReportAssembler().validator.validate_artifact(orphan, spec, insight, review)


def test_chart_and_table_tampering_and_invalid_chart_type_rejected():
    insight, review, spec, report = _chain()
    chart = report.model_copy(deep=True)
    chart.charts[0].data[0].y = 999
    with pytest.raises(ReportValidationError, match="chart data"):
        ReportAssembler().validator.validate_artifact(chart, spec, insight, review)
    table = report.model_copy(deep=True)
    table.tables[0].rows[0][table.tables[0].columns[0]] = 999
    with pytest.raises(ReportValidationError, match="table data"):
        ReportAssembler().validator.validate_artifact(table, spec, insight, review)
    bad_spec = spec.model_copy(deep=True)
    bad_spec.chart_specs[0].chart_type = ChartType.LINE
    with pytest.raises(ReportValidationError, match="incompatible"):
        ReportAssembler().assemble(bad_spec, insight, review)


def test_chart_mapping_for_scalar_comparison_grouped_trend_ranking_and_v5():
    scalar = _chain(v4plan())[2]
    assert scalar.chart_specs[0].chart_type == ChartType.KPI
    comparison = _chain()[2]
    assert comparison.chart_specs[0].chart_type == ChartType.COMPARISON_BAR
    grouped = v4plan(
        dimensions=["region"], analysis_period=period("2024-08-01", "2024-08-31"),
        comparison_period=period("2024-07-01", "2024-07-31"),
        operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"),
                    Operation(type="GROUPED_PERIOD_COMPARE")],
    )
    grouped_chain = _chain(grouped)
    assert grouped_chain[2].chart_specs[0].chart_type == ChartType.COMPARISON_BAR
    assert {point.series for point in grouped_chain[3].charts[0].data} == {"analysis", "comparison"}
    assert any(section.section_type == "KEY_DRIVERS" for section in grouped_chain[2].sections)
    trend = v4plan(time_bucket="month", operations=[Operation(type="AGGREGATE"), Operation(type="TREND")])
    trend_chain = _chain(trend)
    assert trend_chain[2].chart_specs[0].chart_type == ChartType.LINE
    assert len(trend_chain[3].charts[0].data) == len(trend_chain[0].evidence)
    ranking = v4plan(dimensions=["product_id"], limit=3, sort="revenue",
                     operations=[Operation(type="AGGREGATE"), Operation(type="GROUP_BY"), Operation(type="RANK")])
    ranking_chain = _chain(ranking)
    assert ranking_chain[2].chart_specs[0].chart_type == ChartType.BAR
    assert len(ranking_chain[3].charts[0].data) == len(ranking_chain[0].evidence)
    assert any(section.section_type == "RANKING" for section in ranking_chain[2].sections)
    review, _, investigation, _, _, analysis = review_v5()
    insight = BusinessInterpretationAgent().interpret(analysis, review, investigation)
    spec = ReportAgent().create_spec(insight, review)
    assert any(item.chart_type == ChartType.BAR for item in spec.chart_specs)
    assert any("contribution_percent" in item.columns for item in spec.table_specs)


def test_causal_and_recommendation_report_content_rejected_but_questions_allowed():
    insight, review, _, _ = _chain()
    for unsafe, match in (("Revenue fell because customers left.", "causal"),
                          ("Revenue report: Increase marketing spend.", "Recommendation")):
        changed = insight.model_copy(deep=True)
        changed.headline = changed.executive_summary = unsafe
        changed.key_insights[0].statement = unsafe
        spec = ReportAgent().create_spec(changed, review)
        with pytest.raises(ReportValidationError, match=match):
            ReportAssembler().assemble(spec, changed, review)
    insight.unanswered_questions = ["Which validated dimension should be investigated next?"]
    spec = ReportAgent().create_spec(insight, review)
    ReportAssembler().assemble(spec, insight, review)


class Provider:
    name = "mock"
    model_name = "layout-1"
    def __init__(self, payload): self.payload = payload
    def generate_structured(self, metadata):
        if isinstance(self.payload, Exception): raise self.payload
        return self.payload


def _proposal(insight, review):
    sections = ReportAgent()._sections(insight, Audience.EXECUTIVE)
    return {
        "report_title": "Revenue Executive Report",
        "section_order": [item.section_type for item in reversed(sections)],
        "prioritized_claim_ids": ["claim:0"],
        "chart_preferences": [{"claim_id": "claim:0", "chart_type": "KPI"}],
        "included_caveats": list(insight.caveats),
    }


def test_valid_provider_controls_layout_not_facts():
    insight, review, _, _ = _chain()
    spec = ReportAgent(provider=Provider(_proposal(insight, review))).create_spec(
        insight, review, use_provider=True
    )
    report = ReportAssembler().assemble(spec, insight, review)
    assert spec.provider_name == "mock" and spec.report_title == "Revenue Executive Report"
    assert report.rendered_sections[0].section_type == spec.sections[0].section_type
    assert {item.text for section in report.rendered_sections for item in section.statements} <= {
        insight.headline, insight.executive_summary, *[item.statement for item in insight.key_insights]
    }


@pytest.mark.parametrize("mutation", ["malformed", "timeout", "claim", "evidence", "number", "caveat", "recommendation", "causal"])
def test_provider_failure_uses_deterministic_fallback(mutation):
    insight, review, _, _ = _chain()
    payload = _proposal(insight, review)
    if mutation == "malformed": payload = {"unknown": True}
    elif mutation == "timeout": payload = TimeoutError()
    elif mutation == "claim": payload["prioritized_claim_ids"] = ["claim:999"]
    elif mutation == "evidence": payload["evidence_refs"] = ["invented"]
    elif mutation == "number": payload["report_title"] = "Revenue 12 Report"
    elif mutation == "caveat":
        insight.caveats = ["Mandatory QA warning."]
        insight.status = "COMPLETE_WITH_CAVEATS"
        payload["included_caveats"] = []
    elif mutation == "recommendation": payload["report_title"] = "Increase Marketing Spend Report"
    else: payload["report_title"] = "Revenue Caused Decline Report"
    spec = ReportAgent(provider=Provider(payload)).create_spec(insight, review, use_provider=True)
    assert spec.report_title.endswith("Performance Report")
    ReportAssembler().assemble(spec, insight, review)


def test_contract_json_schemas_are_strict():
    for contract in (ReportSpec, ReportArtifact, ChartSpec, TableSpec, ProviderReportProposal):
        assert contract.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        ProviderReportProposal.model_validate({"unexpected": "fact"})
