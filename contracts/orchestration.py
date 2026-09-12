"""Strict contracts for the controlled V9 workflow lifecycle."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from contracts.reporting import Audience


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"


class WorkflowStage(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    ANALYSIS_PLAN = "ANALYSIS_PLAN"
    ANALYSIS_EXECUTION = "ANALYSIS_EXECUTION"
    INVESTIGATION_DECISION = "INVESTIGATION_DECISION"
    INVESTIGATION = "INVESTIGATION"
    CRITIC = "CRITIC"
    BUSINESS_INTERPRETATION = "BUSINESS_INTERPRETATION"
    REPORT = "REPORT"
    COMPLETE = "COMPLETE"


class StageStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    REPLAYED = "REPLAYED"
    SKIPPED = "SKIPPED"
    WAITING = "WAITING"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class InvestigationPolicy(StrEnum):
    AUTO = "AUTO"
    NEVER = "NEVER"
    REQUIRED = "REQUIRED"


class WorkflowEventType(StrEnum):
    WORKFLOW_CREATED = "WORKFLOW_CREATED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    STAGE_REPLAYED = "STAGE_REPLAYED"
    STAGE_SKIPPED = "STAGE_SKIPPED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    WORKFLOW_BLOCKED = "WORKFLOW_BLOCKED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"


class InvestigationRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_absolute_change: float = Field(default=0, ge=0)
    minimum_percentage_change: float = Field(default=0, ge=0)
    routing_rules_version: str = "1.0"


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: WorkflowStage
    status: StageStatus = StageStatus.NOT_STARTED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    artifact_ids: list[str] = []
    replayed: bool = False
    attempt: int = Field(default=0, ge=0)
    warnings: list[str] = []
    error_code: str | None = None
    error_message: str | None = None


class WorkflowArtifactRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_request_id: str | None = None
    analysis_plan_id: str | None = None
    computed_analysis_id: str | None = None
    investigation_plan_id: str | None = None
    investigation_result_id: str | None = None
    critic_review_id: str | None = None
    business_insight_id: str | None = None
    report_spec_id: str | None = None
    report_artifact_id: str | None = None


class WorkflowFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: WorkflowStage
    error_code: str
    message: str
    retryable: bool
    artifact_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_run_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_identity_hash: str
    dataset_version_id: str
    profile_id: str
    original_question: str
    clarified_question: str | None = None
    clarification_questions: list[str] = []
    audience: Audience = Audience.EXECUTIVE
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_stage: WorkflowStage = WorkflowStage.REQUIREMENT
    investigation_policy: InvestigationPolicy = InvestigationPolicy.AUTO
    investigation_config: InvestigationRoutingConfig = Field(default_factory=InvestigationRoutingConfig)
    stage_records: list[StageRecord] = []
    artifact_refs: WorkflowArtifactRefs = Field(default_factory=WorkflowArtifactRefs)
    warnings: list[str] = []
    failure: WorkflowFailure | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    orchestrator_name: str = "controlled-workflow-orchestrator"
    orchestrator_version: str = "9.0.0"
    workflow_rules_version: str = "1.0"
    version: int = Field(default=0, ge=0)


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_run_id: str
    stage: WorkflowStage
    event_type: WorkflowEventType
    artifact_refs: list[str] = []
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str | int | float | bool | None] = {}
