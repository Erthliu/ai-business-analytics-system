"""Review a persisted V4 analysis, optionally including its V5 investigation."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.critic_agent import CriticAgent
from config.settings import load_settings
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from contracts.computed_analysis import ComputedAnalysis
from contracts.critic import CriticReview
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import InvestigationPlan, InvestigationResult
from database.models import (
    AnalysisPlanRecord, AnalysisRequestRecord, ComputedAnalysisRecord,
    DatasetProfileRecord, DatasetVersion, InvestigationPlanRecord, InvestigationResultRecord,
)
from database.persistence import create_database_engine
from qa.review_store import CriticReviewStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReviewExecution:
    review: CriticReview
    replayed: bool


def execute_review(analysis_id: str, database_url: str, investigation_id: str | None = None) -> ReviewExecution:
    """Load persisted artifacts, run or replay their versioned critic review."""
    with Session(create_database_engine(database_url)) as session:
        analysis_record = session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == analysis_id))
        if analysis_record is None: raise LookupError(f"Computed analysis not found: {analysis_id}")
        analysis = ComputedAnalysis.model_validate(analysis_record.analysis_content)
        plan_record = session.get(AnalysisPlanRecord, analysis_record.plan_id)
        if plan_record is None: raise LookupError("AnalysisPlan is missing.")
        plan = AnalysisPlan.model_validate(plan_record.plan_content)
        request_record = session.get(AnalysisRequestRecord, plan_record.request_id)
        if request_record is None: raise LookupError("AnalysisRequest is missing.")
        request = AnalysisRequest.model_validate(request_record.request_content)
        profile_record = session.get(DatasetProfileRecord, request_record.profile_id)
        version = session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == analysis.dataset_version_id))
        if profile_record is None or version is None: raise LookupError("Dataset profile or version is missing.")
        profile = DatasetProfile.model_validate(profile_record.profile_content)
        investigation_plan = investigation = None
        if investigation_id:
            investigation_record = session.scalar(select(InvestigationResultRecord).where(InvestigationResultRecord.investigation_id == investigation_id))
            if investigation_record is None: raise LookupError(f"Investigation result not found: {investigation_id}")
            investigation = InvestigationResult.model_validate(investigation_record.result_content)
            investigation_plan_record = session.get(InvestigationPlanRecord, investigation_record.investigation_plan_id)
            if investigation_plan_record is None: raise LookupError("InvestigationPlan is missing.")
            investigation_plan = InvestigationPlan.model_validate(investigation_plan_record.plan_content)
        agent = CriticAgent()
        identity = CriticReviewStore.identity(
            analysis.analysis_id, investigation.investigation_id if investigation else None,
            agent.critic_version, agent.qa_rules_version, agent.prompt_version,
            agent.provider.name if agent.provider else None, agent.provider.model_name if agent.provider else None,
        )
        store = CriticReviewStore(session)
        existing = store.get_by_identity(identity)
        if existing:
            logger.info("Critic review %s replayed analysis=%s investigation=%s", existing.review_id, analysis_id, investigation_id)
            return ReviewExecution(existing, True)
        review = agent.review(request, plan, analysis, profile, version.content_hash,
                              investigation_plan, investigation)
        review, replayed = store.get_or_create(review, identity)
        return ReviewExecution(review, replayed)


def _print_text(execution: ReviewExecution) -> None:
    review = execution.review
    print(f"Review: {review.overall_status}")
    print(f"Severity: {review.severity}")
    print(f"Confidence: {review.review_confidence}")
    print("\nChecks:")
    for check in review.deterministic_checks:
        print(f"- {check.check_name}: {check.status}")
    print("\nIssues:")
    if not review.issues: print("- none")
    for issue in review.issues:
        print(f"- [{issue.severity}] {issue.issue_code}: {issue.message}")
    for warning in review.warnings:
        print(f"- [WARNING] {warning}")
    print(f"\nReplay: {str(execution.replayed).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_id")
    parser.add_argument("--investigation-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url: raise RuntimeError("DATABASE_URL must be set.")
    execution = execute_review(args.analysis_id, settings.database_url, args.investigation_id)
    if args.json:
        print(json.dumps({"replayed": execution.replayed,
                          "review": execution.review.model_dump(mode="json")}, indent=2))
    else:
        _print_text(execution)


if __name__ == "__main__":
    main()
