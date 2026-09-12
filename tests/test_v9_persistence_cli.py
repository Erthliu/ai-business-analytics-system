"""V9 migration, workflow persistence, event history, and CLI tests."""
import json

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from database.models import WorkflowRunRecord, WorkflowStageEventRecord
from database.persistence import create_database_engine
from orchestration.orchestrator import WorkflowOrchestrator
from orchestration.store import WorkflowStore
from scripts.resume_workflow import main as resume_main
from scripts.run_workflow import main as run_main
from scripts.workflow_status import main as status_main
from test_v4_persistence_cli import _database


def test_v9_migration_creates_workflow_and_event_tables(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'v9-migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert {"workflow_runs", "workflow_stage_events"} <= tables


def test_workflow_create_read_update_and_append_only_events(tmp_path):
    database_url, profile, _ = _database(tmp_path)
    orchestrator = WorkflowOrchestrator(database_url)
    workflow = orchestrator.start(profile.dataset_version_id, "total revenue")
    with Session(create_database_engine(database_url)) as session:
        assert session.query(WorkflowRunRecord).count() == 1
        assert session.query(WorkflowStageEventRecord).count() > 1
        persisted = WorkflowStore(session).get(workflow.workflow_run_id)
        events = WorkflowStore(session).events(workflow.workflow_run_id)
    assert persisted == workflow
    assert events[0].event_type == "WORKFLOW_CREATED"
    assert events[-1].event_type == "WORKFLOW_COMPLETED"
    assert [event.occurred_at for event in events] == sorted(event.occurred_at for event in events)


def test_run_and_status_cli_json(tmp_path, monkeypatch, capsys):
    database_url, profile, _ = _database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["run_workflow", profile.dataset_version_id,
                                     "total revenue", "--investigation", "never", "--json"])
    run_main()
    workflow = json.loads(capsys.readouterr().out)
    assert workflow["status"] == "COMPLETED"
    assert workflow["artifact_refs"]["report_artifact_id"]
    monkeypatch.setattr("sys.argv", ["workflow_status", workflow["workflow_run_id"], "--json"])
    status_main()
    status = json.loads(capsys.readouterr().out)
    assert status["workflow"]["status"] == "COMPLETED"
    assert status["events"][-1]["event_type"] == "WORKFLOW_COMPLETED"


def test_run_cli_markdown_uses_v8_renderer(tmp_path, monkeypatch, capsys):
    database_url, profile, _ = _database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["run_workflow", profile.dataset_version_id,
                                     "total revenue", "--markdown"])
    run_main()
    output = capsys.readouterr().out
    assert output.startswith("# Revenue Performance Report")
    assert "## Executive Summary" in output
    assert "## Evidence" in output


def test_resume_cli_after_clarification_and_status_text(tmp_path, monkeypatch, capsys):
    database_url, profile, _ = _database(tmp_path)
    orchestrator = WorkflowOrchestrator(database_url)
    waiting = orchestrator.start(profile.dataset_version_id, "Which product is best?")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["resume_workflow", waiting.workflow_run_id,
                                     "--clarified-question", "total revenue", "--json"])
    resume_main()
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "COMPLETED"
    monkeypatch.setattr("sys.argv", ["workflow_status", waiting.workflow_run_id])
    status_main()
    output = capsys.readouterr().out
    assert "Status: COMPLETED" in output
    assert "WORKFLOW_COMPLETED" in output
