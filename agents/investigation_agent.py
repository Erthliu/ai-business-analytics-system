"""Narrow planning agent for descriptive V5 investigations."""
from __future__ import annotations

from dataclasses import dataclass

from analytics.metric_registry import MetricRegistry
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import InvestigationCandidate, InvestigationMethod, InvestigationPlan, InvestigationType, MaterialityThresholds
from investigation.validator import InvestigationSemanticValidator, InvestigationValidationError, InvestigationValidationIssue
from profiling.llm import LLMProvider


class InvestigationProviderError(RuntimeError):
    """Controlled failure for an unsafe or unavailable provider proposal."""


@dataclass
class InvestigationAgent:
    """Select what to investigate; never calculate or modify V4 results."""

    provider: LLMProvider | None = None
    investigator_version: str = "5.0.0"
    prompt_version: str = "investigation-v1"

    def __post_init__(self) -> None:
        self.registry = MetricRegistry()
        self.validator = InvestigationSemanticValidator(self.registry)

    def build(self, request: AnalysisRequest, analysis_plan: AnalysisPlan, analysis: ComputedAnalysis,
              profile: DatasetProfile, *, selected_dimensions: list[str] | None = None,
              investigation_type: InvestigationType = InvestigationType.CHANGE_DECOMPOSITION,
              top_n: int = 5, materiality: MaterialityThresholds | None = None,
              use_provider: bool = False) -> InvestigationPlan:
        """Create a semantically validated deterministic or provider-assisted plan."""
        eligible = self.validator.eligible_dimensions(profile)
        if use_provider:
            candidate = self._candidate(analysis, eligible)
            investigation_type = candidate.investigation_type
            selected = candidate.selected_dimensions
            candidate_dimensions = candidate.candidate_dimensions
            methods, top_n, assumptions = candidate.methods, candidate.top_n, candidate.assumptions
            provider_name, model_name = self.provider.name, self.provider.model_name
        else:
            selected = selected_dimensions or eligible[:2]
            candidate_dimensions = eligible
            methods = [InvestigationMethod.GROUP_CHANGE, InvestigationMethod.RECONCILIATION,
                       InvestigationMethod.CONTRIBUTION_CONCENTRATION, InvestigationMethod.DIMENSION_DIAGNOSTICS]
            assumptions = ["Associations are descriptive and do not establish causality."]
            provider_name = model_name = None
        if analysis_plan.analysis_period is None or analysis_plan.comparison_period is None:
            raise InvestigationValidationError([InvestigationValidationIssue("MISSING_PERIOD", "V4 analysis requires both periods.")])
        plan = InvestigationPlan(
            request_id=request.request_id, analysis_plan_id=analysis_plan.plan_id, analysis_id=analysis.analysis_id,
            dataset_version_id=analysis.dataset_version_id, profile_id=profile.profile_id,
            investigation_type=investigation_type, target_metric=analysis.primary_metric,
            analysis_period=analysis_plan.analysis_period, comparison_period=analysis_plan.comparison_period,
            candidate_dimensions=candidate_dimensions, selected_dimensions=selected,
            filters=analysis_plan.filters, methods=methods, top_n=top_n,
            materiality=materiality or MaterialityThresholds(), assumptions=assumptions,
            limitations=["Descriptive decomposition does not establish causality."],
            investigator_version=self.investigator_version, prompt_version=self.prompt_version,
            provider_name=provider_name, model_name=model_name, metric_registry_version=self.registry.version,
        )
        try:
            return self.validator.validate(plan, request, analysis_plan, analysis, profile)
        except InvestigationValidationError as error:
            if use_provider:
                raise InvestigationProviderError(f"Provider proposal was rejected: {error}") from error
            raise

    def _candidate(self, analysis: ComputedAnalysis, eligible: list[str]) -> InvestigationCandidate:
        if self.provider is None:
            raise InvestigationProviderError("No investigation provider is configured.")
        try:
            raw = self.provider.generate_structured({"analysis_id": analysis.analysis_id,
                                                     "metric": analysis.primary_metric,
                                                     "eligible_dimensions": eligible})
            return InvestigationCandidate.model_validate(raw)
        except Exception as error:
            raise InvestigationProviderError(f"Investigation provider failed safely: {type(error).__name__}.") from error
