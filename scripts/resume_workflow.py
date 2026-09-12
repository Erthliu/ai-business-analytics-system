"""Resume an interrupted or clarification-waiting V9 workflow."""
import argparse
import json

from config.settings import load_settings
from orchestration.orchestrator import WorkflowOrchestrator
from scripts.run_workflow import final_markdown, print_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_run_id")
    parser.add_argument("--clarified-question")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url: raise RuntimeError("DATABASE_URL must be set.")
    workflow = WorkflowOrchestrator(settings.database_url).resume(
        args.workflow_run_id, clarified_question=args.clarified_question
    )
    if args.json: print(json.dumps(workflow.model_dump(mode="json"), indent=2))
    elif args.markdown: print(final_markdown(workflow, settings.database_url), end="")
    else: print_workflow(workflow)


if __name__ == "__main__":
    main()
