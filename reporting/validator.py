"""Authoritative validation for V8 report specifications and artifacts."""
from __future__ import annotations

import re

from contracts.business import BusinessInsight, BusinessInsightStatus
from contracts.critic import CriticReview, ReviewStatus
from contracts.reporting import ProviderReportProposal, ReportArtifact, ReportSpec, ReportStatus
from reporting.data import build_chart, build_table, claim_catalog, eligible_chart_types, evidence_catalog


class ReportValidationError(ValueError):
    """Raised when a report could introduce or obscure analytical truth."""


_CAUSAL = re.compile(r"\b(caused?|because|due to|led to|resulted in|drove business)\b", re.IGNORECASE)
_RECOMMENDATION = re.compile(r"\b(increase|decrease|cut|reduce|raise|invest|target|discontinue)\b.{0,30}\b(marketing|spend|price|prices|customers?|product|budget)\b", re.IGNORECASE)


def ensure_eligible(insight: BusinessInsight, review: CriticReview) -> None:
    """Enforce the persisted V7 and V6 gates."""
    if insight.status not in {BusinessInsightStatus.COMPLETE, BusinessInsightStatus.COMPLETE_WITH_CAVEATS}:
        raise ReportValidationError("BusinessInsight BLOCKED cannot produce a report.")
    if review.overall_status == ReviewStatus.FAIL or not review.downstream_eligible:
        raise ReportValidationError("CriticReview FAIL blocks report generation.")
    if insight.critic_review_id != review.review_id:
        raise ReportValidationError("BusinessInsight does not reference the supplied CriticReview.")


