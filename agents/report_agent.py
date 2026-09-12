"""V8 Report Agent: presentation choices over approved V7 artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from time import perf_counter

from contracts.business import BusinessInsight, ClaimType
from contracts.critic import CriticReview
from contracts.reporting import (
    Audience, ChartSpec, ChartType, ProviderReportProposal, ReportSectionSpec,
    ReportSpec, SectionType, SortDirection, TableSpec,
)
from profiling.llm import LLMProvider
from reporting import REPORT_RULES_VERSION
from reporting.data import claim_catalog, eligible_chart_types, evidence_catalog
from reporting.validator import ReportValidator, ensure_eligible

logger = logging.getLogger(__name__)


@dataclass
class ReportAgent:
    """Choose report layout while leaving all analytical truth unchanged."""

    provider: LLMProvider | None = None
    agent_version: str = "8.0.0"
    prompt_version: str = "report-v1"
    report_rules_version: str = REPORT_RULES_VERSION

    def __post_init__(self) -> None:
        self.validator = ReportValidator()

    def create_spec(self, insight: BusinessInsight, review: CriticReview,
                    audience: Audience = Audience.EXECUTIVE, *,
                    use_provider: bool = False) -> ReportSpec:
        """Create a validated deterministic or provider-assisted ReportSpec."""
        started = perf_counter()
        ensure_eligible(insight, review)
        claims = claim_catalog(insight)
        claim_order = list(claims)
        sections = self._sections(insight, audience)
        title = self._title(insight)
        provider_attempted = use_provider
        provider_succeeded = False
        provider_name = model_name = None
        chart_preferences: dict[str, ChartType] = {}
        if use_provider and self.provider:
            provider_name, model_name = self.provider.name, self.provider.model_name
            try:
                proposal = ProviderReportProposal.model_validate(self.provider.generate_structured({
                    "audience": audience,
                    "business_insight_id": insight.insight_id,
                    "headline": insight.headline,
                    "claims": [{"claim_id": claim_id, "claim": claim.model_dump(mode="json")}
                               for claim_id, claim in claims.items()],
                    "available_sections": [section.section_type for section in sections],
                    "mandatory_caveats": insight.caveats,
                }))
                self.validator.validate_proposal(
                    proposal, insight, [section.section_type for section in sections]
                )
                by_type = {section.section_type: section for section in sections}
                sections = [by_type[item] for item in proposal.section_order]
                claim_order = list(dict.fromkeys([*proposal.prioritized_claim_ids, *claim_order]))
                chart_preferences = {item.claim_id: item.chart_type for item in proposal.chart_preferences}
                title = proposal.report_title or title
                provider_succeeded = True
            except Exception as error:
                logger.warning("Optional report proposal rejected type=%s", type(error).__name__)
        elif use_provider:
            logger.warning("Optional report provider unavailable; deterministic specification used")
        charts = self._charts(insight, claim_order, chart_preferences)
        tables = self._tables(insight, claim_order)
        included_refs = list(dict.fromkeys(
            reference for claim_id in claim_order for reference in claims[claim_id].evidence_refs
        ))
        spec = ReportSpec(
            business_insight_id=insight.insight_id,
            critic_review_id=insight.critic_review_id,
            analysis_id=insight.analysis_id,
            investigation_id=insight.investigation_id,
            dataset_version_id=insight.dataset_version_id,
            audience=audience,
            report_title=title,
            sections=sections,
            chart_specs=charts,
            table_specs=tables,
            included_claim_ids=claim_order,
            included_evidence_refs=included_refs,
            included_caveats=list(insight.caveats),
            agent_version=self.agent_version,
            prompt_version=self.prompt_version,
            provider_name=provider_name,
            model_name=model_name,
            report_rules_version=self.report_rules_version,
        )
        self.validator.validate_spec(spec, insight, review)
        logger.info(
            "Report spec %s insight=%s review=%s analysis=%s investigation=%s dataset=%s audience=%s "
            "agent=%s rules=%s sections=%d charts=%d tables=%d caveats=%d provider_attempted=%s "
            "provider_succeeded=%s elapsed=%.3fs",
            spec.report_spec_id, insight.insight_id, insight.critic_review_id, insight.analysis_id,
            insight.investigation_id, insight.dataset_version_id, audience, self.agent_version,
            self.report_rules_version, len(sections), len(charts), len(tables), len(insight.caveats),
            provider_attempted, provider_succeeded, perf_counter() - started,
        )
        return spec

    def _sections(self, insight: BusinessInsight, audience: Audience) -> list[ReportSectionSpec]:
        claims = claim_catalog(insight)
        all_ids = list(claims)
        all_refs = list(dict.fromkeys(ref for claim in claims.values() for ref in claim.evidence_refs))
        sections = [
            ReportSectionSpec(section_id="executive-summary", section_type="EXECUTIVE_SUMMARY",
                              title="Executive Summary", claim_ids=all_ids, evidence_refs=all_refs),
            ReportSectionSpec(section_id="primary-result", section_type="PRIMARY_RESULT",
                              title="Primary Result", claim_ids=[all_ids[0]],
                              evidence_refs=claims[all_ids[0]].evidence_refs),
        ]
        driver_ids = [item for item, claim in claims.items() if claim.claim_type in {
            ClaimType.TOP_CONTRIBUTOR, ClaimType.OFFSETTING_CONTRIBUTOR, ClaimType.CONCENTRATION
        }]
        trend_ids = [item for item, claim in claims.items() if claim.claim_type == ClaimType.TREND_DIRECTION]
        ranking_ids = [item for item in driver_ids if self._is_ranking(claims[item], insight)]
        driver_ids = [item for item in driver_ids if item not in ranking_ids]
        for section_type, title, ids in ((SectionType.KEY_DRIVERS, "Key Drivers", driver_ids),
                                         (SectionType.TREND, "Trend", trend_ids),
                                         (SectionType.RANKING, "Ranking", ranking_ids)):
            if ids:
                sections.append(ReportSectionSpec(
                    section_id=section_type.value.lower().replace("_", "-"), section_type=section_type,
                    title=title, claim_ids=ids,
                    evidence_refs=list(dict.fromkeys(ref for item in ids for ref in claims[item].evidence_refs)),
                ))
        sections.append(ReportSectionSpec(section_id="evidence", section_type="EVIDENCE_TABLE",
                                          title="Evidence", claim_ids=all_ids, evidence_refs=all_refs))
        if insight.caveats:
            sections.append(ReportSectionSpec(section_id="caveats", section_type="QA_CAVEATS", title="Caveats"))
        if insight.unanswered_questions:
            sections.append(ReportSectionSpec(section_id="questions", section_type="UNANSWERED_QUESTIONS",
                                              title="Unanswered Questions"))
        if audience == Audience.ANALYST:
            sections.append(ReportSectionSpec(section_id="methodology", section_type="METHODOLOGY",
                                              title="Methodology"))
        if audience == Audience.EXECUTIVE:
            order = {SectionType.EXECUTIVE_SUMMARY: 0, SectionType.PRIMARY_RESULT: 1,
                     SectionType.KEY_DRIVERS: 2, SectionType.TREND: 3, SectionType.RANKING: 3,
                     SectionType.QA_CAVEATS: 4, SectionType.UNANSWERED_QUESTIONS: 5,
                     SectionType.EVIDENCE_TABLE: 6}
            sections.sort(key=lambda item: order[item.section_type])
        return sections

    def _charts(self, insight: BusinessInsight, claim_order: list[str],
                preferences: dict[str, ChartType]) -> list[ChartSpec]:
        claims = claim_catalog(insight)
        charts = []
        defaults = {ClaimType.METRIC_VALUE: ChartType.KPI, ClaimType.METRIC_CHANGE: ChartType.COMPARISON_BAR,
                    ClaimType.TOP_CONTRIBUTOR: ChartType.BAR, ClaimType.OFFSETTING_CONTRIBUTOR: ChartType.BAR,
                    ClaimType.CONCENTRATION: ChartType.BAR, ClaimType.TREND_DIRECTION: ChartType.LINE}
        for claim_id in claim_order:
            claim = claims[claim_id]
            if not eligible_chart_types(claim.claim_type): continue
            default = defaults[claim.claim_type]
            record = evidence_catalog(insight)[claim.evidence_refs[0]]
            if (claim.claim_type == ClaimType.TOP_CONTRIBUTOR and
                    isinstance(record.value, dict) and "analysis_period_value" in record.value):
                default = ChartType.COMPARISON_BAR
            chart_type = preferences.get(claim_id, default)
            charts.append(ChartSpec(
                chart_id=f"chart-{claim_id.split(':')[1]}", chart_type=chart_type,
                title=f"{claim.metric.replace('_', ' ').title() if claim.metric else 'Approved'} evidence",
                claim_ids=[claim_id], evidence_refs=claim.evidence_refs,
                x_field="category", y_field=self._value_field(claim, insight),
                sort=SortDirection.DESC if chart_type == ChartType.BAR else SortDirection.NONE,
                unit="percent" if claim.claim_type == ClaimType.CONCENTRATION else claim.metric,
            ))
        return charts

    def _tables(self, insight: BusinessInsight, claim_order: list[str]) -> list[TableSpec]:
        claims, evidence = claim_catalog(insight), evidence_catalog(insight)
        tables = []
        for claim_id in claim_order:
            claim = claims[claim_id]
            if claim.claim_type == ClaimType.QA_CAVEAT: continue
            table_refs = [reference for reference in claim.evidence_refs
                          if evidence[reference].evidence_type != "PERIOD"]
            if not table_refs: continue
            columns: list[str] = []
            for reference in table_refs:
                value = evidence[reference].value
                for name in (value.keys() if isinstance(value, dict) else ["value"]):
                    if name not in columns: columns.append(name)
            if any(evidence[ref].group_value is not None for ref in table_refs):
                columns.insert(0, "group")
            tables.append(TableSpec(
                table_id=f"table-{claim_id.split(':')[1]}", title=f"Evidence for {claim_id}",
                claim_ids=[claim_id], evidence_refs=table_refs, columns=columns,
            ))
        return tables

    @staticmethod
    def _title(insight: BusinessInsight) -> str:
        metric = insight.claims[0].metric.replace("_", " ").title() if insight.claims and insight.claims[0].metric else "Business Analytics"
        return f"{metric} Performance Report"

    @staticmethod
    def _value_field(claim, insight: BusinessInsight) -> str:
        record = evidence_catalog(insight)[claim.evidence_refs[0]]
        if claim.claim_type == ClaimType.CONCENTRATION: return "share"
        if isinstance(record.value, dict):
            for name in ("contribution_percent", "absolute_change", claim.metric, "value"):
                if name and name in record.value: return name
        return "value"

    @staticmethod
    def _is_ranking(claim, insight: BusinessInsight) -> bool:
        record = evidence_catalog(insight)[claim.evidence_refs[0]]
        return record.result_type == "RANKING"
