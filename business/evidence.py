"""Deterministic evidence catalog construction and reference validation."""
from __future__ import annotations

from urllib.parse import quote

from contracts.computed_analysis import ComputedAnalysis
from contracts.critic import CriticReview
from contracts.business import BusinessClaim, EvidenceRecord
from contracts.investigation import InvestigationResult


class EvidenceBindingError(ValueError):
    """Raised when a business claim references nonexistent structured evidence."""


class EvidenceBinder:
    """Build stable evidence references from approved structured artifacts."""

    def build(self, analysis: ComputedAnalysis, review: CriticReview,
              investigation: InvestigationResult | None = None) -> dict[str, EvidenceRecord]:
        records: list[EvidenceRecord] = []
        prefix = f"analysis:{analysis.analysis_id}"
        for name, value in analysis.summary_metrics.items():
            records.append(EvidenceRecord(evidence_ref=f"{prefix}:summary:{name}", artifact_type="ComputedAnalysis",
                                          artifact_id=analysis.analysis_id, evidence_type="SUMMARY_METRIC",
                                          result_type=analysis.result_type, metric=name, value=value))
        for index, row in enumerate(analysis.rows):
            dimension_fields = [name for name in row if name not in {analysis.primary_metric, "value", "period", "analysis_period_value", "comparison_period_value", "absolute_change", "percentage_change"}]
            dimension = dimension_fields[0] if dimension_fields else None
            records.append(EvidenceRecord(evidence_ref=f"{prefix}:row:{index}", artifact_type="ComputedAnalysis",
                                          artifact_id=analysis.analysis_id, evidence_type="RESULT_ROW",
                                          result_type=analysis.result_type,
                                          metric=analysis.primary_metric, dimension=dimension,
                                          group_value=row.get(dimension) if dimension else None, value=row))
            for field, value in row.items():
                records.append(EvidenceRecord(evidence_ref=f"{prefix}:row:{index}:{field}", artifact_type="ComputedAnalysis",
                                              artifact_id=analysis.analysis_id, evidence_type="RESULT_FIELD",
                                              result_type=analysis.result_type,
                                              metric=analysis.primary_metric, value=value))
        for name, period in (("analysis", analysis.analysis_period), ("comparison", analysis.comparison_period)):
            if period:
                records.append(EvidenceRecord(evidence_ref=f"{prefix}:period:{name}", artifact_type="ComputedAnalysis",
                                              artifact_id=analysis.analysis_id, evidence_type="PERIOD",
                                              result_type=analysis.result_type,
                                              metric=analysis.primary_metric, value=period.model_dump(mode="json")))
        if investigation:
            inv_prefix = f"investigation:{investigation.investigation_id}"
            for dimension in investigation.dimension_results:
                for group in dimension.groups:
                    value = quote(str(group.value), safe="")
                    records.append(EvidenceRecord(
                        evidence_ref=f"{inv_prefix}:dimension:{dimension.dimension}:{value}",
                        artifact_type="InvestigationResult", artifact_id=investigation.investigation_id,
                        evidence_type="GROUP_RESULT", metric=investigation.target_metric,
                        dimension=dimension.dimension, group_value=group.value,
                        value=group.model_dump(mode="json"),
                    ))
            for concentration in investigation.concentration_metrics:
                records.append(EvidenceRecord(
                    evidence_ref=f"{inv_prefix}:concentration:{concentration.dimension}",
                    artifact_type="InvestigationResult", artifact_id=investigation.investigation_id,
                    evidence_type="CONCENTRATION", metric=investigation.target_metric,
                    dimension=concentration.dimension, value=concentration.model_dump(mode="json"),
                ))
            for reconciliation in investigation.reconciliation:
                records.append(EvidenceRecord(
                    evidence_ref=f"{inv_prefix}:reconciliation:{reconciliation.dimension}",
                    artifact_type="InvestigationResult", artifact_id=investigation.investigation_id,
                    evidence_type="RECONCILIATION", metric=investigation.target_metric,
                    dimension=reconciliation.dimension, value=reconciliation.model_dump(mode="json"),
                ))
        review_prefix = f"review:{review.review_id}"
        for index, warning in enumerate(review.warnings):
            records.append(EvidenceRecord(evidence_ref=f"{review_prefix}:warning:{index}", artifact_type="CriticReview",
                                          artifact_id=review.review_id, evidence_type="QA_WARNING", value=warning))
        for issue in review.issues:
            records.append(EvidenceRecord(evidence_ref=f"{review_prefix}:issue:{issue.issue_code}", artifact_type="CriticReview",
                                          artifact_id=review.review_id, evidence_type="QA_ISSUE", value=issue.model_dump(mode="json")))
        return {record.evidence_ref: record for record in records}

    @staticmethod
    def bind(claims: list[BusinessClaim], catalog: dict[str, EvidenceRecord]) -> list[EvidenceRecord]:
        refs = []
        for claim in claims:
            for reference in claim.evidence_refs:
                if reference not in catalog:
                    raise EvidenceBindingError(f"Unknown evidence reference: {reference}")
                refs.append(reference)
        return [catalog[reference] for reference in dict.fromkeys(refs)]