class ReportValidator:
    """Validate report structure, traceability, factual text, and derived displays."""

    def validate_proposal(self, proposal: ProviderReportProposal, insight: BusinessInsight,
                          available_sections: list[str]) -> None:
        claims = claim_catalog(insight)
        if len(proposal.section_order) != len(set(proposal.section_order)) or set(proposal.section_order) != set(available_sections):
            raise ReportValidationError("Provider section order must contain each available section exactly once.")
        if any(claim_id not in claims for claim_id in proposal.prioritized_claim_ids):
            raise ReportValidationError("Provider invented a claim ID.")
        if not set(insight.caveats) <= set(proposal.included_caveats):
            raise ReportValidationError("Provider suppressed a mandatory caveat.")
        for preference in proposal.chart_preferences:
            claim = claims.get(preference.claim_id)
            if claim is None: raise ReportValidationError("Provider invented a chart claim ID.")
            if preference.chart_type.value not in eligible_chart_types(claim.claim_type):
                raise ReportValidationError("Provider selected an invalid chart type for the claim.")
        if proposal.report_title:
            self._validate_title(proposal.report_title, insight)

    def validate_spec(self, spec: ReportSpec, insight: BusinessInsight, review: CriticReview) -> None:
        ensure_eligible(insight, review)
        self._validate_provenance(spec, insight)
        claims, evidence = claim_catalog(insight), evidence_catalog(insight)
        if any(item not in claims for item in spec.included_claim_ids):
            raise ReportValidationError("ReportSpec contains an unknown claim ID.")
        if any(item not in evidence for item in spec.included_evidence_refs):
            raise ReportValidationError("ReportSpec contains an unknown evidence reference.")
        expected_included_evidence = {
            reference for claim_id in spec.included_claim_ids
            for reference in claims[claim_id].evidence_refs
        }
        if set(spec.included_evidence_refs) != expected_included_evidence:
            raise ReportValidationError("ReportSpec evidence does not match its included claims.")
        if not set(insight.caveats) <= set(spec.included_caveats):
            raise ReportValidationError("ReportSpec omitted a mandatory caveat.")
        if insight.caveats and not any(section.section_type == "QA_CAVEATS" for section in spec.sections):
            raise ReportValidationError("A visible QA caveat section is required.")
        for section in spec.sections:
            self._refs(section.claim_ids, section.evidence_refs, claims, evidence)
        for chart in spec.chart_specs:
            self._refs(chart.claim_ids, chart.evidence_refs, claims, evidence)
            if any(chart.chart_type.value not in eligible_chart_types(claims[item].claim_type) for item in chart.claim_ids):
                raise ReportValidationError("Chart type is incompatible with its claim.")
        for table in spec.table_specs:
            self._refs(table.claim_ids, table.evidence_refs, claims, evidence, exact=False)
        self._validate_title(spec.report_title, insight)

    def validate_artifact(self, artifact: ReportArtifact, spec: ReportSpec,
                          insight: BusinessInsight, review: CriticReview) -> None:
        self.validate_spec(spec, insight, review)
        if artifact.report_spec_id != spec.report_spec_id or artifact.business_insight_id != insight.insight_id:
            raise ReportValidationError("ReportArtifact provenance does not match its inputs.")
        if (artifact.critic_review_id != insight.critic_review_id or
                artifact.analysis_id != insight.analysis_id or
                artifact.investigation_id != insight.investigation_id or
                artifact.dataset_version_id != insight.dataset_version_id):
            raise ReportValidationError("ReportArtifact upstream provenance is invalid.")
        expected_status = ReportStatus.COMPLETE_WITH_CAVEATS if insight.caveats else ReportStatus.COMPLETE
        if artifact.status != expected_status:
            raise ReportValidationError("Report status does not reflect upstream caveats.")
        if artifact.caveats != insight.caveats:
            raise ReportValidationError("ReportArtifact did not preserve mandatory caveats.")
        claims, evidence = claim_catalog(insight), evidence_catalog(insight)
        if [(item.section_id, item.section_type, item.title) for item in artifact.rendered_sections] != [
            (item.section_id, item.section_type, item.title) for item in spec.sections
        ]:
            raise ReportValidationError("Rendered sections do not match ReportSpec.")
        allowed_text = {insight.headline, insight.executive_summary,
                        *[item.statement for item in insight.key_insights]}
        for section in artifact.rendered_sections:
            for statement in section.statements:
                self._refs(statement.claim_ids, statement.evidence_refs, claims, evidence)
                if statement.text not in allowed_text:
                    raise ReportValidationError("Rendered report contains orphan factual prose.")
        expected_charts = [build_chart(item, insight) for item in spec.chart_specs]
        if artifact.charts != expected_charts:
            raise ReportValidationError("Rendered chart data does not match approved evidence.")
        expected_tables = [build_table(item, insight) for item in spec.table_specs]
        if artifact.tables != expected_tables:
            raise ReportValidationError("Rendered table data does not match approved evidence.")
        expected_provenance = {claim_id: claim.evidence_refs for claim_id, claim in claims.items()}
        if artifact.provenance.claim_evidence != expected_provenance:
            raise ReportValidationError("Claim-to-evidence provenance is invalid.")
        provenance_values = (
            artifact.provenance.business_insight_id == insight.insight_id,
            artifact.provenance.critic_review_id == insight.critic_review_id,
            artifact.provenance.analysis_id == insight.analysis_id,
            artifact.provenance.investigation_id == insight.investigation_id,
            artifact.provenance.dataset_version_id == insight.dataset_version_id,
        )
        if not all(provenance_values):
            raise ReportValidationError("Embedded report provenance is invalid.")
        texts = [artifact.title, *artifact.caveats, *artifact.unanswered_questions,
                 *[statement.text for section in artifact.rendered_sections for statement in section.statements]]
        for text in texts:
            if _CAUSAL.search(text): raise ReportValidationError("Unsupported causal language is forbidden.")
            if _RECOMMENDATION.search(text): raise ReportValidationError("Recommendation language is forbidden.")

    @staticmethod
    def _refs(claim_ids, evidence_refs, claims, evidence, *, exact=True) -> None:
        if any(item not in claims for item in claim_ids): raise ReportValidationError("Unknown claim reference.")
        if any(item not in evidence for item in evidence_refs): raise ReportValidationError("Unknown evidence reference.")
        permitted = {ref for item in claim_ids for ref in claims[item].evidence_refs}
        matches = set(evidence_refs) == permitted if exact else set(evidence_refs) <= permitted
        if not matches:
            raise ReportValidationError("Evidence does not belong to the referenced claim.")

    @staticmethod
    def _validate_provenance(spec: ReportSpec, insight: BusinessInsight) -> None:
        values = (spec.business_insight_id == insight.insight_id,
                  spec.critic_review_id == insight.critic_review_id,
                  spec.analysis_id == insight.analysis_id,
                  spec.investigation_id == insight.investigation_id,
                  spec.dataset_version_id == insight.dataset_version_id)
        if not all(values): raise ReportValidationError("ReportSpec provenance does not match BusinessInsight.")

    @staticmethod
    def _validate_title(title: str, insight: BusinessInsight) -> None:
        if re.search(r"\d", title): raise ReportValidationError("Report title cannot introduce numeric facts.")
        if _CAUSAL.search(title) or _RECOMMENDATION.search(title):
            raise ReportValidationError("Unsafe report title.")
        allowed = set(re.findall(r"[a-z]+", insight.headline.lower())) | {
            "business", "analytics", "analysis", "report", "performance", "summary", "executive"
        }
        if not set(re.findall(r"[a-z]+", title.lower())) <= allowed:
            raise ReportValidationError("Report title contains unsupported external context.")
