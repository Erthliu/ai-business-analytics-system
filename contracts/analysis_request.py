"""Strict contract for a grounded analytical requirement, not an analysis."""

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field


class RequestStatus(StrEnum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"


class DateRange(BaseModel):
    start: date
    end: date
    source: str


class Ambiguity(BaseModel):
    topic: str
    reason: str
    severity: str
    candidate_interpretations: list[str] = []


class ClarificationQuestion(BaseModel):
    question: str
    relates_to: str
    required: bool = True


class AnalysisRequest(BaseModel):
    """Executable only when status is READY and all fields are grounded."""

    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    original_question: str
    business_objective: str | None = None
    analytical_question: str | None = None
    primary_metric: str | None = None
    secondary_metrics: list[str] = []
    dimensions: list[str] = []
    analysis_period: DateRange | None = None
    comparison_period: DateRange | None = None
    filters: list[str] = []
    required_fields: list[str] = []
    explicit_requirements: list[str] = []
    inferred_assumptions: list[str] = []
    ambiguities: list[Ambiguity] = []
    clarification_required: bool = False
    clarification_questions: list[ClarificationQuestion] = []
    dataset_version_id: str
    profile_id: str
    status: RequestStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str = "requirement-agent"
    agent_version: str
    prompt_version: str
    provider_name: str | None = None
    model_name: str | None = None

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return cls.model_json_schema()
