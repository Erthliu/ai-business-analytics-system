"""Strict V6 analytical quality-control contracts."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ReviewStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReviewConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IssueCategory(StrEnum):
    DATA_QUALITY = "DATA_QUALITY"
    NUMERIC_CONSISTENCY = "NUMERIC_CONSISTENCY"
    PROVENANCE = "PROVENANCE"
    GROUNDING = "GROUNDING"
    COVERAGE = "COVERAGE"
    RECONCILIATION = "RECONCILIATION"
    LOGICAL_CONSISTENCY = "LOGICAL_CONSISTENCY"
    INTERPRETATION = "INTERPRETATION"
    CAUSALITY = "CAUSALITY"
    PRIVACY = "PRIVACY"
    PROVIDER_OUTPUT = "PROVIDER_OUTPUT"
    CONTRACT = "CONTRACT"


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class DeterministicCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_name: str
    category: IssueCategory
    status: CheckStatus
    message: str
    evidence: dict[str, object] = {}


class CriticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_code: str
    category: IssueCategory
    severity: Severity
    message: str
    artifact_type: str
    artifact_id: str
    field_path: str | None = None
    evidence: dict[str, object] = {}
    blocking: bool
    suggested_action: str


class ProviderFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_code: str
    severity: Severity
    message: str
    artifact_type: str
    artifact_id: str
    field_path: str | None = None
    evidence_reference: str | None = None


class ProviderCritique(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[ProviderFinding] = []
    interpretation_risks: list[str] = []
    missing_caveats: list[str] = []
    confidence: ReviewConfidence


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed_artifact_ids: list[str]
    deterministic_check_count: int
    passed_check_count: int
    issue_count: int
    investigation_included: bool


class CriticReview(BaseModel):
    """Immutable review whose decision is controlled by deterministic checks."""

    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    analysis_plan_id: str
    analysis_id: str
    investigation_plan_id: str | None = None
    investigation_id: str | None = None
    dataset_version_id: str
    profile_id: str
    overall_status: ReviewStatus
    severity: Severity
    deterministic_checks: list[DeterministicCheck]
    issues: list[CriticIssue]
    warnings: list[str]
    passed_checks: list[str]
    evidence_summary: EvidenceSummary
    review_confidence: ReviewConfidence
    provider_findings: list[ProviderFinding] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    critic_name: str = "critic-agent"
    critic_version: str = "6.0.0"
    qa_rules_version: str = "1.0"
    prompt_version: str = "critic-v1"
    provider_name: str | None = None
    model_name: str | None = None

    @classmethod
    def json_schema(cls) -> dict[str, object]:
        return cls.model_json_schema()

    @property
    def downstream_eligible(self) -> bool:
        """Allow future layers to consume only non-failing reviews."""
        return self.overall_status in {ReviewStatus.PASS, ReviewStatus.PASS_WITH_WARNINGS}
