"""Deterministic V9 investigation routing; no provider decisions."""
from dataclasses import dataclass

from agents.investigation_agent import InvestigationAgent
from analytics.metric_registry import MetricAdditivity, MetricRegistry
from contracts.computed_analysis import ComputedAnalysis, ResultType
from contracts.dataset_profile import DatasetProfile
from contracts.orchestration import InvestigationPolicy, InvestigationRoutingConfig


class InvestigationRoutingError(ValueError):
    """Raised when REQUIRED investigation is unsupported."""


@dataclass(frozen=True, slots=True)
class InvestigationDecision:
    run: bool
    reason: str
    dimensions: list[str]


class InvestigationRouter:
    """Apply versioned structural and threshold rules to V5 routing."""

    def __init__(self) -> None:
        self.registry = MetricRegistry()
        self.investigation_agent = InvestigationAgent()

    def decide(self, analysis: ComputedAnalysis, profile: DatasetProfile,
               policy: InvestigationPolicy,
               config: InvestigationRoutingConfig) -> InvestigationDecision:
        """Choose V5 only for supported scalar period comparisons."""
        dimensions = self.investigation_agent.validator.eligible_dimensions(profile)
        metric = self.registry.get(analysis.primary_metric)
        structural = (
            analysis.result_type == ResultType.PERIOD_COMPARISON and
            metric.additivity == MetricAdditivity.ADDITIVE and bool(dimensions) and
            bool(analysis.rows)
        )
        if policy == InvestigationPolicy.NEVER:
            return InvestigationDecision(False, "Investigation policy is NEVER.", dimensions)
        if policy == InvestigationPolicy.REQUIRED:
            if not structural:
                raise InvestigationRoutingError(
                    "REQUIRED investigation is unsupported for this result, metric, or profile."
                )
            return InvestigationDecision(True, "Investigation policy is REQUIRED.", dimensions[:2])
        if not structural:
            return InvestigationDecision(False, "AUTO structural eligibility was not met.", dimensions)
        row = analysis.rows[0]
        absolute = row.get("absolute_change")
        percentage = row.get("percentage_change")
        meaningful = (
            absolute is not None and abs(absolute) > config.minimum_absolute_change and
            percentage is not None and abs(percentage) > config.minimum_percentage_change
        )
        if not meaningful:
            return InvestigationDecision(False, "AUTO change thresholds were not met.", dimensions)
        return InvestigationDecision(True, "AUTO investigation eligibility was met.", dimensions[:2])
