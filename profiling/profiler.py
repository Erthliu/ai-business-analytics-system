"""Deterministic, read-only profiler with optional safe enrichment."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from time import perf_counter
from urllib.parse import unquote, urlparse

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts.dataset_profile import CategoricalValue, ColumnProfile, DatasetProfile, NumericStatistics
from database.models import DatasetProfileRecord, DatasetVersion, PipelineRun
from database.persistence import create_database_engine
from profiling.exceptions import DatasetNotProfileableError
from profiling.llm import LLMProvider

logger = logging.getLogger(__name__)
SENSITIVE_TOKENS = ("email", "phone", "address", "name", "customer", "ssn")


def _date_profile(series: pd.Series, name: str) -> tuple[str | None, str | None, int | None]:
    """Return date range only for datetime fields or clearly date-named fields."""
    if not (pd.api.types.is_datetime64_any_dtype(series) or "date" in name.lower()):
        return None, None, None
    parsed = pd.to_datetime(series, errors="coerce")
    valid = parsed.dropna()
    return (
        valid.min().date().isoformat() if not valid.empty else None,
        valid.max().date().isoformat() if not valid.empty else None,
        int((series.notna() & parsed.isna()).sum()),
    )


def profile_dataframe(
    dataframe: pd.DataFrame, dataset_version_id: str, pipeline_run_id: str,
    schema_fingerprint: str, profiler_version: str,
) -> DatasetProfile:
    """Build authoritative statistics without modifying the DataFrame."""
    rows = len(dataframe)
    columns: list[ColumnProfile] = []
    warnings: list[str] = []
    for name in dataframe.columns:
        series = dataframe[name]
        unique_count = int(series.nunique(dropna=True))
        null_count = int(series.isna().sum())
        likely_identifier = name.lower().endswith("_id") or (rows > 0 and unique_count / rows >= 0.98)
        numeric = None
        top_values: list[CategoricalValue] = []
        if pd.api.types.is_numeric_dtype(series):
            values = series.dropna()
            numeric = NumericStatistics(
                minimum=float(values.min()) if not values.empty else None,
                maximum=float(values.max()) if not values.empty else None,
                mean=float(values.mean()) if not values.empty else None,
                median=float(values.median()) if not values.empty else None,
                standard_deviation=float(values.std()) if len(values) > 1 else None,
                p25=float(values.quantile(.25)) if not values.empty else None,
                p75=float(values.quantile(.75)) if not values.empty else None,
                zero_count=int((values == 0).sum()), negative_count=int((values < 0).sum()),
            )
        else:
            for value, count in series.dropna().astype(str).value_counts().head(10).items():
                top_values.append(CategoricalValue(value=value, frequency=int(count)))
        minimum_date, maximum_date, invalid_dates = _date_profile(series, name)
        if null_count:
            warnings.append(f"Column '{name}' contains {null_count} null value(s).")
        columns.append(ColumnProfile(
            name=name, dtype=str(series.dtype), null_count=null_count,
            null_percentage=round((null_count / rows * 100) if rows else 0, 2),
            unique_count=unique_count, uniqueness_ratio=round((unique_count / rows) if rows else 0, 4),
            numeric=numeric, top_values=top_values, minimum_date=minimum_date,
            maximum_date=maximum_date, invalid_date_count=invalid_dates,
            likely_identifier=likely_identifier,
            identifier_detection="heuristic: name suffix or high uniqueness" if likely_identifier else None,
        ))
    findings = [f"{rows} rows across {len(columns)} columns."]
    return DatasetProfile(
        dataset_version_id=dataset_version_id, pipeline_run_id=pipeline_run_id,
        row_count=rows, column_count=len(columns), schema_fingerprint=schema_fingerprint,
        columns=columns, warnings=warnings, deterministic_findings=findings,
        profiler_version=profiler_version,
    )


def sanitize_profile_metadata(profile: DatasetProfile) -> dict[str, object]:
    """Expose only aggregate, PII-sanitized metadata to an optional provider."""
    columns = []
    for column in profile.columns:
        sensitive = any(token in column.name.lower() for token in SENSITIVE_TOKENS)
        columns.append({
            "name": column.name, "dtype": column.dtype, "null_percentage": column.null_percentage,
            "unique_count": column.unique_count,
            "top_values": [] if sensitive else [item.model_dump() for item in column.top_values],
            "numeric": column.numeric.model_dump() if column.numeric else None,
        })
    return {"row_count": profile.row_count, "column_count": profile.column_count, "columns": columns}


def profile_dataset_version(
    dataset_version_id: str, database_url: str, profiler_version: str = "2.0.0",
    provider: LLMProvider | None = None,
) -> DatasetProfile:
    """Load an eligible retained source, profile it, and persist once per version."""
    started = perf_counter()
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        version = session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == dataset_version_id))
        if version is None:
            raise LookupError(f"Dataset version not found: {dataset_version_id}")
        run = session.get(PipelineRun, version.pipeline_run_id)
        if run is None or run.status in {"rejected", "quarantined"}:
            raise DatasetNotProfileableError(f"Dataset version {dataset_version_id} is blocked: {run.status if run else 'missing run'}.")
        existing = session.scalar(select(DatasetProfileRecord).where(
            DatasetProfileRecord.dataset_version_id == version.id,
            DatasetProfileRecord.profiler_version == profiler_version,
        ))
        if existing:
            return DatasetProfile.model_validate(existing.profile_content)
        raw_path = unquote(urlparse(version.storage_uri).path)
        if len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        source_path = Path(raw_path)
        dataframe = pd.read_csv(source_path)
        profile = profile_dataframe(dataframe, version.version_id, run.run_id, version.schema_fingerprint, profiler_version)
        if provider is not None:
            try:
                findings = provider.generate_structured(sanitize_profile_metadata(profile))
                if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
                    raise ValueError("provider returned invalid findings")
                profile.inferred_findings = findings
                profile.llm_provider_name = provider.name
                profile.llm_model_name = provider.model_name
                profile.prompt_version = "profile-metadata-v1"
            except Exception as error:
                logger.warning("Optional profile enrichment failed with %s", type(error).__name__)
        session.add(DatasetProfileRecord(
            profile_id=profile.profile_id, dataset_version_id=version.id, pipeline_run_id=run.id,
            profiler_version=profiler_version, profile_content=profile.model_dump(mode="json"),
        ))
        session.commit()
    logger.info("Profile %s completed for dataset %s in %.3fs; enrichment=%s", profile.profile_id, dataset_version_id, perf_counter() - started, provider is not None and bool(profile.inferred_findings))
    return profile
