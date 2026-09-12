"""Create or replay a deterministic V5 investigation from a V4 analysis ID."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.investigation_agent import InvestigationAgent
from config.settings import load_settings
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import InvestigationResult, InvestigationType
from database.models import AnalysisPlanRecord, AnalysisRequestRecord, ComputedAnalysisRecord, DatasetProfileRecord, DatasetVersion
from database.persistence import create_database_engine
from investigation.engine import InvestigationEngine
from investigation.stores import InvestigationPlanStore, InvestigationResultStore
from scripts.run_analysis import _local_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvestigationExecution:
    result: InvestigationResult
    plan_replayed: bool
    investigation_replayed: bool


def execute_investigation(analysis_id: str, database_url: str, *, dimensions: list[str] | None = None,
                          top_n: int = 5,
                          investigation_type: InvestigationType = InvestigationType.CHANGE_DECOMPOSITION) -> InvestigationExecution:
    """Load V4 provenance, then plan, validate, execute, persist, or replay V5."""
    database_engine = create_database_engine(database_url)
    with Session(database_engine) as session:
        analysis_record = session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == analysis_id))
        if analysis_record is None:
            raise LookupError(f"Computed analysis not found: {analysis_id}")
        analysis = ComputedAnalysis.model_validate(analysis_record.analysis_content)
        analysis_plan_record = session.get(AnalysisPlanRecord, analysis_record.plan_id)
        if analysis_plan_record is None:
            raise LookupError("Source AnalysisPlan is missing.")
        analysis_plan = AnalysisPlan.model_validate(analysis_plan_record.plan_content)
        request_record = session.get(AnalysisRequestRecord, analysis_plan_record.request_id)
        version = session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == analysis.dataset_version_id))
        if request_record is None or version is None:
            raise LookupError("Source request or dataset version is missing.")
        request = AnalysisRequest.model_validate(request_record.request_content)
        profile_record = session.get(DatasetProfileRecord, request_record.profile_id)
        if profile_record is None:
            raise LookupError("Source DatasetProfile is missing.")
        profile = DatasetProfile.model_validate(profile_record.profile_content)
        agent = InvestigationAgent()
        candidate = agent.build(request, analysis_plan, analysis, profile,
                                selected_dimensions=dimensions, investigation_type=investigation_type, top_n=top_n)
        plan_store = InvestigationPlanStore(session)
        plan, plan_replayed = plan_store.get_or_create(candidate)
        engine = InvestigationEngine(agent.registry)
        identity = InvestigationResultStore.identity(plan.investigation_plan_id, version.content_hash, engine.engine_version)
        result_store = InvestigationResultStore(session)
        result = result_store.get_by_identity(identity)
        if result is None:
            dataframe = pd.read_csv(_local_path(version.storage_uri))
            result = engine.execute(dataframe, plan, request, analysis_plan, analysis, profile, version.content_hash)
            result, investigation_replayed = result_store.get_or_create(result, identity)
        else:
            investigation_replayed = True
        logger.info("Investigation plan=%s analysis=%s plan_replayed=%s result_replayed=%s",
                    plan.investigation_plan_id, analysis_id, plan_replayed, investigation_replayed)
        return InvestigationExecution(result, plan_replayed, investigation_replayed)


def _print_text(execution: InvestigationExecution) -> None:
    result = execution.result
    print(f"Investigation: {result.investigation_type}")
    print(f"Status: {result.status}")
    print(f"Metric: {result.target_metric}")
    print(f"Plan replayed: {str(execution.plan_replayed).lower()}")
    print(f"Investigation replayed: {str(execution.investigation_replayed).lower()}")
    for dimension in result.dimension_results:
        print(f"\nTop contributors by {dimension.dimension}:")
        for group in dimension.groups[:result.top_n]:
            print(f"{group.rank}. {group.value} | change={group.absolute_change:.2f} | contribution={group.contribution_percent if group.contribution_percent is not None else 'undefined'} | direction={group.contribution_direction}")
    print("\nReconciliation:")
    for item in result.reconciliation:
        state = "PASS" if item.reconciled else "FAIL"
        print(f"{item.dimension} | expected={item.expected_total_change:.2f} | decomposed={item.decomposed_total_change:.2f} | status={state}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_id")
    parser.add_argument("--dimension", action="append", dest="dimensions")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--type", choices=[item.value for item in InvestigationType], default=InvestigationType.CHANGE_DECOMPOSITION.value)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be set.")
    execution = execute_investigation(args.analysis_id, settings.database_url, dimensions=args.dimensions,
                                      top_n=args.top, investigation_type=InvestigationType(args.type))
    if args.json:
        print(json.dumps({"plan_replayed": execution.plan_replayed,
                          "investigation_replayed": execution.investigation_replayed,
                          "investigation": execution.result.model_dump(mode="json")}, indent=2))
    else:
        _print_text(execution)


if __name__ == "__main__":
    main()
