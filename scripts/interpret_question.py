"""CLI for grounded requirement interpretation; no provider is configured here."""
import argparse
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from agents.requirement_agent import RequirementAgent
from agents.request_store import AnalysisRequestStore
from config.settings import load_settings
from database.models import DatasetProfileRecord, DatasetVersion
from database.persistence import create_database_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_version_id")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be set.")
    with Session(create_database_engine(settings.database_url)) as session:
        record = session.scalar(
            select(DatasetProfileRecord)
            .join_from(DatasetProfileRecord, DatasetVersion)
            .where(DatasetVersion.version_id == args.dataset_version_id)
        )
        if record is None:
            raise LookupError("No persisted DatasetProfile for this dataset version.")
        from contracts.dataset_profile import DatasetProfile
        profile = DatasetProfile.model_validate(record.profile_content)
        agent = RequirementAgent()
        request = agent.interpret(args.question, profile)
        request = AnalysisRequestStore(session).get_or_create(request, agent.identity(args.question, profile, agent.agent_version, agent.prompt_version))
    if args.json:
        print(json.dumps(request.model_dump(mode="json"), indent=2))
    else:
        print(f"Status: {request.status}")
        if request.status == "READY":
            print(f"Primary metric: {request.primary_metric}")
            print(f"Dimensions: {', '.join(request.dimensions) or 'none'}")
        else:
            print("Questions:")
            for item in request.clarification_questions:
                print(f"- {item.question}")


if __name__ == "__main__":
    main()
