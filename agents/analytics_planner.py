"""Translation of READY requests into constrained, semantically validated plans."""
from __future__ import annotations

from dataclasses import dataclass
import re

from analytics.metric_registry import MetricRegistry
from analytics.plan_validator import PlanSemanticValidationError, PlanSemanticValidator
from contracts.analysis_plan import Aggregation, AnalysisPlan, FilterExpression, FilterOperator, Operation, OperationType
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.dataset_profile import DatasetProfile
from contracts.planner_candidate import PlannerCandidate, RankDirection
from profiling.llm import LLMProvider


class PlannerProviderError(RuntimeError):
    """Controlled failure for an invalid or unavailable provider proposal."""


@dataclass
class AnalyticsPlanner:
    """Build deterministic plans by default; provider use must be explicit."""

    registry: MetricRegistry | None = None
    provider: LLMProvider | None = None
    planner_version: str = "4.0.0"
    prompt_version: str = "planner-v1"

    def __post_init__(self) -> None:
        self.registry = self.registry or MetricRegistry()
        self.validator = PlanSemanticValidator(self.registry)

    def build(self, request: AnalysisRequest, profile: DatasetProfile, *, use_provider: bool = False) -> AnalysisPlan:
        """Create and semantically validate a plan for a READY request."""
        if request.status != RequestStatus.READY or not request.primary_metric:
            raise ValueError("Only READY grounded requests can be planned.")
        plan = self._provider_plan(request) if use_provider else self._deterministic_plan(request)
        try:
            return self.validator.validate(plan, profile=profile)
        except PlanSemanticValidationError as error:
            if use_provider:
                raise PlannerProviderError(f"Planner provider proposal was rejected: {error}") from error
            raise

    def _base(self, request: AnalysisRequest) -> dict[str, object]:
        assert self.registry is not None
        return {
            "request_id": request.request_id, "dataset_version_id": request.dataset_version_id,
            "profile_id": request.profile_id, "analysis_period": request.analysis_period,
            "comparison_period": request.comparison_period, "planner_version": self.planner_version,
            "prompt_version": self.prompt_version, "metric_registry_version": self.registry.version,
        }

    def _deterministic_plan(self, request: AnalysisRequest) -> AnalysisPlan:
        assert request.primary_metric and self.registry is not None
        self.registry.get(request.primary_metric)
        operations = [Operation(type=OperationType.AGGREGATE, field=request.primary_metric, aggregation=Aggregation.SUM)]
        if request.dimensions:
            operations.append(Operation(type=OperationType.GROUP_BY))
        if request.analysis_period and request.comparison_period:
            operations.append(Operation(type=OperationType.GROUPED_PERIOD_COMPARE if request.dimensions else OperationType.PERIOD_COMPARE, field=request.primary_metric))
        lower = request.original_question.lower()
        bucket = next((name for name in ("day", "month", "quarter", "year") if re.search(rf"\b{name}(?:ly)?\b", lower)), None)
        if bucket:
            operations.append(Operation(type=OperationType.TREND, field=request.primary_metric))
        rank = re.search(r"\b(top|bottom)\s+(\d+)\b", lower)
        if rank:
            operations.append(Operation(type=OperationType.RANK, field=request.primary_metric))
        filters = self._filters(request)
        if filters:
            operations.append(Operation(type=OperationType.FILTER))
        return AnalysisPlan(
            **self._base(request), primary_metric=request.primary_metric,
            secondary_metrics=request.secondary_metrics, dimensions=request.dimensions, filters=filters,
            operations=operations, time_bucket=bucket,
            limit=int(rank.group(2)) if rank else None, sort=request.primary_metric if rank else None,
            sort_descending=not rank or rank.group(1) == "top", assumptions=request.inferred_assumptions,
        )

    @staticmethod
    def _filters(request: AnalysisRequest) -> list[FilterExpression]:
        """Parse only the documented declarative filter grammar."""
        parsed: list[FilterExpression] = []
        for raw in request.filters:
            between = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+BETWEEN\s+(.+?)\s+AND\s+(.+)", raw, re.IGNORECASE)
            membership = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+IN\s*\(([^)]*)\)", raw, re.IGNORECASE)
            simple = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+(EQ|NE|GT|GTE|LT|LTE)\s+(.+)", raw, re.IGNORECASE)
            if between:
                field, first, second = between.groups()
                value: object = [AnalyticsPlanner._literal(first), AnalyticsPlanner._literal(second)]
                operator = FilterOperator.BETWEEN
            elif membership:
                field, members = membership.groups()
                value = [AnalyticsPlanner._literal(item) for item in members.split(",") if item.strip()]
                operator = FilterOperator.IN
            elif simple:
                field, name, literal = simple.groups()
                value, operator = AnalyticsPlanner._literal(literal), FilterOperator(name.upper())
            else:
                raise ValueError(f"Unsupported filter syntax: {raw}")
            parsed.append(FilterExpression(field=field, operator=operator, value=value))
        return parsed

    @staticmethod
    def _literal(value: str) -> object:
        cleaned = value.strip().strip("'\"")
        try:
            return float(cleaned) if "." in cleaned else int(cleaned)
        except ValueError:
            return cleaned

    def _provider_plan(self, request: AnalysisRequest) -> AnalysisPlan:
        if self.provider is None:
            raise PlannerProviderError("No planner provider is configured.")
        try:
            raw = self.provider.generate_structured({
                "question": request.original_question,
                "allowed_metrics": self.registry.names(),
                "dataset_version_id": request.dataset_version_id,
            })
            candidate = PlannerCandidate.model_validate(raw)
        except Exception as error:
            raise PlannerProviderError(f"Planner provider failed safely: {type(error).__name__}.") from error
        operations = [Operation(type=item, field=candidate.primary_metric) for item in candidate.operation_types]
        rank = candidate.ranking
        return AnalysisPlan(
            **self._base(request), primary_metric=candidate.primary_metric,
            secondary_metrics=candidate.secondary_metrics, dimensions=candidate.dimensions,
            filters=candidate.filters, operations=operations, time_bucket=candidate.time_bucket,
            limit=rank.limit if rank else None, sort=candidate.primary_metric if rank else None,
            sort_descending=rank is None or rank.direction == RankDirection.TOP,
            assumptions=candidate.assumptions, provider_name=self.provider.name, model_name=self.provider.model_name,
        )
