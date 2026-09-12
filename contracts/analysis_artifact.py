"""Schema-validatable contract for future analytical outputs."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    """Traceable support for a finding."""

    source: str
    detail: str


class AnalysisArtifact(BaseModel):
    """A versioned analytical output; this module does not generate reports."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    pipeline_run_id: str
    dataset_version: str
    question: str = Field(min_length=1)
    metrics: dict[str, float | int | str]
    findings: list[str]
    evidence: list[Evidence]
    assumptions: list[str]
    limitations: list[str]
    confidence: float = Field(ge=0, le=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    producer_name: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return JSON Schema for API, storage, and future agent validation."""
        return cls.model_json_schema()
