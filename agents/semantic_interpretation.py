"""Typed, non-authoritative provider response for Requirement Agent."""
from pydantic import BaseModel, ConfigDict


class SemanticAmbiguity(BaseModel):
    topic: str
    reason: str


class SemanticInterpretation(BaseModel):
    """Only structured candidates; never directly executable."""
    model_config = ConfigDict(extra="forbid")
    business_objective: str | None = None
    analytical_question: str | None = None
    primary_metric_candidate: str | None = None
    secondary_metric_candidates: list[str] = []
    dimension_candidates: list[str] = []
    filter_candidates: list[str] = []
    time_expression: str | None = None
    ambiguities: list[SemanticAmbiguity] = []
    clarification_questions: list[str] = []
