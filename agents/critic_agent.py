"""V6 quality-control agent with deterministic decisions."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from time import perf_counter

from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from contracts.computed_analysis import ComputedAnalysis
from contracts.critic import (
    CriticReview, EvidenceSummary, ProviderCritique, ReviewConfidence,
    ReviewStatus, Severity,
)
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import InvestigationPlan, InvestigationResult
from profiling.llm import LLMProvider
from qa import QA_RULES_VERSION
from qa.check_engine import QACheckEngine

logger = logging.getLogger(__name__)
_SEVERITY = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}


@dataclass
class CriticAgent:
    """Classify artifact quality without altering upstream artifacts."""

    provider: LLMProvider | None = None
    critic_version: str = "6.0.0"
    prompt_version: str = "critic-v1"
    qa_rules_version: str = QA_RULES_VERSION

    def __post_init__(self) -> None:
        self.engine = QACheckEngine()

    def review(self, request: AnalysisRequest, plan: AnalysisPlan, analysis: ComputedAnalysis,
               profile: DatasetProfile, source_hash: str,
               investigation_plan: InvestigationPlan | None = None,
               investigation: InvestigationResult | None = None) -> CriticReview:
        """Run deterministic checks, then optional non-authoritative critique."""
        started = perf_counter()
        checks, issues, warnings = self.engine.run(request, plan, analysis, profile, source_hash,
                                                   investigation_plan, investigation)
        blocking = [item for item in issues if item.blocking and _SEVERITY[item.severity] >= _SEVERITY[Severity.MEDIUM]]
        if blocking:
            status = ReviewStatus.FAIL
        elif issues or warnings:
            status = ReviewStatus.PASS_WITH_WARNINGS
        else:
            status = ReviewStatus.PASS
        severity = max((item.severity for item in issues), key=_SEVERITY.get, default=Severity.INFO)
        provider_findings = []
        provider_name = model_name = None
        provider_attempted = self.provider is not None
        provider_succeeded = False
        confidence = ReviewConfidence.HIGH
        if self.provider is not None:
            provider_name, model_name = self.provider.name, self.provider.model_name
            try:
                raw = self.provider.generate_structured({
                    "artifact_ids": [request.request_id, plan.plan_id, analysis.analysis_id,
                                     *([investigation.investigation_id] if investigation else [])],
                    "deterministic_checks": [item.model_dump(mode="json") for item in checks],
                    "deterministic_issue_codes": [item.issue_code for item in issues],
                    "deterministic_status": status,
                })
                critique = ProviderCritique.model_validate(raw)
                valid_ids = {request.request_id, plan.plan_id, analysis.analysis_id,
                             *([investigation_plan.investigation_plan_id] if investigation_plan else []),
                             *([investigation.investigation_id] if investigation else [])}
                valid_references = valid_ids | {item.check_name for item in checks} | {item.issue_code for item in issues}
                if any(item.artifact_id not in valid_ids or
                       (item.evidence_reference is not None and item.evidence_reference not in valid_references)
                       for item in critique.issues):
                    raise ValueError("provider referenced an unknown artifact")
                provider_findings = critique.issues
                provider_succeeded = True
            except Exception as error:
                warnings.append(f"Optional semantic critique unavailable ({type(error).__name__}).")
                confidence = ReviewConfidence.MEDIUM
                if status == ReviewStatus.PASS:
                    status = ReviewStatus.PASS_WITH_WARNINGS
        artifacts = [request.request_id, plan.plan_id, analysis.analysis_id]
        if investigation_plan: artifacts.append(investigation_plan.investigation_plan_id)
        if investigation: artifacts.append(investigation.investigation_id)
        review = CriticReview(
            request_id=request.request_id, analysis_plan_id=plan.plan_id, analysis_id=analysis.analysis_id,
            investigation_plan_id=investigation_plan.investigation_plan_id if investigation_plan else None,
            investigation_id=investigation.investigation_id if investigation else None,
            dataset_version_id=analysis.dataset_version_id, profile_id=profile.profile_id,
            overall_status=status, severity=severity, deterministic_checks=checks, issues=issues,
            warnings=warnings, passed_checks=[item.check_name for item in checks if item.status == "PASS"],
            evidence_summary=EvidenceSummary(
                reviewed_artifact_ids=artifacts, deterministic_check_count=len(checks),
                passed_check_count=sum(item.status == "PASS" for item in checks), issue_count=len(issues),
                investigation_included=investigation is not None,
            ),
            review_confidence=confidence, provider_findings=provider_findings,
            critic_version=self.critic_version, qa_rules_version=self.qa_rules_version,
            prompt_version=self.prompt_version, provider_name=provider_name, model_name=model_name,
        )
        logger.info("Critic review %s analysis=%s investigation=%s dataset=%s critic=%s rules=%s status=%s severity=%s checks=%d provider_attempted=%s provider_succeeded=%s elapsed=%.3fs",
                    review.review_id, analysis.analysis_id, review.investigation_id,
                    review.dataset_version_id, self.critic_version, self.qa_rules_version,
                    review.overall_status, review.severity, len(checks), provider_attempted,
                    provider_succeeded, perf_counter() - started)
        return review
