"""Independent internal-consistency checks for V3-V5 artifacts."""
from __future__ import annotations

from datetime import date
import hashlib
import math
import re

from agents.analytics_engine import AnalyticsEngine
from analytics.metric_registry import MetricAdditivity, MetricRegistry
from analytics.plan_validator import PlanSemanticValidationError, PlanSemanticValidator
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.computed_analysis import ComputedAnalysis, ResultType
from contracts.critic import CheckStatus, CriticIssue, DeterministicCheck, IssueCategory, Severity
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import ContributionDirection, InvestigationPlan, InvestigationResult


class QACheckEngine:
    """Verify artifact consistency without replacing authoritative calculations."""

    tolerance = 1e-9

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()

    @staticmethod
    def _issue(code: str, category: IssueCategory, severity: Severity, message: str,
               artifact_type: str, artifact_id: str, action: str, *, field: str | None = None,
               evidence: dict[str, object] | None = None, blocking: bool = True) -> CriticIssue:
        return CriticIssue(issue_code=code, category=category, severity=severity, message=message,
                           artifact_type=artifact_type, artifact_id=artifact_id, field_path=field,
                           evidence=evidence or {}, blocking=blocking, suggested_action=action)

    @staticmethod
    def _close(actual: float, expected: float) -> bool:
        return math.isclose(actual, expected, rel_tol=QACheckEngine.tolerance, abs_tol=QACheckEngine.tolerance)

    @staticmethod
    def _numbers(value: object, path: str = ""):
        if isinstance(value, dict):
            for key, item in value.items(): yield from QACheckEngine._numbers(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value): yield from QACheckEngine._numbers(item, f"{path}[{index}]")
        elif isinstance(value, float):
            yield path, value

    def run(self, request: AnalysisRequest, plan: AnalysisPlan, analysis: ComputedAnalysis,
            profile: DatasetProfile, source_hash: str,
            investigation_plan: InvestigationPlan | None = None,
            investigation: InvestigationResult | None = None) -> tuple[list[DeterministicCheck], list[CriticIssue], list[str]]:
        checks: list[DeterministicCheck] = []
        issues: list[CriticIssue] = []
        warnings: list[str] = []

        def finish(name: str, category: IssueCategory, before: int, message: str) -> None:
            added = issues[before:]
            status = CheckStatus.FAIL if any(item.blocking for item in added) else (CheckStatus.WARN if added else CheckStatus.PASS)
            checks.append(DeterministicCheck(check_name=name, category=category, status=status, message=message,
                                             evidence={"issue_codes": [item.issue_code for item in added]}))

        before = len(issues)
        ids = {request.dataset_version_id, plan.dataset_version_id, analysis.dataset_version_id, profile.dataset_version_id}
        if len(ids) != 1:
            issues.append(self._issue("DATASET_VERSION_MISMATCH", IssueCategory.PROVENANCE, Severity.CRITICAL,
                "Dataset-version identities do not align.", "ComputedAnalysis", analysis.analysis_id,
                "Rebuild the artifact chain from one dataset version."))
        if not (request.request_id == plan.request_id == analysis.request_id):
            issues.append(self._issue("REQUEST_ID_MISMATCH", IssueCategory.PROVENANCE, Severity.CRITICAL,
                "Request identities do not align.", "ComputedAnalysis", analysis.analysis_id,
                "Reload artifacts referenced by the persisted request."))
        if analysis.plan_id != plan.plan_id:
            issues.append(self._issue("PLAN_ID_MISMATCH", IssueCategory.PROVENANCE, Severity.CRITICAL,
                "Computed analysis references a different plan.", "ComputedAnalysis", analysis.analysis_id,
                "Reload the analysis belonging to this plan."))
        if not (request.profile_id == plan.profile_id == analysis.profile_id == profile.profile_id):
            issues.append(self._issue("PROFILE_ID_MISMATCH", IssueCategory.PROVENANCE, Severity.HIGH,
                "Profile identities do not align.", "ComputedAnalysis", analysis.analysis_id,
                "Rebuild artifacts against one persisted profile."))
        if analysis.source_dataset_hash != source_hash:
            issues.append(self._issue("SOURCE_HASH_MISMATCH", IssueCategory.PROVENANCE, Severity.CRITICAL,
                "Source hash differs from the retained dataset version.", "ComputedAnalysis", analysis.analysis_id,
                "Re-run analysis from the retained immutable source."))
        expected_plan_hash = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
        if analysis.plan_hash != expected_plan_hash:
            issues.append(self._issue("PLAN_HASH_MISMATCH", IssueCategory.PROVENANCE, Severity.CRITICAL,
                "Stored plan hash does not match the plan payload.", "ComputedAnalysis", analysis.analysis_id,
                "Recompute the analysis from the persisted plan."))
        if not plan.planner_version or not analysis.engine_version:
            issues.append(self._issue("MISSING_VERSION", IssueCategory.PROVENANCE, Severity.HIGH,
                "Planner or engine version metadata is absent.", "ComputedAnalysis", analysis.analysis_id,
                "Regenerate artifacts with complete version metadata."))
        finish("provenance", IssueCategory.PROVENANCE, before, "Artifact identities and hashes checked.")

        before = len(issues)
        if request.status != RequestStatus.READY:
            issues.append(self._issue("REQUEST_NOT_READY", IssueCategory.GROUNDING, Severity.HIGH,
                "AnalysisRequest is not READY.", "AnalysisRequest", request.request_id,
                "Resolve request clarification before analysis."))
        if analysis.execution_status != "COMPLETED":
            issues.append(self._issue("ANALYSIS_INCOMPLETE", IssueCategory.LOGICAL_CONSISTENCY, Severity.HIGH,
                "ComputedAnalysis is not completed.", "ComputedAnalysis", analysis.analysis_id,
                "Complete or rerun the analysis."))
        try:
            PlanSemanticValidator(self.registry).validate(plan, profile=profile)
        except (PlanSemanticValidationError, ValueError) as error:
            issues.append(self._issue("PLAN_SEMANTIC_INVALID", IssueCategory.GROUNDING, Severity.HIGH,
                f"AnalysisPlan failed semantic validation: {error}", "AnalysisPlan", plan.plan_id,
                "Correct and revalidate the analysis plan."))
        finish("upstream_status", IssueCategory.GROUNDING, before, "Upstream eligibility checked.")

        before = len(issues)
        for path, value in self._numbers(analysis.model_dump()):
            if not math.isfinite(value):
                issues.append(self._issue("NON_FINITE_VALUE", IssueCategory.NUMERIC_CONSISTENCY, Severity.CRITICAL,
                    "Analysis contains NaN or infinity.", "ComputedAnalysis", analysis.analysis_id,
                    "Recompute with finite numeric inputs.", field=path))
        if analysis.row_count != len(analysis.rows):
            issues.append(self._issue("ROW_COUNT_MISMATCH", IssueCategory.NUMERIC_CONSISTENCY, Severity.HIGH,
                "row_count does not match result rows.", "ComputedAnalysis", analysis.analysis_id,
                "Regenerate result metadata.", field="row_count"))
        self._check_v4_numeric(plan, analysis, issues)
        finish("numeric_consistency", IssueCategory.NUMERIC_CONSISTENCY, before, "V4 internal arithmetic and shape checked.")

        before = len(issues)
        self._check_coverage(plan, analysis, profile, issues, warnings)
        finish("coverage", IssueCategory.COVERAGE, before, "Requested periods checked against profile coverage.")

        before = len(issues)
        if bool(investigation_plan) != bool(investigation):
            issues.append(self._issue("INCOMPLETE_INVESTIGATION_CHAIN", IssueCategory.PROVENANCE, Severity.HIGH,
                "Investigation plan and result must be supplied together.", "ComputedAnalysis", analysis.analysis_id,
                "Load both persisted V5 artifacts or neither."))
        elif investigation_plan and investigation:
            self._check_investigation(request, plan, analysis, profile, source_hash,
                                      investigation_plan, investigation, issues)
        finish("investigation_reconciliation", IssueCategory.RECONCILIATION, before,
               "V5 provenance, reconciliation, contributions, and rankings checked." if investigation else "No V5 investigation supplied.")

        before = len(issues)
        self._check_evidence(investigation, issues)
        finish("evidence_traceability", IssueCategory.INTERPRETATION, before, "Deterministic findings checked against structured evidence.")

        before = len(issues)
        texts = [*plan.assumptions, *analysis.warnings]
        if investigation_plan: texts.extend([*investigation_plan.assumptions, *investigation_plan.limitations])
        if investigation: texts.extend([*investigation.deterministic_findings, *investigation.limitations])
        causal = re.compile(r"\b(caused?|led to|resulted in|because(?: of)?|drove)\b", re.IGNORECASE)
        allowed = re.compile(r"drives? (?:the )?observed (?:change|decomposition)", re.IGNORECASE)
        for text in texts:
            if causal.search(text) and not allowed.search(text):
                issues.append(self._issue("UNSUPPORTED_CAUSAL_LANGUAGE", IssueCategory.CAUSALITY, Severity.HIGH,
                    "Artifact contains unsupported causal language.", "ArtifactChain", analysis.analysis_id,
                    "Replace causal wording with descriptive contribution language.", evidence={"text": text}))
        finish("causal_language", IssueCategory.CAUSALITY, before, "Artifact wording checked for unsupported causality.")
        return checks, issues, warnings

    def _check_v4_numeric(self, plan: AnalysisPlan, analysis: ComputedAnalysis, issues: list[CriticIssue]) -> None:
        def problem(code, message, field="rows"):
            issues.append(self._issue(code, IssueCategory.NUMERIC_CONSISTENCY, Severity.HIGH, message,
                "ComputedAnalysis", analysis.analysis_id, "Recompute the result from the validated plan.", field=field))
        if analysis.result_type == ResultType.SCALAR:
            if len(analysis.rows) != 1 or analysis.primary_metric not in analysis.rows[0]: problem("SCALAR_SHAPE", "Scalar result shape is inconsistent.")
        if analysis.result_type in {ResultType.PERIOD_COMPARISON, ResultType.GROUPED_PERIOD_COMPARISON}:
            seen = set()
            for index, row in enumerate(analysis.rows):
                av, cv = row.get("analysis_period_value"), row.get("comparison_period_value")
                change, percentage = AnalyticsEngine._changes(av, cv)
                if change != row.get("absolute_change") and not (change is not None and row.get("absolute_change") is not None and self._close(float(change), float(row["absolute_change"]))):
                    problem("ABSOLUTE_CHANGE_INVALID", "absolute_change does not equal analysis minus comparison.", f"rows[{index}].absolute_change")
                actual_pct = row.get("percentage_change")
                if percentage != actual_pct and not (percentage is not None and actual_pct is not None and self._close(float(percentage), float(actual_pct))):
                    problem("PERCENTAGE_CHANGE_INVALID", "percentage_change violates the zero-denominator policy.", f"rows[{index}].percentage_change")
                if analysis.result_type == ResultType.GROUPED_PERIOD_COMPARISON:
                    key = tuple(row.get(name) for name in plan.dimensions)
                    if key in seen: problem("DUPLICATE_GROUP_KEY", "Grouped comparison contains duplicate dimension keys.")
                    seen.add(key)
                    if any(name not in row for name in plan.dimensions): problem("MISSING_DIMENSION", "Grouped result is missing a planned dimension.")
        if analysis.result_type == ResultType.TIME_SERIES:
            periods = [row.get("period") for row in analysis.rows]
            if periods != sorted(periods): problem("TREND_ORDER_INVALID", "Trend periods are not chronological.")
        if analysis.result_type == ResultType.RANKING:
            values = [row.get(analysis.primary_metric) for row in analysis.rows]
            if values != sorted(values, reverse=plan.sort_descending): problem("RANK_ORDER_INVALID", "Ranking order is inconsistent with the plan.")

    def _check_coverage(self, plan, analysis, profile, issues, warnings) -> None:
        starts = [date.fromisoformat(column.minimum_date) for column in profile.columns if column.minimum_date]
        ends = [date.fromisoformat(column.maximum_date) for column in profile.columns if column.maximum_date]
        if starts and ends:
            low, high = min(starts), max(ends)
            for name, period in (("analysis", plan.analysis_period), ("comparison", plan.comparison_period)):
                if period and (period.start < low or period.end > high):
                    issues.append(self._issue("PERIOD_OUTSIDE_COVERAGE", IssueCategory.COVERAGE, Severity.MEDIUM,
                        f"{name.title()} period is outside known profile coverage.", "AnalysisPlan", plan.plan_id,
                        "Use a fully covered period or preserve an explicit partial-period warning."))
        coverage_warnings = [item for item in profile.warnings if "partial" in item.lower() or "coverage" in item.lower()]
        for warning in coverage_warnings:
            if warning not in analysis.warnings:
                issues.append(self._issue("COVERAGE_WARNING_SUPPRESSED", IssueCategory.COVERAGE, Severity.MEDIUM,
                    "A profile coverage warning was not preserved downstream.", "ComputedAnalysis", analysis.analysis_id,
                    "Propagate the source coverage warning.", evidence={"warning": warning}, blocking=False))
                warnings.append(warning)

    def _check_investigation(self, request, plan, analysis, profile, source_hash, inv_plan, inv, issues):
        def problem(code, message, severity=Severity.HIGH, field=None):
            issues.append(self._issue(code, IssueCategory.RECONCILIATION, severity, message,
                "InvestigationResult", inv.investigation_id, "Regenerate the investigation from the reviewed V4 artifacts.", field=field))
        if not (inv.request_id == request.request_id == inv_plan.request_id): problem("INVESTIGATION_REQUEST_MISMATCH", "Investigation request provenance differs.", Severity.CRITICAL)
        if inv.analysis_id != analysis.analysis_id or inv_plan.analysis_id != analysis.analysis_id: problem("INVESTIGATION_ANALYSIS_MISMATCH", "Investigation references another V4 analysis.", Severity.CRITICAL)
        if inv.investigation_plan_id != inv_plan.investigation_plan_id: problem("INVESTIGATION_PLAN_MISMATCH", "Investigation result references another plan.", Severity.CRITICAL)
        if inv.dataset_version_id != analysis.dataset_version_id or inv_plan.dataset_version_id != analysis.dataset_version_id: problem("INVESTIGATION_DATASET_MISMATCH", "Investigation dataset provenance differs.", Severity.CRITICAL)
        if inv.target_metric != analysis.primary_metric or inv_plan.target_metric != analysis.primary_metric: problem("INVESTIGATION_METRIC_MISMATCH", "Investigation metric differs from V4.")
        if inv.source_hash != source_hash: problem("INVESTIGATION_SOURCE_HASH", "Investigation source hash differs.", Severity.CRITICAL)
        if inv.plan_hash != hashlib.sha256(inv_plan.model_dump_json().encode()).hexdigest(): problem("INVESTIGATION_PLAN_HASH", "Investigation plan hash is invalid.", Severity.CRITICAL)
        if inv_plan.analysis_period != plan.analysis_period or inv_plan.comparison_period != plan.comparison_period: problem("INVESTIGATION_PERIOD_MISMATCH", "Investigation periods differ from V4.")
        if inv_plan.metric_registry_version != self.registry.version: problem("INVESTIGATION_REGISTRY_VERSION", "Investigation metric-registry version is incompatible.")
        if not inv_plan.investigator_version or not inv.engine_version: problem("INVESTIGATION_VERSION_MISSING", "Investigator or engine version metadata is absent.")
        try:
            if self.registry.get(inv.target_metric).additivity != MetricAdditivity.ADDITIVE: problem("NON_ADDITIVE_DECOMPOSITION", "A non-additive metric was decomposed.", Severity.CRITICAL)
        except ValueError: problem("UNKNOWN_INVESTIGATION_METRIC", "Investigation metric is unknown.", Severity.CRITICAL)
        source_change = analysis.rows[0].get("absolute_change") if analysis.rows else None
        for dim_index, dimension in enumerate(inv.dimension_results):
            group_sum = sum(group.absolute_change for group in dimension.groups)
            ranked = sorted(dimension.groups, key=lambda item: (-abs(item.absolute_change), str(item.value)))
            if [item.value for item in ranked] != [item.value for item in dimension.groups] or [item.rank for item in dimension.groups] != list(range(1, len(dimension.groups)+1)):
                problem("CONTRIBUTION_RANK_INVALID", "Contribution ranking is inconsistent.", field=f"dimension_results[{dim_index}].groups")
            for group_index, group in enumerate(dimension.groups):
                expected_ratio = None if source_change == 0 else group.absolute_change / source_change
                if expected_ratio != group.contribution_ratio and not (expected_ratio is not None and group.contribution_ratio is not None and self._close(expected_ratio, group.contribution_ratio)):
                    problem("CONTRIBUTION_RATIO_INVALID", "Contribution ratio is mathematically inconsistent.", field=f"dimension_results[{dim_index}].groups[{group_index}]")
                expected_direction = InvestigationEngineDirection.direction(group.absolute_change, float(source_change))
                if group.contribution_direction != expected_direction: problem("CONTRIBUTION_DIRECTION_INVALID", "Contribution direction does not match signs.")
            reconciliation = next((item for item in inv.reconciliation if item.dimension == dimension.dimension), None)
            if reconciliation is None: problem("RECONCILIATION_MISSING", "Dimension reconciliation is missing."); continue
            if source_change is None or not self._close(reconciliation.expected_total_change, float(source_change)): problem("EXPECTED_TOTAL_INVALID", "Reconciliation expected total differs from V4.")
            if not self._close(reconciliation.decomposed_total_change, group_sum): problem("DECOMPOSED_TOTAL_INVALID", "Decomposed total differs from group sum.")
            difference = reconciliation.decomposed_total_change - reconciliation.expected_total_change
            if not self._close(reconciliation.reconciliation_difference, difference): problem("RECONCILIATION_DIFFERENCE_INVALID", "Reconciliation difference is incorrect.")
            expected_flag = abs(difference) <= reconciliation.tolerance
            if reconciliation.reconciled != expected_flag: problem("RECONCILIATION_FLAG_INVALID", "Reconciled flag does not match values and tolerance.")
            if not expected_flag: problem("RECONCILIATION_FAILED", "Investigation decomposition does not reconcile with V4.")

    def _check_evidence(self, investigation, issues):
        if investigation is None: return
        known_text = {str(item.value) for dimension in investigation.dimension_results for item in dimension.groups}
        known_text |= {dimension.dimension for dimension in investigation.dimension_results}
        known_numbers = set()
        for dimension in investigation.dimension_results:
            for item in dimension.groups:
                known_numbers |= {str(item.rank), f"{item.absolute_change:.2f}", f"{item.contribution_percent:.1f}" if item.contribution_percent is not None else ""}
        for concentration in investigation.concentration_metrics:
            known_numbers |= {f"{value:.1f}" for value in [concentration.top_1_share, concentration.top_3_share,
                                                            concentration.top_5_share, *concentration.cumulative_contribution_share]}
        known_numbers.add(str(investigation.top_n))
        for finding in investigation.deterministic_findings:
            references = any(value and value in finding for value in known_text)
            numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", finding)
            numeric_supported = all(number in known_numbers for number in numbers)
            if not references or not numeric_supported:
                issues.append(self._issue("UNSUPPORTED_FINDING", IssueCategory.INTERPRETATION, Severity.MEDIUM,
                    "Deterministic finding is not traceable to structured evidence.", "InvestigationResult",
                    investigation.investigation_id, "Regenerate the finding from structured group results.",
                    evidence={"finding": finding}, blocking=False))


class InvestigationEngineDirection:
    """Local sign policy copy used to independently verify V5 output."""
    @staticmethod
    def direction(group_change: float, total_change: float) -> ContributionDirection:
        if group_change == 0 or total_change == 0: return ContributionDirection.NEUTRAL
        return ContributionDirection.DRIVES_CHANGE if math.copysign(1, group_change) == math.copysign(1, total_change) else ContributionDirection.OFFSETS_CHANGE
