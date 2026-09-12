"""Narrow, grounded requirement interpretation; never performs analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import re

from contracts.analysis_request import (
    Ambiguity, AnalysisRequest, ClarificationQuestion, DateRange, RequestStatus,
)
from contracts.dataset_profile import DatasetProfile
from profiling.llm import LLMProvider
from agents.time_resolution import resolve_named_months, resolve_time
from agents.semantic_interpretation import SemanticInterpretation

AMBIGUOUS_TERMS = ("best", "bad", "perform well", "growth", "high value", "churn", "successful", "poor performance")


class RequirementInterpretationError(RuntimeError):
    """Raised when optional provider output cannot be safely interpreted."""


def _coverage(profile: DatasetProfile) -> tuple[date | None, date | None]:
    dates = [(column.minimum_date, column.maximum_date) for column in profile.columns if column.minimum_date]
    if not dates:
        return None, None
    return min(date.fromisoformat(start) for start, _ in dates), max(date.fromisoformat(end) for _, end in dates if end)


def _period_for_month(month: int, year: int, source: str) -> DateRange:
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    return DateRange(start=start, end=end, source=source)


@dataclass
class RequirementAgent:
    """Deterministic grounding plus optional, non-authoritative semantic provider."""

    agent_version: str = "3.0.0"
    prompt_version: str = "requirement-v1"
    provider: LLMProvider | None = None
    current_date: date = date.today()

    def interpret(self, question: str, profile: DatasetProfile) -> AnalysisRequest:
        """Return a READY request only for grounded, unambiguous patterns."""
        fields = {column.name for column in profile.columns}
        lower = question.lower()
        base = dict(original_question=question, dataset_version_id=profile.dataset_version_id,
                    profile_id=profile.profile_id, agent_version=self.agent_version,
                    prompt_version=self.prompt_version)
        ambiguous = any(term in lower for term in AMBIGUOUS_TERMS)
        if ambiguous and self.provider is None:
            return AnalysisRequest(**base, status=RequestStatus.NEEDS_CLARIFICATION, clarification_required=True,
                ambiguities=[Ambiguity(topic="business definition", reason="No measurable definition was supplied.", severity="HIGH")],
                clarification_questions=[ClarificationQuestion(question="What measurable definition should be used?", relates_to="business definition")])
        if ambiguous and self.provider is not None:
            return self._interpret_with_provider(question, profile, base, fields)
        mentioned = [column for column in profile.columns if re.search(rf"\b{re.escape(column.name.lower())}\b", lower)]
        metric_column = next((column for column in mentioned if column.numeric is not None or "float" in column.dtype or "int" in column.dtype), mentioned[0] if mentioned else None)
        metric = metric_column.name if metric_column else None
        dimensions = [field for field in fields if field != metric and f"by {field.lower()}" in lower]
        if metric is None and self.provider is not None:
            return self._interpret_with_provider(question, profile, base, fields)
        if metric is None:
            return AnalysisRequest(**base, status=RequestStatus.NEEDS_CLARIFICATION, clarification_required=True,
                clarification_questions=[ClarificationQuestion(question="Which available metric should be analyzed?", relates_to="metric")])
        request = AnalysisRequest(**base, status=RequestStatus.READY, primary_metric=metric,
            dimensions=dimensions, required_fields=[metric, *dimensions],
            analytical_question=question, business_objective="Describe the requested metric.",
            explicit_requirements=[f"Metric: {metric}", *[f"Dimension: {item}" for item in dimensions]])
        periods = resolve_named_months(question, self.current_date)
        if periods:
            request.analysis_period = periods[0]
            if len(periods) > 1 and any(token in lower for token in (" vs ", " versus ", " with ", " compared ")):
                request.comparison_period = periods[1]
        else:
            period = resolve_time(question, self.current_date)
            if period:
                request.analysis_period = period
        self._ground(request, fields, profile)
        return request

    def _interpret_with_provider(self, question: str, profile: DatasetProfile, base: dict[str, object], fields: set[str]) -> AnalysisRequest:
        """Validate a provider candidate without ever trusting it as authoritative."""
        try:
            raw = self.provider.generate_structured({"question": question, "fields": sorted(fields)})
            candidate = SemanticInterpretation.model_validate(raw)
        except Exception as error:
            return AnalysisRequest(**base, status=RequestStatus.INVALID, clarification_required=True,
                clarification_questions=[ClarificationQuestion(question="Semantic interpretation is unavailable; clarify the request.", relates_to="provider")])
        if candidate.ambiguities or candidate.primary_metric_candidate is None:
            return AnalysisRequest(**base, status=RequestStatus.NEEDS_CLARIFICATION, clarification_required=True,
                clarification_questions=[ClarificationQuestion(question=item, relates_to="semantic interpretation") for item in candidate.clarification_questions] or [ClarificationQuestion(question="Please clarify the analytical definition.", relates_to="semantic interpretation")])
        request = AnalysisRequest(**base, status=RequestStatus.READY, primary_metric=candidate.primary_metric_candidate,
            secondary_metrics=candidate.secondary_metric_candidates, dimensions=candidate.dimension_candidates,
            filters=candidate.filter_candidates, required_fields=[candidate.primary_metric_candidate, *candidate.secondary_metric_candidates, *candidate.dimension_candidates],
            business_objective=candidate.business_objective, analytical_question=candidate.analytical_question,
            inferred_assumptions=["Provider candidate revalidated deterministically."],
            provider_name=self.provider.name, model_name=self.provider.model_name)
        if candidate.time_expression:
            request.analysis_period = resolve_time(candidate.time_expression, self.current_date)
            if request.analysis_period is None:
                request.status = RequestStatus.NEEDS_CLARIFICATION
                request.clarification_required = True
                request.clarification_questions.append(ClarificationQuestion(question="Unsupported time expression.", relates_to="time"))
        self._ground(request, fields, profile)
        return request

    def _ground(self, request: AnalysisRequest, fields: set[str], profile: DatasetProfile) -> None:
        filter_fields = [item.split()[0] for item in request.filters if item]
        referenced = [item for item in [request.primary_metric, *request.secondary_metrics, *request.dimensions, *request.required_fields, *filter_fields] if item]
        unknown = sorted(set(referenced) - fields)
        if unknown:
            request.status = RequestStatus.NEEDS_CLARIFICATION
            request.clarification_required = True
            request.clarification_questions.append(ClarificationQuestion(question=f"Unknown field(s): {', '.join(unknown)}.", relates_to="field"))
        coverage_start, coverage_end = _coverage(profile)
        for period in (request.analysis_period, request.comparison_period):
            if period and coverage_start and coverage_end and (period.start < coverage_start or period.end > coverage_end):
                request.status = RequestStatus.NEEDS_CLARIFICATION
                request.clarification_required = True
                request.clarification_questions.append(ClarificationQuestion(question="Requested period is outside dataset coverage.", relates_to="time"))

    @staticmethod
    def identity(question: str, profile: DatasetProfile, agent_version: str, prompt_version: str) -> str:
        """Stable idempotency digest for an interpretation."""
        normalized = " ".join(question.lower().split())
        value = "|".join([normalized, profile.profile_id, profile.dataset_version_id, agent_version, prompt_version])
        return hashlib.sha256(value.encode()).hexdigest()
