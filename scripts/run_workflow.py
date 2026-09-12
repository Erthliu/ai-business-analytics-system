"""Run the controlled V9 analytics workflow end to end."""
from __future__ import annotations

import argparse
import json

from sqlalchemy.orm import Session

from config.settings import load_settings
from contracts.orchestration import InvestigationPolicy, InvestigationRoutingConfig, WorkflowRun
from contracts.reporting import Audience
from database.persistence import create_database_engine
from orchestration.orchestrator import WorkflowOrchestrator
from reporting.assembler import ReportAssembler
from reporting.store import ReportArtifactStore


def print_workflow(workflow: WorkflowRun) -> None:
    """Print concise workflow status, artifacts, and stage replay information."""
    print(f"Workflow ID: {workflow.workflow_run_id}")
    print(f"Status: {workflow.status}")
    print(f"Current stage: {workflow.current_stage}")
    print("\nArtifacts:")
    for name, value in workflow.artifact_refs.model_dump().items():
        if value: print(f"- {name}: {value}")
    print("\nStages:")
    for record in workflow.stage_records:
        print(f"- {record.stage}: {record.status} | replay={str(record.replayed).lower()} | attempt={record.attempt}")
    if workflow.clarification_questions:
        print("\nClarification questions:")
        for question in workflow.clarification_questions: print(f"- {question}")
    if workflow.warnings:
        print("\nWarnings:")
        for warning in workflow.warnings: print(f"- {warning}")
    if workflow.failure:
        print(f"\nFailure: [{workflow.failure.error_code}] {workflow.failure.message}")


def final_markdown(workflow: WorkflowRun, database_url: str) -> str:
    """Load and render the final V8 report without creating report logic in V9."""
    report_id = workflow.artifact_refs.report_artifact_id
    if not report_id: raise ValueError("Workflow has no completed ReportArtifact.")
    with Session(create_database_engine(database_url)) as session:
        report = ReportArtifactStore(session).get(report_id)
        if report is None: raise LookupError("Workflow ReportArtifact is missing.")
        return ReportAssembler.to_markdown(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_version_id")
    parser.add_argument("question")
    parser.add_argument("--audience", choices=[item.value.lower() for item in Audience], default="executive")
    parser.add_argument("--investigation", choices=[item.value.lower() for item in InvestigationPolicy], default="auto")
    parser.add_argument("--minimum-absolute-change", type=float, default=0)
    parser.add_argument("--minimum-percentage-change", type=float, default=0)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url: raise RuntimeError("DATABASE_URL must be set.")
    workflow = WorkflowOrchestrator(settings.database_url).start(
        args.dataset_version_id, args.question, Audience(args.audience.upper()),
        InvestigationPolicy(args.investigation.upper()),
        InvestigationRoutingConfig(
            minimum_absolute_change=args.minimum_absolute_change,
            minimum_percentage_change=args.minimum_percentage_change,
        ),
    )
    if args.json: print(json.dumps(workflow.model_dump(mode="json"), indent=2))
    elif args.markdown: print(final_markdown(workflow, settings.database_url), end="")
    else: print_workflow(workflow)


if __name__ == "__main__":
    main()
