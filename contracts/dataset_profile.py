"""Strict, schema-validatable contract for a read-only dataset profile."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class NumericStatistics(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    median: float | None = None
    standard_deviation: float | None = None
    p25: float | None = None
    p75: float | None = None
    zero_count: int
    negative_count: int


class CategoricalValue(BaseModel):
    value: str
    frequency: int


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_count: int
    null_percentage: float
    unique_count: int
    uniqueness_ratio: float
    numeric: NumericStatistics | None = None
    top_values: list[CategoricalValue] = []
    minimum_date: str | None = None
    maximum_date: str | None = None
    invalid_date_count: int | None = None
    likely_identifier: bool = False
    identifier_detection: str | None = None


class DatasetProfile(BaseModel):
    """Deterministic statistics plus clearly separated optional inferences."""

    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    dataset_version_id: str
    pipeline_run_id: str
    row_count: int
    column_count: int
    schema_fingerprint: str
    columns: list[ColumnProfile]
    warnings: list[str] = []
    deterministic_findings: list[str] = []
    inferred_findings: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    profiler_version: str
    llm_provider_name: str | None = None
    llm_model_name: str | None = None
    prompt_version: str | None = None

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return JSON Schema for persistence and future integration validation."""
        return cls.model_json_schema()
