"""Display a persisted V9 workflow and its append-only event history."""
import argparse
import json

from config.settings import load_settings
from orchestration.orchestrator import WorkflowOrchestrator
from scripts.run_workflow import print_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_run_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url: raise RuntimeError("DATABASE_URL must be set.")
    orchestrator = WorkflowOrchestrator(settings.database_url)
    workflow = orchestrator.get(args.workflow_run_id)
    events = orchestrator.events(args.workflow_run_id)
    if args.json:
        print(json.dumps({"workflow": workflow.model_dump(mode="json"),
                          "events": [item.model_dump(mode="json") for item in events]}, indent=2))
    else:
        print_workflow(workflow)
        print("\nEvents:")
        for event in events: print(f"- {event.event_type}: {event.stage}")


if __name__ == "__main__":
    main()
