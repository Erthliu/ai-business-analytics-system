"""Run or replay one persisted READY analysis request."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.analytics_engine import AnalyticsEngine
from agents.analytics_planner import AnalyticsPlanner
from analytics.plan_store import AnalysisPlanStore
from analytics.result_store import ComputedAnalysisStore
from config.settings import load_settings
from contracts.analysis_request import AnalysisRequest, RequestStatus
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import DatasetProfile
from database.models import AnalysisRequestRecord, DatasetProfileRecord, DatasetVersion, PipelineRun
from database.persistence import create_database_engine


@dataclass(frozen=True, slots=True)
class AnalysisExecution:
    result: ComputedAnalysis
    plan_replayed: bool
    analysis_replayed: bool


def _local_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("V4 execution supports only filesystem-backed raw storage.")
    value = unquote(parsed.path)
    if len(value) > 2 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    return Path(value)


def execute_request(request_id: str, database_url: str) -> AnalysisExecution:
    """Load, validate, execute, persist, and replay a READY request."""
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        request_record = session.scalar(select(AnalysisRequestRecord).where(AnalysisRequestRecord.request_id == request_id))
        if request_record is None:
            raise LookupError(f"Analysis request not found: {request_id}")
        request = AnalysisRequest.model_validate(request_record.request_content)
        if request.status != RequestStatus.READY:
            raise ValueError(f"Analysis request {request_id} is not READY: {request.status}.")
        profile_record = session.get(DatasetProfileRecord, request_record.profile_id)
        version = session.get(DatasetVersion, request_record.dataset_version_id)
        run = session.get(PipelineRun, request_record.pipeline_run_id)
        if profile_record is None or version is None or run is None:
            raise LookupError("Persisted request provenance is incomplete.")
        if run.status != "completed":
            raise ValueError(f"Dataset version is not analytics-eligible: {run.status}.")
        profile = DatasetProfile.model_validate(profile_record.profile_content)
        planner = AnalyticsPlanner()
        plan_identity = AnalysisPlanStore.identity(
            request, planner.planner_version, planner.prompt_version, planner.registry.version,
        )
        plan_store = AnalysisPlanStore(session)
        plan = plan_store.get_by_identity(plan_identity)
        if plan is None:
            plan = planner.build(request, profile)
            plan, plan_replayed = plan_store.get_or_create(plan, plan_identity)
        else:
            planner.validator.validate(plan, profile=profile)
            plan_replayed = True
        analytics_engine = AnalyticsEngine(planner.registry)
        result_identity = ComputedAnalysisStore.identity(
            plan.plan_id, version.version_id, version.content_hash,
            analytics_engine.engine_version, planner.registry.version,
        )
        result_store = ComputedAnalysisStore(session)
        result = result_store.get_by_identity(result_identity)
        if result is None:
            dataframe = pd.read_csv(_local_path(version.storage_uri))
            result = analytics_engine.execute(dataframe, plan, version.content_hash)
            result, analysis_replayed = result_store.get_or_create(result, result_identity)
        else:
            analysis_replayed = True
        return AnalysisExecution(result, plan_replayed, analysis_replayed)


def _print_text(execution: AnalysisExecution) -> None:
    result = execution.result
    print(f"Status: {result.execution_status}")
    print(f"Metric: {result.primary_metric}")
    print(f"Plan replayed: {str(execution.plan_replayed).lower()}")
    print(f"Analysis replayed: {str(execution.analysis_replayed).lower()}")
    if result.result_type == "SCALAR":
        print(f"Value: {result.rows[0][result.primary_metric]}")
        return
    if result.result_type == "PERIOD_COMPARISON":
        row = result.rows[0]
        if result.analysis_period and result.comparison_period:
            print(f"Analysis period: {result.analysis_period.start} to {result.analysis_period.end}")
            print(f"Comparison period: {result.comparison_period.start} to {result.comparison_period.end}")
        for label, key in (("Analysis value", "analysis_period_value"), ("Comparison value", "comparison_period_value"),
                           ("Absolute change", "absolute_change"), ("Percentage change", "percentage_change")):
            print(f"{label}: {row[key]}")
        return
    prefix = "Rank | " if result.result_type == "RANKING" else ""
    print(prefix + " | ".join(result.columns))
    for index, row in enumerate(result.rows, start=1):
        values = [str(row.get(column)) for column in result.columns]
        if result.result_type == "RANKING":
            values.insert(0, str(index))
        print(" | ".join(values))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_request_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be set.")
    execution = execute_request(args.analysis_request_id, settings.database_url)
    if args.json:
        print(json.dumps({
            "plan_replayed": execution.plan_replayed,
            "analysis_replayed": execution.analysis_replayed,
            "analysis": execution.result.model_dump(mode="json"),
        }, indent=2))
    else:
        _print_text(execution)


if __name__ == "__main__":
    main()
