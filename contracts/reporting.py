"""Strict V8 report specification and rendered artifact contracts."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Audience(StrEnum):
    EXECUTIVE = "EXECUTIVE"
    ANALYST = "ANALYST"
    OPERATIONS = "OPERATIONS"
    GENERAL_BUSINESS = "GENERAL_BUSINESS"


class SectionType(StrEnum):
    EXECUTIVE_SUMMARY = "EXECUTIVE_SUMMARY"
    PRIMARY_RESULT = "PRIMARY_RESULT"
    KEY_DRIVERS = "KEY_DRIVERS"
    TREND = "TREND"
    RANKING = "RANKING"
    EVIDENCE_TABLE = "EVIDENCE_TABLE"
    QA_CAVEATS = "QA_CAVEATS"
    UNANSWERED_QUESTIONS = "UNANSWERED_QUESTIONS"
    METHODOLOGY = "METHODOLOGY"


class ChartType(StrEnum):
    BAR = "BAR"
    LINE = "LINE"
    KPI = "KPI"
    COMPARISON_BAR = "COMPARISON_BAR"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"
    NONE = "NONE"


class ReportStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_CAVEATS = "COMPLETE_WITH_CAVEATS"
    BLOCKED = "BLOCKED"


class ReportSectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str
    section_type: SectionType
    title: str
    claim_ids: list[str] = []
    evidence_refs: list[str] = []


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chart_id: str
    chart_type: ChartType
    title: str
    claim_ids: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    x_field: str
    y_field: str
    series: str | None = None
    sort: SortDirection = SortDirection.NONE
    limit: int | None = Field(default=None, ge=1, le=100)
    unit: str | None = None
    notes: list[str] = []


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_id: str
    title: str
    claim_ids: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    columns: list[str] = Field(min_length=1)
    sort: SortDirection = SortDirection.NONE
    sort_by: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100)


class ReportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_spec_id: str = Field(default_factory=lambda: str(uuid4()))
    business_insight_id: str
    critic_review_id: str
    analysis_id: str
    investigation_id: str | None = None
    dataset_version_id: str
    audience: Audience
    report_title: str
    sections: list[ReportSectionSpec] = Field(min_length=1)
    chart_specs: list[ChartSpec]
    table_specs: list[TableSpec]
    included_claim_ids: list[str] = Field(min_length=1)
    included_evidence_refs: list[str] = Field(min_length=1)
    included_caveats: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str = "report-agent"
    agent_version: str = "8.0.0"
    prompt_version: str = "report-v1"
    provider_name: str | None = None
    model_name: str | None = None
    report_rules_version: str = "1.0"


class ProviderChartPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    chart_type: ChartType


class ProviderReportProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_title: str | None = None
    section_order: list[SectionType]
    prioritized_claim_ids: list[str]
    chart_preferences: list[ProviderChartPreference] = []
    included_caveats: list[str]


class RenderedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    claim_ids: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class RenderedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str
    section_type: SectionType
    title: str
    statements: list[RenderedStatement] = []
    informational_items: list[str] = []


class ChartDatum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: str | int | float | bool | None
    y: int | float | None
    series: str | None = None


class RenderedChart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chart_id: str
    chart_type: ChartType
    title: str
    claim_ids: list[str]
    evidence_refs: list[str]
    data: list[ChartDatum]
    x_field: str
    y_field: str
    unit: str | None = None
    notes: list[str] = []


class RenderedTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_id: str
    title: str
    claim_ids: list[str]
    evidence_refs: list[str]
    columns: list[str]
    rows: list[dict[str, object]]


class ReportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_insight_id: str
    critic_review_id: str
    analysis_id: str
    investigation_id: str | None = None
    dataset_version_id: str
    claim_evidence: dict[str, list[str]]


class ReportArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    report_spec_id: str
    business_insight_id: str
    critic_review_id: str
    analysis_id: str
    investigation_id: str | None = None
    dataset_version_id: str
    status: ReportStatus
    title: str
    rendered_sections: list[RenderedSection] = Field(min_length=1)
    charts: list[RenderedChart]
    tables: list[RenderedTable]
    caveats: list[str]
    unanswered_questions: list[str]
    provenance: ReportProvenance
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    renderer_name: str = "deterministic-markdown-report-assembler"
    renderer_version: str = "8.0.0"
    report_rules_version: str = "1.0"
