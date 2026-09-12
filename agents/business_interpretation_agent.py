"""V7 evidence-bound business interpretation with deterministic fallback."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from time import perf_counter
from urllib.parse import quote

from business import BUSINESS_RULES_VERSION
from business.evidence import EvidenceBinder
from business.renderer import BusinessRenderer
from business.validator import BusinessInterpretationError, BusinessInterpretationValidator, required_caveats
from contracts.business import (
    BusinessClaim, BusinessInsight, BusinessInsightStatus, ClaimType,
    Importance, InsightConfidence, KeyInsight, ProviderBusinessInterpretation,
)
from contracts.computed_analysis import ComputedAnalysis, ResultType
from contracts.critic import CriticReview, ReviewStatus
from contracts.investigation import ContributionDirection, InvestigationResult, InvestigationType
from profiling.llm import LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class BusinessInterpretationAgent:
    """Select evidence-bound claims and deterministically render their facts."""

    provider: LLMProvider | None = None
    agent_version: str = "7.0.0"
    prompt_version: str = "business-v1"
    business_rules_version: str = BUSINESS_RULES_VERSION

    def __post_init__(self) -> None:
        self.binder = EvidenceBinder()
        self.renderer = BusinessRenderer()
        self.validator = BusinessInterpretationValidator()

    def interpret(self, analysis: ComputedAnalysis, review: CriticReview,
                  investigation: InvestigationResult | None = None,
                  *, use_provider: bool = False) -> BusinessInsight:
        """Generate an insight only from a QA-approved structured artifact chain."""
        started = perf_counter()
        if review.overall_status == ReviewStatus.FAIL or not review.downstream_eligible:
            raise BusinessInterpretationError("CriticReview FAIL blocks business interpretation.")
        if review.analysis_id != analysis.analysis_id or review.dataset_version_id != analysis.dataset_version_id:
            raise BusinessInterpretationError("CriticReview and analysis provenance do not match.")
        if bool(review.investigation_id) != bool(investigation) or (investigation and review.investigation_id != investigation.investigation_id):
            raise BusinessInterpretationError("CriticReview and investigation provenance do not match.")
        catalog = self.binder.build(analysis, review, investigation)
        provider_attempted = use_provider
        provider_succeeded = False
        provider_caveats: list[str] = []
        unanswered = ["What additional validated evidence would further explain the observed pattern?"]
        claims = self._default_claims(analysis, investigation, catalog)
        provider_name = model_name = None
        if use_provider:
            if self.provider is None:
                provider_caveats.append("Optional business interpretation provider unavailable; deterministic rendering used.")
            else:
                provider_name, model_name = self.provider.name, self.provider.model_name
                try:
                    candidate = ProviderBusinessInterpretation.model_validate(self.provider.generate_structured({
                        "critic_status": review.overall_status,
                        "evidence": [item.model_dump(mode="json") for item in catalog.values()],
                        "allowed_claim_types": [item.value for item in ClaimType],
                    }))
                    self.validator.validate_provider(candidate, catalog)
                    claims = candidate.claims
                    provider_caveats.extend(candidate.caveats)
                    unanswered = candidate.unanswered_questions or unanswered
                    provider_succeeded = True
                except Exception as error:
                    provider_caveats.append(f"Optional business interpretation unavailable ({type(error).__name__}); deterministic rendering used.")
        evidence = self.binder.bind(claims, catalog)
        statements = [self.renderer.render(claim, catalog, analysis) for claim in claims]
        upstream = required_caveats(review)
        caveats = list(dict.fromkeys([*upstream, *provider_caveats]))
        status = BusinessInsightStatus.COMPLETE_WITH_CAVEATS if caveats else BusinessInsightStatus.COMPLETE
        key_insights = [KeyInsight(
            insight_code=f"INSIGHT_{index}", statement=statement,
            importance=Importance.HIGH if index == 1 else Importance.MEDIUM,
            evidence_refs=claim.evidence_refs,
            confidence=InsightConfidence.HIGH if review.review_confidence == "HIGH" else InsightConfidence.MEDIUM,
            caveats=upstream,
        ) for index, (claim, statement) in enumerate(zip(claims, statements), start=1)]
        headline = statements[0] if not caveats else f"{statements[0]} Review caveats apply."
        summary = " ".join(statements[:3])
        if caveats:
            summary = f"{summary} Review caveats apply; see the structured caveats."
        insight = BusinessInsight(
            request_id=review.request_id, analysis_id=analysis.analysis_id,
            investigation_id=investigation.investigation_id if investigation else None,
            critic_review_id=review.review_id, dataset_version_id=analysis.dataset_version_id,
            status=status, headline=headline, executive_summary=summary,
            claims=claims, key_insights=key_insights, evidence=evidence, caveats=caveats,
            business_context_assumptions=["Interpretation is limited to validated structured artifacts."],
            unanswered_questions=unanswered, agent_version=self.agent_version,
            business_rules_version=self.business_rules_version, prompt_version=self.prompt_version,
            provider_name=provider_name, model_name=model_name,
        )
        self.validator.validate(insight, catalog, review)
        logger.info("Business insight %s review=%s analysis=%s investigation=%s dataset=%s agent=%s rules=%s provider_attempted=%s provider_succeeded=%s claims=%d caveats=%d elapsed=%.3fs",
                    insight.insight_id, review.review_id, analysis.analysis_id, insight.investigation_id,
                    insight.dataset_version_id, self.agent_version, self.business_rules_version,
                    provider_attempted, provider_succeeded, len(claims), len(caveats), perf_counter() - started)
        return insight

    def _default_claims(self, analysis: ComputedAnalysis, investigation: InvestigationResult | None,
                        catalog: dict) -> list[BusinessClaim]:
        prefix = f"analysis:{analysis.analysis_id}"
        metric = analysis.primary_metric
        if analysis.result_type == ResultType.SCALAR:
            claims = [BusinessClaim(claim_type=ClaimType.METRIC_VALUE, metric=metric,
                                    evidence_refs=[f"{prefix}:summary:{metric}"])]
        elif analysis.result_type == ResultType.PERIOD_COMPARISON:
            refs = [f"{prefix}:row:0"]
            refs.extend(ref for ref in (f"{prefix}:period:analysis", f"{prefix}:period:comparison") if ref in catalog)
            claims = [BusinessClaim(claim_type=ClaimType.METRIC_CHANGE, metric=metric, evidence_refs=refs)]
        elif analysis.result_type in {ResultType.TABLE, ResultType.GROUPED_PERIOD_COMPARISON}:
            index = max(range(len(analysis.rows)), key=lambda i: abs(analysis.rows[i].get("absolute_change") or 0))
            record = catalog[f"{prefix}:row:{index}"]
            refs = [record.evidence_ref, *[
                f"{prefix}:row:{row_index}" for row_index in range(len(analysis.rows)) if row_index != index
            ]]
            claims = [BusinessClaim(claim_type=ClaimType.TOP_CONTRIBUTOR, metric=metric,
                                    dimension=record.dimension, evidence_refs=refs)]
        elif analysis.result_type == ResultType.TIME_SERIES:
            claims = [BusinessClaim(claim_type=ClaimType.TREND_DIRECTION, metric=metric,
                                    evidence_refs=[f"{prefix}:row:{index}" for index in range(len(analysis.rows))])]
        elif analysis.result_type == ResultType.RANKING:
            record = catalog[f"{prefix}:row:0"]
            claims = [BusinessClaim(claim_type=ClaimType.TOP_CONTRIBUTOR, metric=metric,
                                    dimension=record.dimension,
                                    evidence_refs=[f"{prefix}:row:{index}" for index in range(len(analysis.rows))])]
        else:
            record = catalog[f"{prefix}:row:0"]
            claims = [BusinessClaim(claim_type=ClaimType.METRIC_VALUE, metric=metric, evidence_refs=[record.evidence_ref])]
        if investigation and investigation.investigation_type in {
            InvestigationType.CHANGE_DECOMPOSITION, InvestigationType.CONTRIBUTION_ANALYSIS,
            InvestigationType.CONCENTRATION_ANALYSIS, InvestigationType.DIMENSION_SCAN,
        }:
            groups = [group for dimension in investigation.dimension_results for group in dimension.groups]
            driver = next((group for group in groups if group.contribution_direction == ContributionDirection.DRIVES_CHANGE), None)
            offset = next((group for group in groups if group.contribution_direction == ContributionDirection.OFFSETS_CHANGE), None)
            inv_prefix = f"investigation:{investigation.investigation_id}"
            if driver:
                driver_refs = [
                    f"{inv_prefix}:dimension:{group.dimension}:{quote(str(group.value), safe='')}"
                    for group in groups if group.dimension == driver.dimension
                ]
                claims.append(BusinessClaim(claim_type=ClaimType.TOP_CONTRIBUTOR, metric=metric,
                    dimension=driver.dimension, evidence_refs=driver_refs))
            if offset:
                claims.append(BusinessClaim(claim_type=ClaimType.OFFSETTING_CONTRIBUTOR, metric=metric,
                    dimension=offset.dimension, evidence_refs=[f"{inv_prefix}:dimension:{offset.dimension}:{quote(str(offset.value), safe='')}"]))
            if investigation.concentration_metrics:
                dimension = investigation.concentration_metrics[0].dimension
                claims.append(BusinessClaim(claim_type=ClaimType.CONCENTRATION, metric=metric, dimension=dimension,
                                            evidence_refs=[f"{inv_prefix}:concentration:{dimension}"]))
        return claims
