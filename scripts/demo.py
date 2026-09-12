"""Run the canonical V1–V9 demonstration through production contracts."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from config.logging_config import configure_logging
from config.settings import load_settings
from contracts.orchestration import InvestigationPolicy
from orchestration.orchestrator import WorkflowOrchestrator
from pipeline.execution import execute_pipeline
from profiling.profiler import profile_dataset_version
from scripts.run_workflow import final_markdown


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = PROJECT_ROOT / "data" / "sample" / "sales.csv"
QUESTION = "Compare August 2024 revenue with July 2024 revenue."


def _migrate(database_url: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def main() -> None:
    """Migrate, ingest, profile, run twice, and print the final Markdown report."""
    settings = load_settings()
    configure_logging(settings.log_level)
    database_url = settings.require_database_url()
    _migrate(database_url)

    ingestion = execute_pipeline(
        SAMPLE_CSV,
        database_url,
        required_columns=(
            "order_id", "order_date", "customer_id", "product_id", "region",
            "quantity", "unit_price", "revenue",
        ),
        numeric_columns=("quantity", "unit_price", "revenue"),
        unique_columns=("order_id",),
        date_columns=("order_date",),
        numeric_ranges={
            "quantity": (0, None), "unit_price": (0, None), "revenue": (0, None)
        },
        apply_sales_policy=True,
    )
    if ingestion.status not in {"completed", "WARN"} or ingestion.dataset_version is None:
        raise RuntimeError(f"Sample ingestion did not complete: {ingestion.status}.")

    profile = profile_dataset_version(ingestion.dataset_version, database_url)
    orchestrator = WorkflowOrchestrator(database_url)
    first = orchestrator.start(
        profile.dataset_version_id, QUESTION,
        investigation_policy=InvestigationPolicy.REQUIRED,
    )
    if not first.artifact_refs.report_artifact_id:
        raise RuntimeError(f"Golden workflow did not produce a report: {first.status}.")
    second = orchestrator.start(
        profile.dataset_version_id, QUESTION,
        investigation_policy=InvestigationPolicy.REQUIRED,
    )
    replayed = [record.stage.value for record in second.stage_records if record.replayed]

    print("Golden demo")
    print(f"Pipeline run ID: {ingestion.run_id}")
    print(f"Source replayed: {str(ingestion.idempotent_replay).lower()}")
    print(f"Dataset version ID: {profile.dataset_version_id}")
    print(f"Workflow run ID: {first.workflow_run_id}")
    print(f"Workflow status: {first.status}")
    print(f"Replay workflow run ID: {second.workflow_run_id}")
    print(f"Replayed stages: {', '.join(replayed) or 'none'}")
    print()
    print(final_markdown(first, database_url), end="")


if __name__ == "__main__":
    main()
