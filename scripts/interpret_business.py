"""Create or replay a V7 BusinessInsight from a persisted CriticReview."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.business_interpretation_agent import BusinessInterpretationAgent
from business.store import BusinessInsightStore
from config.settings import load_settings
from contracts.business import BusinessInsight
from contracts.computed_analysis import ComputedAnalysis
from contracts.critic import CriticReview
from contracts.investigation import InvestigationResult
from database.models import ComputedAnalysisRecord, CriticReviewRecord, InvestigationResultRecord
from database.persistence import create_database_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BusinessExecution:
    insight: BusinessInsight
    replayed: bool


def execute_interpretation(review_id: str, database_url: str) -> BusinessExecution:
    """Load approved structured artifacts, then create or replay an insight."""
    started = perf_counter()
    with Session(create_database_engine(database_url)) as session:
        review_record = session.scalar(select(CriticReviewRecord).where(CriticReviewRecord.review_id == review_id))
        if review_record is None: raise LookupError(f"CriticReview not found: {review_id}")
        review = CriticReview.model_validate(review_record.review_content)
        analysis_record = session.get(ComputedAnalysisRecord, review_record.analysis_id)
        if analysis_record is None: raise LookupError("ComputedAnalysis is missing.")
        analysis = ComputedAnalysis.model_validate(analysis_record.analysis_content)
        investigation = None
        if review_record.investigation_id:
            investigation_record = session.get(InvestigationResultRecord, review_record.investigation_id)
            if investigation_record is None: raise LookupError("InvestigationResult is missing.")
            investigation = InvestigationResult.model_validate(investigation_record.result_content)
        agent = BusinessInterpretationAgent()
        identity = BusinessInsightStore.identity(
            review.review_id, analysis.analysis_id,
            investigation.investigation_id if investigation else None,
            agent.agent_version, agent.prompt_version, agent.business_rules_version,
            agent.provider.name if agent.provider else None, agent.provider.model_name if agent.provider else None,
        )
        store = BusinessInsightStore(session)
        existing = store.get_by_identity(identity)
        if existing:
            logger.info(
                "Business insight %s replayed review=%s analysis=%s investigation=%s dataset=%s "
                "agent=%s rules=%s provider=%s model=%s claims=%d caveats=%d replay=true elapsed=%.3fs",
                existing.insight_id, review_id, analysis.analysis_id, existing.investigation_id,
                existing.dataset_version_id, existing.agent_version, existing.business_rules_version,
                existing.provider_name, existing.model_name, len(existing.claims), len(existing.caveats),
                perf_counter() - started,
            )
            return BusinessExecution(existing, True)
        insight = agent.interpret(analysis, review, investigation)
        insight, replayed = store.get_or_create(insight, identity)
        logger.info(
            "Business insight %s stored review=%s analysis=%s investigation=%s dataset=%s "
            "agent=%s rules=%s provider=%s model=%s claims=%d caveats=%d replay=%s elapsed=%.3fs",
            insight.insight_id, review_id, analysis.analysis_id, insight.investigation_id,
            insight.dataset_version_id, insight.agent_version, insight.business_rules_version,
            insight.provider_name, insight.model_name, len(insight.claims), len(insight.caveats),
            str(replayed).lower(), perf_counter() - started,
        )
        return BusinessExecution(insight, replayed)


def _print_text(execution: BusinessExecution) -> None:
    insight = execution.insight
    print(f"Status: {insight.status}")
    print("\nHeadline:")
    print(insight.headline)
    print("\nSummary:")
    print(insight.executive_summary)
    print("\nKey insights:")
    for index, item in enumerate(insight.key_insights, start=1): print(f"{index}. {item.statement}")
    print("\nCaveats:")
    if not insight.caveats: print("- none")
    for caveat in insight.caveats: print(f"- {caveat}")
    print("\nUnanswered questions:")
    for question in insight.unanswered_questions: print(f"- {question}")
    print(f"\nReplay: {str(execution.replayed).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("critic_review_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url: raise RuntimeError("DATABASE_URL must be set.")
    execution = execute_interpretation(args.critic_review_id, settings.database_url)
    if args.json:
        print(json.dumps({"replayed": execution.replayed,
                          "insight": execution.insight.model_dump(mode="json")}, indent=2))
    else:
        _print_text(execution)


if __name__ == "__main__":
    main()
