"""Strict contracts for descriptive, non-causal V5 investigations."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from contracts.analysis_plan import FilterExpression
from contracts.analysis_request import DateRange


class InvestigationType(StrEnum):
    CONTRIBUTION_ANALYSIS = "CONTRIBUTION_ANALYSIS"
    CHANGE_DECOMPOSITION = "CHANGE_DECOMPOSITION"
    CONCENTRATION_ANALYSIS = "CONCENTRATION_ANALYSIS"
    DIMENSION_SCAN = "DIMENSION_SCAN"


class InvestigationMethod(StrEnum):
    GROUP_CHANGE = "GROUP_CHANGE"
    RECONCILIATION = "RECONCILIATION"
    CONTRIBUTION_CONCENTRATION = "CONTRIBUTION_CONCENTRATION"
    DIMENSION_DIAGNOSTICS = "DIMENSION_DIAGNOSTICS"


class ContributionDirection(StrEnum):
    DRIVES_CHANGE = "DRIVES_CHANGE"
    OFFSETS_CHANGE = "OFFSETS_CHANGE"
    NEUTRAL = "NEUTRAL"


class InvestigationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    WARNING = "WARNING"


class MaterialityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_absolute_change: float = Field(default=0.0, ge=0)
    minimum_contribution_percent: float = Field(default=0.0, ge=0, le=100)
    minimum_group_size: int = Field(default=1, ge=1)


class InvestigationPlan(BaseModel):
    """Declarative plan for deterministic descriptive investigation."""

    model_config = ConfigDict(extra="forbid")
    investigation_plan_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    analysis_plan_id: str
    analysis_id: str
    dataset_version_id: str
    profile_id: str
    investigation_type: InvestigationType
    target_metric: str
    analysis_period: DateRange
    comparison_period: DateRange
    candidate_dimensions: list[str]
    selected_dimensions: list[str]
    filters: list[FilterExpression] = []
    methods: list[InvestigationMethod]
    top_n: int = Field(default=5, gt=0, le=100)
    materiality: MaterialityThresholds = Field(default_factory=MaterialityThresholds)
    assumptions: list[str] = []
    limitations: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    investigator_name: str = "investigation-agent"
    investigator_version: str = "5.0.0"
    prompt_version: str = "investigation-v1"
    provider_name: str | None = None
    model_name: str | None = None
    metric_registry_version: str = "1.0.0"

    @classmethod
    def json_schema(cls) -> dict[str, object]:
        return cls.model_json_schema()


class InvestigationCandidate(BaseModel):
    """Constrained provider proposal with no numerical result fields."""

    model_config = ConfigDict(extra="forbid")
    investigation_type: InvestigationType
    candidate_dimensions: list[str]
    selected_dimensions: list[str]
    methods: list[InvestigationMethod]
    top_n: int = Field(default=5, gt=0, le=100)
    assumptions: list[str] = []


class GroupInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    value: str | int | float | bool | None
    analysis_value: float
    comparison_value: float
    absolute_change: float
    percentage_change: float | None
    contribution_ratio: float | None
    contribution_percent: float | None
    contribution_direction: ContributionDirection
    group_size: int
    material: bool
    rank: int


class DimensionInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    groups: list[GroupInvestigationResult]
    total_absolute_movement: float
    largest_negative_value: str | int | float | bool | None = None
    largest_positive_value: str | int | float | bool | None = None
    materially_changed_groups: int


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    reconciled: bool
    expected_total_change: float
    decomposed_total_change: float
    reconciliation_difference: float
    tolerance: float


class ConcentrationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    top_1_share: float
    top_3_share: float
    top_5_share: float
    cumulative_contribution_share: list[float]


class InvestigationResult(BaseModel):
    """Machine-readable deterministic investigation output."""

    model_config = ConfigDict(extra="forbid")
    investigation_id: str = Field(default_factory=lambda: str(uuid4()))
    investigation_plan_id: str
    request_id: str
    analysis_id: str
    dataset_version_id: str
    target_metric: str
    investigation_type: InvestigationType
    top_n: int = Field(gt=0, le=100)
    status: InvestigationStatus
    dimension_results: list[DimensionInvestigationResult]
    reconciliation: list[ReconciliationResult]
    concentration_metrics: list[ConcentrationMetrics]
    deterministic_findings: list[str]
    warnings: list[str]
    limitations: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    engine_name: str = "deterministic-investigation-engine"
    engine_version: str = "5.0.0"
    source_hash: str
    plan_hash: str

    @classmethod
    def json_schema(cls) -> dict[str, object]:
        return cls.model_json_schema()
