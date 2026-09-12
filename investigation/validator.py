"""Authoritative semantic validation for V5 investigation plans."""
from __future__ import annotations

from dataclasses import dataclass
import re

from analytics.metric_registry import MetricAdditivity, MetricRegistry
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.computed_analysis import ComputedAnalysis, ResultType
from contracts.dataset_profile import ColumnProfile, DatasetProfile
from contracts.investigation import InvestigationMethod, InvestigationPlan, InvestigationType


@dataclass(frozen=True, slots=True)
class InvestigationValidationIssue:
    code: str
    message: str


class InvestigationValidationError(ValueError):
    """Raised when an investigation plan is unsafe or not executable."""

    def __init__(self, issues: list[InvestigationValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{item.code}: {item.message}" for item in issues))


_EXECUTABLE = re.compile(r"(?:\bselect\b|\bdrop\b|\bjoin\b|\bimport\b|\bexec\b|\beval\b|\blambda\b|\bbecause\b|\bcaus(?:e|ed|al)\b|;|```|\$\()", re.IGNORECASE)
_PREFERRED = {"region", "product_id", "product", "category", "segment", "channel", "country", "city"}
_SENSITIVE = {"customer_id", "order_id", "transaction_id", "email", "phone", "address", "name", "ssn"}


class InvestigationSemanticValidator:
    """Validate provenance, metric additivity, dimensions, and safe content."""

    max_cardinality = 1000
    max_cardinality_ratio = 0.80
    max_null_percentage = 20.0

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()

    def dimension_eligible(self, column: ColumnProfile, profile: DatasetProfile) -> tuple[bool, str]:
        name = column.name.lower()
        if name in _SENSITIVE or any(token in name for token in ("email", "phone", "address", "ssn")):
            return False, "sensitive or transaction-level identifier"
        if "date" in name or any(token in column.dtype.lower() for token in ("float", "datetime")):
            return False, "not a categorical investigation dimension"
        if column.null_percentage > self.max_null_percentage:
            return False, "null percentage exceeds policy"
        if column.unique_count > self.max_cardinality:
            return False, "cardinality exceeds policy"
        ratio = column.unique_count / profile.row_count if profile.row_count else 1.0
        if (column.likely_identifier or ratio > self.max_cardinality_ratio) and name not in _PREFERRED:
            return False, "identifier-like or excessive cardinality"
        return True, "eligible"

    def eligible_dimensions(self, profile: DatasetProfile) -> list[str]:
        """Return deterministic preferred-first eligible dimensions."""
        eligible = [column.name for column in profile.columns if self.dimension_eligible(column, profile)[0]]
        return sorted(eligible, key=lambda name: (name.lower() not in _PREFERRED, name))

    def validate(self, plan: InvestigationPlan, request: AnalysisRequest, analysis_plan: AnalysisPlan,
                 analysis: ComputedAnalysis, profile: DatasetProfile) -> InvestigationPlan:
        """Return the same plan if safe, otherwise raise structured errors."""
        issues: list[InvestigationValidationIssue] = []
        reject = lambda code, message: issues.append(InvestigationValidationIssue(code, message))
        if request.status != RequestStatus.READY:
            reject("REQUEST_STATUS", "Source AnalysisRequest must be READY.")
        if analysis.execution_status != "COMPLETED":
            reject("ANALYSIS_STATUS", "Source ComputedAnalysis must be COMPLETED.")
        if analysis.result_type != ResultType.PERIOD_COMPARISON:
            reject("ANALYSIS_SHAPE", "V5 decomposition requires a scalar period comparison.")
        identities = {request.dataset_version_id, analysis_plan.dataset_version_id, analysis.dataset_version_id, plan.dataset_version_id}
        if len(identities) != 1 or profile.dataset_version_id != plan.dataset_version_id:
            reject("DATASET_MISMATCH", "Dataset-version provenance does not match.")
        if not (plan.request_id == request.request_id == analysis_plan.request_id == analysis.request_id):
            reject("REQUEST_MISMATCH", "Request provenance does not match.")
        if plan.analysis_plan_id != analysis_plan.plan_id or plan.analysis_id != analysis.analysis_id or analysis.plan_id != analysis_plan.plan_id:
            reject("ANALYSIS_MISMATCH", "V4 plan or analysis provenance does not match.")
        if plan.profile_id != profile.profile_id or analysis.profile_id != profile.profile_id:
            reject("PROFILE_MISMATCH", "Dataset-profile provenance does not match.")
        if not (plan.target_metric == analysis.primary_metric == analysis_plan.primary_metric):
            reject("METRIC_MISMATCH", "Target metric does not match the V4 analysis.")
        try:
            metric = self.registry.get(plan.target_metric)
            if metric.additivity != MetricAdditivity.ADDITIVE:
                reject("NON_ADDITIVE_METRIC", f"Metric '{plan.target_metric}' does not support additive decomposition.")
        except ValueError:
            reject("UNKNOWN_METRIC", f"Unknown metric: {plan.target_metric}.")
        if plan.metric_registry_version != self.registry.version:
            reject("REGISTRY_VERSION", "Metric registry version is incompatible.")
        if analysis_plan.analysis_period is None or analysis_plan.comparison_period is None:
            reject("MISSING_PERIOD", "V4 analysis must contain analysis and comparison periods.")
        elif plan.analysis_period != analysis_plan.analysis_period or plan.comparison_period != analysis_plan.comparison_period:
            reject("PERIOD_MISMATCH", "Investigation periods must match the V4 plan.")
        if analysis.analysis_period != plan.analysis_period or analysis.comparison_period != plan.comparison_period:
            reject("RESULT_PERIOD_MISMATCH", "Investigation periods must match the V4 result.")
        if plan.analysis_period.start > plan.analysis_period.end or plan.comparison_period.start > plan.comparison_period.end:
            reject("PERIOD_ORDER", "Investigation periods have invalid ordering.")
        if [item.model_dump() for item in plan.filters] != [item.model_dump() for item in analysis_plan.filters]:
            reject("FILTER_MISMATCH", "Investigation filters must match the V4 plan.")
        if len(plan.methods) != len(set(plan.methods)):
            reject("DUPLICATE_METHOD", "Investigation methods must be unique.")
        required_methods = {InvestigationMethod.GROUP_CHANGE, InvestigationMethod.RECONCILIATION}
        if not required_methods <= set(plan.methods):
            reject("METHOD_COMBINATION", "Contribution investigations require group change and reconciliation.")
        if plan.investigation_type == InvestigationType.CONCENTRATION_ANALYSIS and InvestigationMethod.CONTRIBUTION_CONCENTRATION not in plan.methods:
            reject("METHOD_COMBINATION", "Concentration analysis requires contribution concentration.")
        if plan.investigation_type == InvestigationType.DIMENSION_SCAN and InvestigationMethod.DIMENSION_DIAGNOSTICS not in plan.methods:
            reject("METHOD_COMBINATION", "Dimension scan requires dimension diagnostics.")
        if plan.investigation_type == InvestigationType.DIMENSION_SCAN and len(plan.selected_dimensions) < 2:
            reject("DIMENSION_SCAN", "Dimension scan requires at least two eligible dimensions.")
        eligible = set(self.eligible_dimensions(profile))
        if not plan.selected_dimensions:
            reject("NO_DIMENSION", "At least one eligible dimension is required.")
        if not set(plan.selected_dimensions) <= set(plan.candidate_dimensions):
            reject("DIMENSION_SELECTION", "Selected dimensions must be candidate dimensions.")
        for dimension in plan.candidate_dimensions:
            if dimension not in eligible:
                reject("INVALID_CANDIDATE_DIMENSION", f"Candidate dimension '{dimension}' is not eligible.")
        for dimension in plan.selected_dimensions:
            if dimension not in {column.name for column in profile.columns}:
                reject("UNKNOWN_DIMENSION", f"Unknown dimension: {dimension}.")
            elif dimension not in eligible:
                reject("INELIGIBLE_DIMENSION", f"Dimension '{dimension}' is identifier-like, sensitive, or too high-cardinality.")
        strings = [*plan.assumptions, *plan.limitations, *plan.selected_dimensions, *plan.candidate_dimensions]
        if any(_EXECUTABLE.search(value) for value in strings):
            reject("UNSAFE_CONTENT", "Executable or causal content is forbidden.")
        if issues:
            raise InvestigationValidationError(issues)
        return plan
