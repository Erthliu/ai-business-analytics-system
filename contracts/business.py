"""Strict V7 evidence-bound business interpretation contracts."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class BusinessInsightStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_CAVEATS = "COMPLETE_WITH_CAVEATS"
    BLOCKED = "BLOCKED"


class Importance(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class InsightConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ClaimType(StrEnum):
    METRIC_VALUE = "METRIC_VALUE"
    METRIC_CHANGE = "METRIC_CHANGE"
    TOP_CONTRIBUTOR = "TOP_CONTRIBUTOR"
    OFFSETTING_CONTRIBUTOR = "OFFSETTING_CONTRIBUTOR"
    CONCENTRATION = "CONCENTRATION"
    TREND_DIRECTION = "TREND_DIRECTION"
    QA_CAVEAT = "QA_CAVEAT"


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ref: str
    artifact_type: str
    artifact_id: str
    evidence_type: str
    result_type: str | None = None
    metric: str | None = None
    dimension: str | None = None
    group_value: str | int | float | bool | None = None
    value: object


class BusinessClaim(BaseModel):
    """Declarative factual claim; prose is rendered from referenced evidence."""

    model_config = ConfigDict(extra="forbid")
    claim_type: ClaimType
    metric: str | None = None
    dimension: str | None = None
    evidence_refs: list[str] = Field(min_length=1)


class KeyInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    insight_code: str
    statement: str
    importance: Importance
    evidence_refs: list[str] = Field(min_length=1)
    confidence: InsightConfidence
    caveats: list[str] = []


class ProviderBusinessInterpretation(BaseModel):
    """Provider candidate selects claims; final factual prose remains deterministic."""

    model_config = ConfigDict(extra="forbid")
    claims: list[BusinessClaim] = Field(min_length=1)
    headline: str | None = None
    executive_summary: str | None = None
    caveats: list[str] = []
    unanswered_questions: list[str] = []


class BusinessInsight(BaseModel):
    """Evidence-bound business interpretation of QA-approved artifacts."""

    model_config = ConfigDict(extra="forbid")
    insight_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    analysis_id: str
    investigation_id: str | None = None
    critic_review_id: str
    dataset_version_id: str
    status: BusinessInsightStatus
    headline: str
    executive_summary: str
    claims: list[BusinessClaim]
    key_insights: list[KeyInsight]
    evidence: list[EvidenceRecord]
    caveats: list[str]
    business_context_assumptions: list[str]
    unanswered_questions: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str = "business-interpretation-agent"
    agent_version: str = "7.0.0"
    business_rules_version: str = "1.0"
    prompt_version: str = "business-v1"
    provider_name: str | None = None
    model_name: str | None = None

    @classmethod
    def json_schema(cls) -> dict[str, object]:
        return cls.model_json_schema()
