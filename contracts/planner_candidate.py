"""Strict provider proposal contract; it cannot represent executable code."""
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field
from contracts.analysis_plan import FilterExpression, OperationType


class RankDirection(StrEnum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"


class RankingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: RankDirection
    limit: int = Field(gt=0, le=1000)


class PlannerCandidate(BaseModel):
    """Provider output limited to declarative analytical choices."""

    model_config = ConfigDict(extra="forbid")
    primary_metric: str
    secondary_metrics: list[str] = []
    dimensions: list[str] = []
    filters: list[FilterExpression] = []
    operation_types: list[OperationType]
    time_bucket: str | None = None
    ranking: RankingCandidate | None = None
    assumptions: list[str] = []
