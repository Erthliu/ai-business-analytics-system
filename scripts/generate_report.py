"""Create or replay a V8 report from a persisted BusinessInsight."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.report_agent import ReportAgent
from config.settings import load_settings
from contracts.business import BusinessInsight
from contracts.critic import CriticReview
from contracts.reporting import Audience, ReportArtifact, ReportSpec
from database.models import BusinessInsightRecord, CriticReviewRecord
from database.persistence import create_database_engine
from reporting.assembler import ReportAssembler
from reporting.store import ReportArtifactStore, ReportSpecStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReportExecution:
    spec: ReportSpec
    report: ReportArtifact
    spec_replayed: bool
    report_replayed: bool


def execute_report(insight_id: str, database_url: str,
                   audience: Audience = Audience.EXECUTIVE) -> ReportExecution:
    """Load the V7/V6 gate, then create or replay a structured V8 report."""
    started = perf_counter()
    with Session(create_database_engine(database_url)) as session:
        insight_record = session.scalar(select(BusinessInsightRecord).where(BusinessInsightRecord.insight_id == insight_id))
        if insight_record is None: raise LookupError(f"BusinessInsight not found: {insight_id}")
        insight = BusinessInsight.model_validate(insight_record.insight_content)
        review_record = session.get(CriticReviewRecord, insight_record.critic_review_id)
        if review_record is None: raise LookupError("BusinessInsight CriticReview is missing.")
        review = CriticReview.model_validate(review_record.review_content)
        agent = ReportAgent()
        spec_store = ReportSpecStore(session)
        spec_identity = spec_store.identity(
            insight.insight_id, audience, agent.agent_version, agent.prompt_version,
            agent.report_rules_version,
            agent.provider.name if agent.provider else None,
            agent.provider.model_name if agent.provider else None,
        )
        spec = spec_store.get_by_identity(spec_identity)
        spec_replayed = spec is not None
        if spec is None:
            candidate = agent.create_spec(insight, review, audience)
            spec, spec_replayed = spec_store.get_or_create(candidate, spec_identity)
        assembler = ReportAssembler()
        artifact_store = ReportArtifactStore(session)
        artifact_identity = artifact_store.identity(
            spec.report_spec_id, insight.insight_id, assembler.renderer_version,
            spec.report_rules_version,
        )
        report = artifact_store.get_by_identity(artifact_identity)
        report_replayed = report is not None
        if report is None:
            candidate_report = assembler.assemble(spec, insight, review)
            report, report_replayed = artifact_store.get_or_create(candidate_report, artifact_identity)
        logger.info(
            "Report spec=%s report=%s insight=%s review=%s analysis=%s investigation=%s dataset=%s "
            "audience=%s agent=%s renderer=%s rules=%s sections=%d charts=%d tables=%d caveats=%d "
            "spec_replay=%s report_replay=%s elapsed=%.3fs",
            spec.report_spec_id, report.report_id, insight.insight_id, insight.critic_review_id,
            insight.analysis_id, insight.investigation_id, insight.dataset_version_id, audience,
            spec.agent_version, report.renderer_version, report.report_rules_version,
            len(report.rendered_sections), len(report.charts), len(report.tables), len(report.caveats),
            spec_replayed, report_replayed, perf_counter() - started,
        )
        return ReportExecution(spec, report, spec_replayed, report_replayed)


def _print_text(execution: ReportExecution) -> None:
    print(f"Status: {execution.report.status}")
    print()
    print(ReportAssembler.to_markdown(execution.report), end="")
    print(f"\nReplay: {str(execution.report_replayed).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("business_insight_id")
    parser.add_argument("--audience", choices=[item.value.lower() for item in Audience], default="executive")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url: raise RuntimeError("DATABASE_URL must be set.")
    execution = execute_report(
        args.business_insight_id, settings.database_url, Audience(args.audience.upper())
    )
    if args.json:
        print(json.dumps({
            "spec_replayed": execution.spec_replayed,
            "report_replayed": execution.report_replayed,
            "report_spec": execution.spec.model_dump(mode="json"),
            "report": execution.report.model_dump(mode="json"),
        }, indent=2))
    elif args.markdown:
        print(ReportAssembler.to_markdown(execution.report), end="")
    else:
        _print_text(execution)


if __name__ == "__main__":
    main()
