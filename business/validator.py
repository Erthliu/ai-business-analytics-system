"""Authoritative validation for evidence-bound business interpretation."""
from __future__ import annotations

import math
import re

from analytics.metric_registry import MetricRegistry
from business.evidence import EvidenceBinder, EvidenceBindingError
from contracts.business import BusinessInsight, BusinessInsightStatus, EvidenceRecord, ProviderBusinessInterpretation
from contracts.critic import CriticReview, ReviewStatus


class BusinessInterpretationError(ValueError):
    """Raised when a business interpretation is unsafe or unsupported."""


_CAUSAL = re.compile(r"\b(caused?|because|due to|led to|resulted in|drove business)\b", re.IGNORECASE)
_RECOMMENDATION = re.compile(r"\b(increase|decrease|cut|reduce|raise|invest|target|discontinue)\b.{0,30}\b(marketing|spend|price|prices|customers?|product|budget)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?![\w.])")


def required_caveats(review: CriticReview) -> list[str]:
    """Return every upstream warning and non-blocking issue that must remain visible."""
    return list(dict.fromkeys([*review.warnings, *[item.message for item in review.issues if not item.blocking]]))


def _numeric_values(value: object):
    if isinstance(value, dict):
        for item in value.values(): yield from _numeric_values(item)
    elif isinstance(value, list):
        for item in value: yield from _numeric_values(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield float(value)


class BusinessInterpretationValidator:
    """Enforce critic gate, evidence binding, numeric fidelity, and safe language."""

    def __init__(self) -> None:
        self.registry = MetricRegistry()

    def validate_provider(self, candidate: ProviderBusinessInterpretation,
                          catalog: dict[str, EvidenceRecord]) -> None:
        """Reject unsafe or numerically unsupported provider suggestions before use."""
        evidence = EvidenceBinder.bind(candidate.claims, catalog)
        known_caveats = {
            record.value if isinstance(record.value, str) else record.value.get("message")
            for record in catalog.values()
            if record.evidence_type in {"QA_WARNING", "QA_ISSUE"}
        }
        if any(caveat not in known_caveats for caveat in candidate.caveats):
            raise BusinessInterpretationError("Provider introduced an unbound external caveat.")
        if any(not question.strip().endswith("?") for question in candidate.unanswered_questions):
            raise BusinessInterpretationError("Provider unanswered items must remain explicit questions.")
        texts = [item for item in [candidate.headline, candidate.executive_summary,
                                   *candidate.caveats, *candidate.unanswered_questions] if item]
        for text in texts:
            if _CAUSAL.search(text): raise BusinessInterpretationError("Provider used unsupported causal language.")
            if _RECOMMENDATION.search(text): raise BusinessInterpretationError("Provider generated a recommendation.")
        allowed = [number for record in evidence for number in _numeric_values(record.value)]
        for text in [item for item in [candidate.headline, candidate.executive_summary] if item]:
            without_dates = re.sub(r"\b\d{4}(?:-\d{2}(?:-\d{2})?)?\b", "", text)
            for token in _NUMBER.findall(without_dates):
                number = float(token.replace(",", ""))
                if not any(math.isclose(abs(number), abs(value), rel_tol=1e-9, abs_tol=0.005) for value in allowed):
                    raise BusinessInterpretationError(f"Provider invented numeric value: {token}")

    def validate(self, insight: BusinessInsight, catalog: dict[str, EvidenceRecord], review: CriticReview) -> BusinessInsight:
        if review.overall_status == ReviewStatus.FAIL or not review.downstream_eligible:
            raise BusinessInterpretationError("CriticReview FAIL blocks business interpretation.")
        if insight.critic_review_id != review.review_id or insight.analysis_id != review.analysis_id:
            raise BusinessInterpretationError("BusinessInsight provenance does not match CriticReview.")
        expected_status = BusinessInsightStatus.COMPLETE_WITH_CAVEATS if insight.caveats else BusinessInsightStatus.COMPLETE
        if insight.status != expected_status:
            raise BusinessInterpretationError("BusinessInsight status does not reflect upstream caveats.")
        missing = [item for item in required_caveats(review) if item not in insight.caveats]
        if missing:
            raise BusinessInterpretationError(f"Required QA caveats were suppressed: {missing}")
        try:
            EvidenceBinder.bind(insight.claims, catalog)
        except EvidenceBindingError as error:
            raise BusinessInterpretationError(str(error)) from error
        insight_refs = {reference for claim in insight.claims for reference in claim.evidence_refs}
        for item in insight.key_insights:
            if not set(item.evidence_refs) <= set(catalog):
                raise BusinessInterpretationError("Key insight contains unknown evidence references.")
            if not set(item.evidence_refs) <= insight_refs:
                raise BusinessInterpretationError("Key insight is not backed by a declared structured claim.")
        for claim in insight.claims:
            if claim.metric:
                try: self.registry.get(claim.metric)
                except ValueError as error: raise BusinessInterpretationError(str(error)) from error
                if any(catalog[ref].metric not in {None, claim.metric} for ref in claim.evidence_refs):
                    raise BusinessInterpretationError("Claim metric does not match referenced evidence.")
            if claim.dimension and any(catalog[ref].dimension not in {None, claim.dimension} for ref in claim.evidence_refs):
                raise BusinessInterpretationError("Claim dimension does not match referenced evidence.")
        texts = [insight.headline, insight.executive_summary,
                 *[item.statement for item in insight.key_insights], *insight.caveats,
                 *insight.business_context_assumptions, *insight.unanswered_questions]
        for text in texts:
            if _CAUSAL.search(text): raise BusinessInterpretationError("Unsupported causal language is forbidden.")
            if _RECOMMENDATION.search(text): raise BusinessInterpretationError("Operational recommendation language is forbidden.")
        evidence_values = [number for record in insight.evidence for number in _numeric_values(record.value)]
        for text in [insight.headline, insight.executive_summary, *[item.statement for item in insight.key_insights]]:
            without_dates = re.sub(r"\b\d{4}(?:-\d{2}(?:-\d{2})?)?\b", "", text)
            for token in _NUMBER.findall(without_dates):
                number = float(token.replace(",", ""))
                if not any(math.isclose(abs(number), abs(value), rel_tol=1e-9, abs_tol=0.005) for value in evidence_values):
                    raise BusinessInterpretationError(f"Prose contains unsupported numeric value: {token}")
        for item in insight.key_insights:
            allowed = [number for reference in item.evidence_refs for number in _numeric_values(catalog[reference].value)]
            for token in _NUMBER.findall(re.sub(r"\b\d{4}(?:-\d{2}(?:-\d{2})?)?\b", "", item.statement)):
                number = float(token.replace(",", ""))
                if not any(math.isclose(abs(number), abs(value), rel_tol=1e-9, abs_tol=0.005) for value in allowed):
                    raise BusinessInterpretationError(f"Key insight contains unsupported numeric value: {token}")
        return insight
