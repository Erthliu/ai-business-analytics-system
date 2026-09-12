"""SQLAlchemy models for reliable, traceable pipeline execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for application database models."""


class PipelineRun(Base):
    """One attempted execution, deduplicated by its immutable source hash."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    source_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_passed: Mapped[bool | None] = mapped_column(nullable=True)
    validation_statistics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    lineage_records: Mapped[list[DatasetLineage]] = relationship(
        back_populates="pipeline_run", cascade="all, delete-orphan"
    )
    dataset_versions: Mapped[list[DatasetVersion]] = relationship(back_populates="pipeline_run")
    profiles: Mapped[list[DatasetProfileRecord]] = relationship(back_populates="pipeline_run")


class DatasetVersion(Base):
    """Immutable identity for a retained source dataset version."""

    __tablename__ = "dataset_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(36), index=True)
    version_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_uri: Mapped[str] = mapped_column(Text)
    storage_uri: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    schema_fingerprint: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), unique=True)

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="dataset_versions")
    profiles: Mapped[list[DatasetProfileRecord]] = relationship(back_populates="dataset_version")


class DatasetProfileRecord(Base):
    """Persisted profile payload, never a copy of raw source data."""

    __tablename__ = "dataset_profiles"
    __table_args__ = (UniqueConstraint("dataset_version_id", "profiler_version", name="uq_profile_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    profiler_version: Mapped[str] = mapped_column(String(64))
    profile_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dataset_version: Mapped[DatasetVersion] = relationship(back_populates="profiles")
    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="profiles")


class AnalysisRequestRecord(Base):
    """Persisted grounded requirement; never contains a provider secret."""

    __tablename__ = "analysis_requests"
    __table_args__ = (UniqueConstraint("identity_hash", name="uq_request_identity"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("dataset_profiles.id"))
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    status: Mapped[str] = mapped_column(String(32))
    agent_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    provider_name: Mapped[str | None] = mapped_column(String(128))
    model_name: Mapped[str | None] = mapped_column(String(128))
    request_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class AnalysisPlanRecord(Base):
    __tablename__="analysis_plans"
    id:Mapped[int]=mapped_column(primary_key=True)
    plan_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    request_id:Mapped[int]=mapped_column(ForeignKey("analysis_requests.id"))
    plan_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class ComputedAnalysisRecord(Base):
    __tablename__="computed_analyses"
    id:Mapped[int]=mapped_column(primary_key=True)
    analysis_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    plan_id:Mapped[int]=mapped_column(ForeignKey("analysis_plans.id"))
    analysis_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class InvestigationPlanRecord(Base):
    __tablename__="investigation_plans"
    id:Mapped[int]=mapped_column(primary_key=True)
    investigation_plan_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    request_id:Mapped[int]=mapped_column(ForeignKey("analysis_requests.id"))
    analysis_plan_id:Mapped[int]=mapped_column(ForeignKey("analysis_plans.id"))
    analysis_id:Mapped[int]=mapped_column(ForeignKey("computed_analyses.id"))
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"))
    profile_id:Mapped[int]=mapped_column(ForeignKey("dataset_profiles.id"))
    plan_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class InvestigationResultRecord(Base):
    __tablename__="investigation_results"
    id:Mapped[int]=mapped_column(primary_key=True)
    investigation_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    investigation_plan_id:Mapped[int]=mapped_column(ForeignKey("investigation_plans.id"))
    analysis_id:Mapped[int]=mapped_column(ForeignKey("computed_analyses.id"))
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"))
    result_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class CriticReviewRecord(Base):
    __tablename__="critic_reviews"
    id:Mapped[int]=mapped_column(primary_key=True)
    review_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    analysis_id:Mapped[int]=mapped_column(ForeignKey("computed_analyses.id"),index=True)
    investigation_id:Mapped[int|None]=mapped_column(ForeignKey("investigation_results.id"),nullable=True,index=True)
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"),index=True)
    status:Mapped[str]=mapped_column(String(32),index=True)
    severity:Mapped[str]=mapped_column(String(16),index=True)
    critic_version:Mapped[str]=mapped_column(String(64))
    qa_rules_version:Mapped[str]=mapped_column(String(64))
    prompt_version:Mapped[str]=mapped_column(String(64))
    provider_name:Mapped[str|None]=mapped_column(String(128),nullable=True)
    model_name:Mapped[str|None]=mapped_column(String(128),nullable=True)
    review_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class BusinessInsightRecord(Base):
    __tablename__="business_insights"
    id:Mapped[int]=mapped_column(primary_key=True)
    insight_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    critic_review_id:Mapped[int]=mapped_column(ForeignKey("critic_reviews.id"),index=True)
    analysis_id:Mapped[int]=mapped_column(ForeignKey("computed_analyses.id"),index=True)
    investigation_id:Mapped[int|None]=mapped_column(ForeignKey("investigation_results.id"),nullable=True,index=True)
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"),index=True)
    status:Mapped[str]=mapped_column(String(32),index=True)
    agent_version:Mapped[str]=mapped_column(String(64))
    business_rules_version:Mapped[str]=mapped_column(String(64))
    prompt_version:Mapped[str]=mapped_column(String(64))
    provider_name:Mapped[str|None]=mapped_column(String(128),nullable=True)
    model_name:Mapped[str|None]=mapped_column(String(128),nullable=True)
    insight_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())


class ReportSpecRecord(Base):
    __tablename__="report_specs"
    id:Mapped[int]=mapped_column(primary_key=True)
    report_spec_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    business_insight_id:Mapped[int]=mapped_column(ForeignKey("business_insights.id"),index=True)
    critic_review_id:Mapped[int]=mapped_column(ForeignKey("critic_reviews.id"),index=True)
    analysis_id:Mapped[int]=mapped_column(ForeignKey("computed_analyses.id"),index=True)
    investigation_id:Mapped[int|None]=mapped_column(ForeignKey("investigation_results.id"),nullable=True,index=True)
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"),index=True)
    audience:Mapped[str]=mapped_column(String(32),index=True)
    agent_version:Mapped[str]=mapped_column(String(64))
    prompt_version:Mapped[str]=mapped_column(String(64))
    report_rules_version:Mapped[str]=mapped_column(String(64))
    provider_name:Mapped[str|None]=mapped_column(String(128),nullable=True)
    model_name:Mapped[str|None]=mapped_column(String(128),nullable=True)
    spec_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())


class ReportArtifactRecord(Base):
    __tablename__="report_artifacts"
    id:Mapped[int]=mapped_column(primary_key=True)
    report_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    identity_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    report_spec_id:Mapped[int]=mapped_column(ForeignKey("report_specs.id"),index=True)
    business_insight_id:Mapped[int]=mapped_column(ForeignKey("business_insights.id"),index=True)
    critic_review_id:Mapped[int]=mapped_column(ForeignKey("critic_reviews.id"),index=True)
    analysis_id:Mapped[int]=mapped_column(ForeignKey("computed_analyses.id"),index=True)
    investigation_id:Mapped[int|None]=mapped_column(ForeignKey("investigation_results.id"),nullable=True,index=True)
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"),index=True)
    status:Mapped[str]=mapped_column(String(32),index=True)
    renderer_version:Mapped[str]=mapped_column(String(64))
    report_rules_version:Mapped[str]=mapped_column(String(64))
    artifact_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())


class WorkflowRunRecord(Base):
    __tablename__="workflow_runs"
    id:Mapped[int]=mapped_column(primary_key=True)
    workflow_run_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    workflow_identity_hash:Mapped[str]=mapped_column(String(64),index=True)
    dataset_version_id:Mapped[int]=mapped_column(ForeignKey("dataset_versions.id"),index=True)
    profile_id:Mapped[int]=mapped_column(ForeignKey("dataset_profiles.id"),index=True)
    status:Mapped[str]=mapped_column(String(40),index=True)
    current_stage:Mapped[str]=mapped_column(String(40),index=True)
    version:Mapped[int]=mapped_column(Integer,default=0)
    run_content:Mapped[dict[str,Any]]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),index=True)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)


class WorkflowStageEventRecord(Base):
    __tablename__="workflow_stage_events"
    id:Mapped[int]=mapped_column(primary_key=True)
    event_id:Mapped[str]=mapped_column(String(36),unique=True,index=True)
    workflow_run_id:Mapped[int]=mapped_column(ForeignKey("workflow_runs.id"),index=True)
    stage:Mapped[str]=mapped_column(String(40),index=True)
    event_type:Mapped[str]=mapped_column(String(40),index=True)
    artifact_refs:Mapped[list[str]]=mapped_column(JSON)
    event_metadata:Mapped[dict[str,Any]]=mapped_column("event_metadata",JSON)
    occurred_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),index=True)


class DatasetLineage(Base):
    """Immutable description of an input-to-output relationship in a run."""

    __tablename__ = "dataset_lineage"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "source_hash", "output_name", name="uq_lineage_edge"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    source_uri: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    output_name: Mapped[str] = mapped_column(String(128))
    transformation: Mapped[str] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="lineage_records")
