"""Deterministic descriptive contribution and concentration engine."""
from __future__ import annotations

import hashlib
import logging
import math
from time import perf_counter

import pandas as pd

from agents.analytics_engine import AnalyticsEngine
from analytics.metric_registry import MetricRegistry
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import (
    ConcentrationMetrics, ContributionDirection, DimensionInvestigationResult,
    GroupInvestigationResult, InvestigationPlan, InvestigationResult,
    InvestigationStatus, ReconciliationResult,
)
from investigation.validator import InvestigationSemanticValidator

logger = logging.getLogger(__name__)


class InvestigationExecutionError(RuntimeError):
    """Raised when source data cannot support an honest investigation."""


class InvestigationEngine:
    """Compute additive decompositions from immutable V4 inputs."""

    engine_version = "5.0.0"
    relative_tolerance = 1e-9

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()
        self.validator = InvestigationSemanticValidator(self.registry)
        self.analytics = AnalyticsEngine(self.registry)

    @staticmethod
    def _direction(group_change: float, total_change: float) -> ContributionDirection:
        if group_change == 0 or total_change == 0:
            return ContributionDirection.NEUTRAL
        return ContributionDirection.DRIVES_CHANGE if math.copysign(1, group_change) == math.copysign(1, total_change) else ContributionDirection.OFFSETS_CHANGE

    @staticmethod
    def _concentration(dimension: str, groups: list[GroupInvestigationResult]) -> ConcentrationMetrics:
        driving = sorted((abs(item.absolute_change) for item in groups
                          if item.material and item.contribution_direction == ContributionDirection.DRIVES_CHANGE), reverse=True)
        total = sum(driving)
        shares = [value / total * 100 for value in driving] if total else []
        cumulative: list[float] = []
        running = 0.0
        for share in shares:
            running += share
            cumulative.append(running)
        top = lambda count: sum(shares[:count])
        return ConcentrationMetrics(dimension=dimension, top_1_share=top(1), top_3_share=top(3),
                                    top_5_share=top(5), cumulative_contribution_share=cumulative)

    def execute(self, dataframe: pd.DataFrame, plan: InvestigationPlan, request: AnalysisRequest,
                analysis_plan: AnalysisPlan, analysis: ComputedAnalysis, profile: DatasetProfile,
                source_hash: str) -> InvestigationResult:
        """Revalidate and calculate one deterministic descriptive investigation."""
        started = perf_counter()
        self.validator.validate(plan, request, analysis_plan, analysis, profile)
        frame = self.analytics._filter(dataframe.copy(deep=False), plan.filters)
        dates = pd.to_datetime(frame[analysis_plan.time_field], errors="raise")
        analysis_frame = frame[(dates.dt.date >= plan.analysis_period.start) & (dates.dt.date <= plan.analysis_period.end)]
        comparison_frame = frame[(dates.dt.date >= plan.comparison_period.start) & (dates.dt.date <= plan.comparison_period.end)]
        if analysis_frame.empty:
            raise InvestigationExecutionError("Analysis period contains no rows.")
        if comparison_frame.empty:
            raise InvestigationExecutionError("Comparison period contains no rows.")
        source_row = analysis.rows[0] if analysis.rows else {}
        expected = source_row.get("absolute_change")
        if expected is None:
            raise InvestigationExecutionError("Source V4 analysis has no total absolute change.")
        expected = float(expected)
        dimension_results, reconciliations, concentrations = [], [], []
        findings, warnings = [], []
        for dimension in plan.selected_dimensions:
            if expected == 0:
                warnings.append(f"Contribution ratios for {dimension} are undefined because total change is zero.")
            analysis_groups = {key: group for key, group in analysis_frame.groupby(dimension, dropna=False)}
            comparison_groups = {key: group for key, group in comparison_frame.groupby(dimension, dropna=False)}
            keys = sorted(set(analysis_groups) | set(comparison_groups), key=str)
            raw_groups: list[GroupInvestigationResult] = []
            for key in keys:
                a_frame, c_frame = analysis_groups.get(key), comparison_groups.get(key)
                analysis_value = 0.0 if a_frame is None else float(self.analytics._metric(a_frame, plan.target_metric) or 0.0)
                comparison_value = 0.0 if c_frame is None else float(self.analytics._metric(c_frame, plan.target_metric) or 0.0)
                change, percentage = self.analytics._changes(analysis_value, comparison_value)
                assert change is not None
                ratio = None if expected == 0 else change / expected
                contribution_percent = None if ratio is None else abs(ratio) * 100
                group_size = (0 if a_frame is None else len(a_frame)) + (0 if c_frame is None else len(c_frame))
                material = (abs(change) >= plan.materiality.minimum_absolute_change and
                            (contribution_percent or 0) >= plan.materiality.minimum_contribution_percent and
                            group_size >= plan.materiality.minimum_group_size)
                raw_groups.append(GroupInvestigationResult(
                    dimension=dimension, value=None if pd.isna(key) else key,
                    analysis_value=analysis_value, comparison_value=comparison_value,
                    absolute_change=change, percentage_change=percentage,
                    contribution_ratio=ratio, contribution_percent=contribution_percent,
                    contribution_direction=self._direction(change, expected), group_size=group_size,
                    material=material, rank=0,
                ))
            raw_groups.sort(key=lambda item: (-abs(item.absolute_change), str(item.value)))
            groups = [item.model_copy(update={"rank": index}) for index, item in enumerate(raw_groups, start=1)]
            decomposed = float(sum(item.absolute_change for item in groups))
            tolerance = max(abs(expected) * self.relative_tolerance, self.relative_tolerance)
            difference = decomposed - expected
            reconciled = abs(difference) <= tolerance
            reconciliation = ReconciliationResult(
                dimension=dimension, reconciled=reconciled, expected_total_change=expected,
                decomposed_total_change=decomposed, reconciliation_difference=difference, tolerance=tolerance,
            )
            if not reconciled:
                warnings.append(f"{dimension} decomposition did not reconcile and is not authoritative.")
            negative = min(groups, key=lambda item: item.absolute_change) if groups else None
            positive = max(groups, key=lambda item: item.absolute_change) if groups else None
            dimension_results.append(DimensionInvestigationResult(
                dimension=dimension, groups=groups,
                total_absolute_movement=sum(abs(item.absolute_change) for item in groups),
                largest_negative_value=negative.value if negative and negative.absolute_change < 0 else None,
                largest_positive_value=positive.value if positive and positive.absolute_change > 0 else None,
                materially_changed_groups=sum(item.material for item in groups),
            ))
            reconciliations.append(reconciliation)
            concentration = self._concentration(dimension, groups)
            concentrations.append(concentration)
            driving = next((item for item in groups if item.contribution_direction == ContributionDirection.DRIVES_CHANGE), None)
            offset = next((item for item in groups if item.contribution_direction == ContributionDirection.OFFSETS_CHANGE), None)
            if driving:
                findings.append(f"{driving.value} had the largest aligned {plan.target_metric} change for {dimension}: {driving.absolute_change:.2f} ({driving.contribution_percent:.1f}% of the observed total-change magnitude).")
            if offset:
                findings.append(f"{offset.value} moved opposite to the observed total change for {dimension}, offsetting {offset.contribution_percent:.1f}% of its magnitude.")
            count = min(plan.top_n, len(concentration.cumulative_contribution_share))
            top_share = concentration.cumulative_contribution_share[count - 1] if count else 0.0
            findings.append(f"The top {count} driving groups in {dimension} account for {top_share:.1f}% of aligned movement.")
        status = InvestigationStatus.COMPLETED if all(item.reconciled for item in reconciliations) and not warnings else InvestigationStatus.WARNING
        plan_hash = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
        result = InvestigationResult(
            investigation_plan_id=plan.investigation_plan_id, request_id=request.request_id,
            analysis_id=analysis.analysis_id, dataset_version_id=plan.dataset_version_id,
            target_metric=plan.target_metric, investigation_type=plan.investigation_type, top_n=plan.top_n, status=status,
            dimension_results=dimension_results, reconciliation=reconciliations,
            concentration_metrics=concentrations, deterministic_findings=findings,
            warnings=warnings, limitations=plan.limitations, source_hash=source_hash,
            plan_hash=plan_hash, engine_version=self.engine_version,
        )
        logger.info("Investigation result %s plan=%s analysis=%s dataset=%s metric=%s methods=%s dimensions=%s engine=%s investigator=%s reconciliation=%s elapsed=%.3fs",
                    result.investigation_id, plan.investigation_plan_id, analysis.analysis_id,
                    plan.dataset_version_id, plan.target_metric, [item.value for item in plan.methods],
                    plan.selected_dimensions, self.engine_version,
                    f"{plan.investigator_name}:{plan.investigator_version}", status, perf_counter() - started)
        return result
