"""V9 state machine, routing, lifecycle, recovery, and safety tests."""
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from contracts.orchestration import (
    InvestigationPolicy, InvestigationRoutingConfig, StageRecord, WorkflowEvent,
    WorkflowFailure, WorkflowRun, WorkflowStage, WorkflowStatus,
)
from database.models import (
    ComputedAnalysisRecord, PipelineRun, ReportArtifactRecord, ReportSpecRecord,
)
from database.persistence import create_database_engine
from orchestration.orchestrator import WorkflowOrchestrator
from orchestration.retry import RetryPolicy
from orchestration.stages import analysis_execution_stage
from orchestration.state_machine import InvalidTransitionError, WorkflowStateMachine
from orchestration.store import StaleWorkflowVersionError, WorkflowStore
from test_v4_persistence_cli import _database


COMPARISON = "Compare August 2024 revenue with July 2024 revenue"


def _orchestrator(tmp_path: Path, retry_policy=None):
    database_url, profile, _ = _database(tmp_path)
    return WorkflowOrchestrator(database_url, retry_policy), database_url, profile


def _stage(workflow, stage):
    return next(item for item in workflow.stage_records if item.stage == stage)


def test_state_machine_valid_path_and_invalid_transition():
    machine = WorkflowStateMachine()
    path = [
        WorkflowStage.REQUIREMENT, WorkflowStage.ANALYSIS_PLAN,
        WorkflowStage.ANALYSIS_EXECUTION, WorkflowStage.INVESTIGATION_DECISION,
        WorkflowStage.INVESTIGATION, WorkflowStage.CRITIC,
        WorkflowStage.BUSINESS_INTERPRETATION, WorkflowStage.REPORT,
        WorkflowStage.COMPLETE,
    ]
    for current, target in zip(path, path[1:]): assert machine.transition(current, target) == target
    with pytest.raises(InvalidTransitionError):
        machine.transition(WorkflowStage.REQUIREMENT, WorkflowStage.REPORT)
    assert machine.status_transition(WorkflowStatus.PENDING, WorkflowStatus.RUNNING) == WorkflowStatus.RUNNING
    with pytest.raises(InvalidTransitionError):
        machine.status_transition(WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING)


@pytest.mark.parametrize("question,expected_type", [
    ("total revenue", "SCALAR"),
    (COMPARISON, "PERIOD_COMPARISON"),
    ("top 3 revenue by product_id", "RANKING"),
    ("monthly revenue", "TIME_SERIES"),
])
def test_normal_end_to_end_result_shapes(tmp_path, question, expected_type):
    orchestrator, database_url, profile = _orchestrator(tmp_path)
    workflow = orchestrator.start(profile.dataset_version_id, question)
    assert workflow.status == WorkflowStatus.COMPLETED
    assert workflow.current_stage == WorkflowStage.COMPLETE
    assert workflow.artifact_refs.report_artifact_id
    with Session(create_database_engine(database_url)) as session:
        record = session.query(ComputedAnalysisRecord).filter_by(
            analysis_id=workflow.artifact_refs.computed_analysis_id
        ).one()
        assert record.analysis_content["result_type"] == expected_type
    expected_investigation = expected_type == "PERIOD_COMPARISON"
    assert bool(workflow.artifact_refs.investigation_result_id) == expected_investigation


def test_investigation_auto_never_required_and_thresholds(tmp_path):
    auto_path = tmp_path / "auto"; auto_path.mkdir()
    orchestrator, _, profile = _orchestrator(auto_path)
    auto = orchestrator.start(profile.dataset_version_id, COMPARISON)
    assert auto.artifact_refs.investigation_result_id

    never_path = tmp_path / "never"; never_path.mkdir()
    orchestrator, _, profile = _orchestrator(never_path)
    never = orchestrator.start(profile.dataset_version_id, COMPARISON,
                               investigation_policy=InvestigationPolicy.NEVER)
    assert not never.artifact_refs.investigation_result_id
    assert _stage(never, WorkflowStage.INVESTIGATION).status == "SKIPPED"

    threshold_path = tmp_path / "threshold"; threshold_path.mkdir()
    orchestrator, _, profile = _orchestrator(threshold_path)
    threshold = orchestrator.start(
        profile.dataset_version_id, COMPARISON,
        investigation_config=InvestigationRoutingConfig(minimum_absolute_change=1000),
    )
    assert _stage(threshold, WorkflowStage.INVESTIGATION).status == "SKIPPED"

    required_path = tmp_path / "required"; required_path.mkdir()
    orchestrator, _, profile = _orchestrator(required_path)
    required = orchestrator.start(profile.dataset_version_id, COMPARISON,
                                  investigation_policy=InvestigationPolicy.REQUIRED)
    assert required.artifact_refs.investigation_result_id

    unsupported_path = tmp_path / "unsupported"; unsupported_path.mkdir()
    orchestrator, _, profile = _orchestrator(unsupported_path)
    unsupported = orchestrator.start(profile.dataset_version_id, "total revenue",
                                     investigation_policy=InvestigationPolicy.REQUIRED)
    assert unsupported.status == WorkflowStatus.FAILED
    assert not unsupported.failure.retryable


def test_clarification_wait_and_resume_preserves_first_request_event(tmp_path):
    orchestrator, _, profile = _orchestrator(tmp_path)
    waiting = orchestrator.start(profile.dataset_version_id, "Which product is best?")
    assert waiting.status == WorkflowStatus.WAITING_FOR_CLARIFICATION
    first_request = waiting.artifact_refs.analysis_request_id
    assert waiting.clarification_questions
    resumed = orchestrator.resume(waiting.workflow_run_id, clarified_question="total revenue")
    assert resumed.status == WorkflowStatus.COMPLETED
    assert resumed.artifact_refs.analysis_request_id != first_request
    event_artifacts = [artifact for event in orchestrator.events(waiting.workflow_run_id)
                       for artifact in event.artifact_refs]
    assert first_request in event_artifacts
    assert _stage(resumed, WorkflowStage.REQUIREMENT).attempt == 2


def test_second_workflow_is_distinct_but_every_stage_artifact_replays(tmp_path):
    orchestrator, _, profile = _orchestrator(tmp_path)
    first = orchestrator.start(profile.dataset_version_id, COMPARISON)
    second = orchestrator.start(profile.dataset_version_id, COMPARISON)
    assert first.workflow_run_id != second.workflow_run_id
    assert first.workflow_identity_hash == second.workflow_identity_hash
    assert first.artifact_refs == second.artifact_refs
    for stage in (WorkflowStage.REQUIREMENT, WorkflowStage.ANALYSIS_PLAN,
                  WorkflowStage.ANALYSIS_EXECUTION, WorkflowStage.INVESTIGATION,
                  WorkflowStage.CRITIC, WorkflowStage.BUSINESS_INTERPRETATION,
                  WorkflowStage.REPORT):
        assert _stage(second, stage).replayed


def test_workflow_identity_includes_routing_configuration():
    identity = WorkflowStore.identity
    first = identity("dataset", "total revenue", "EXECUTIVE", "AUTO", "9", "1", 0, 0, "1")
    assert first != identity("dataset", "total revenue", "EXECUTIVE", "AUTO", "9", "1", 10, 0, "1")
    assert first != identity("dataset", "total revenue", "EXECUTIVE", "AUTO", "9", "1", 0, 0, "2")


def test_resume_after_analysis_and_crash_gap_reuses_artifact(tmp_path):
    orchestrator, database_url, profile = _orchestrator(tmp_path)
    workflow = orchestrator.create_workflow(profile.dataset_version_id, COMPARISON)
    workflow = orchestrator.run(workflow.workflow_run_id, max_stages=2)
    assert workflow.current_stage == WorkflowStage.ANALYSIS_EXECUTION
    persisted = analysis_execution_stage(workflow.artifact_refs.analysis_request_id, database_url)
    resumed = orchestrator.resume(workflow.workflow_run_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert resumed.artifact_refs.computed_analysis_id == persisted.result.analysis_id
    assert _stage(resumed, WorkflowStage.ANALYSIS_EXECUTION).replayed


@pytest.mark.parametrize("stop_after,next_stage", [
    (5, WorkflowStage.CRITIC),
    (6, WorkflowStage.BUSINESS_INTERPRETATION),
])
def test_resume_after_investigation_or_critic(tmp_path, stop_after, next_stage):
    orchestrator, _, profile = _orchestrator(tmp_path)
    workflow = orchestrator.create_workflow(profile.dataset_version_id, COMPARISON)
    paused = orchestrator.run(workflow.workflow_run_id, max_stages=stop_after)
    assert paused.current_stage == next_stage
    refs = paused.artifact_refs.model_copy()
    completed = orchestrator.resume(paused.workflow_run_id)
    assert completed.status == WorkflowStatus.COMPLETED
    for name, value in refs.model_dump().items():
        if value: assert getattr(completed.artifact_refs, name) == value


def test_critic_fail_blocks_business_and_report(tmp_path):
    orchestrator, database_url, profile = _orchestrator(tmp_path)
    workflow = orchestrator.create_workflow(
        profile.dataset_version_id, COMPARISON,
        investigation_policy=InvestigationPolicy.NEVER,
    )
    workflow = orchestrator.run(workflow.workflow_run_id, max_stages=4)
    assert workflow.current_stage == WorkflowStage.CRITIC
    with Session(create_database_engine(database_url)) as session:
        record = session.query(ComputedAnalysisRecord).filter_by(
            analysis_id=workflow.artifact_refs.computed_analysis_id
        ).one()
        content = dict(record.analysis_content)
        rows = [dict(item) for item in content["rows"]]
        rows[0]["absolute_change"] = 999
        content["rows"] = rows
        record.analysis_content = content
        session.commit()
    blocked = orchestrator.run(workflow.workflow_run_id)
    assert blocked.status == WorkflowStatus.BLOCKED
    assert blocked.failure.error_code == "CRITIC_REJECTED"
    assert not blocked.artifact_refs.business_insight_id
    assert not blocked.artifact_refs.report_artifact_id
    assert orchestrator.events(blocked.workflow_run_id)[-1].event_type == "WORKFLOW_BLOCKED"


def test_warning_propagation_and_final_status(tmp_path):
    database_url, profile, _ = _database(tmp_path)
    from database.models import DatasetProfileRecord
    with Session(create_database_engine(database_url)) as session:
        record = session.query(DatasetProfileRecord).one()
        content = dict(record.profile_content)
        content["warnings"] = ["Partial period coverage detected."]
        record.profile_content = content
        session.commit()
    workflow = WorkflowOrchestrator(database_url).start(profile.dataset_version_id, "total revenue")
    assert workflow.status == WorkflowStatus.COMPLETED_WITH_WARNINGS
    assert "Partial period coverage detected." in workflow.warnings


def test_nonretryable_semantic_and_blocked_dataset_failures(tmp_path):
    semantic_path = tmp_path / "semantic"; semantic_path.mkdir()
    orchestrator, _, profile = _orchestrator(semantic_path)
    semantic = orchestrator.start(profile.dataset_version_id, "total quantity")
    assert semantic.status == WorkflowStatus.FAILED and not semantic.failure.retryable

    blocked_path = tmp_path / "blocked"; blocked_path.mkdir()
    orchestrator, database_url, profile = _orchestrator(blocked_path)
    with Session(create_database_engine(database_url)) as session:
        session.query(PipelineRun).one().status = "rejected"
        session.commit()
    blocked = orchestrator.start(profile.dataset_version_id, "total revenue")
    assert blocked.status == WorkflowStatus.FAILED


def test_explicit_bounded_retry_for_infrastructure_error(tmp_path, monkeypatch):
    orchestrator, _, profile = _orchestrator(tmp_path, RetryPolicy(max_attempts=2))
    import orchestration.orchestrator as module
    original = module.analysis_execution_stage
    calls = {"count": 0}
    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1: raise OSError("temporary")
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "analysis_execution_stage", flaky)
    workflow = orchestrator.start(profile.dataset_version_id, "total revenue")
    assert workflow.status == WorkflowStatus.COMPLETED_WITH_WARNINGS
    assert _stage(workflow, WorkflowStage.ANALYSIS_EXECUTION).attempt == 2


def test_optimistic_concurrency_rejects_stale_snapshot(tmp_path):
    orchestrator, database_url, profile = _orchestrator(tmp_path)
    created = orchestrator.create_workflow(profile.dataset_version_id, "total revenue")
    first = orchestrator.get(created.workflow_run_id)
    stale = orchestrator.get(created.workflow_run_id)
    with Session(create_database_engine(database_url)) as session:
        WorkflowStore(session).save(first, first.version)
    with Session(create_database_engine(database_url)) as session:
        with pytest.raises(StaleWorkflowVersionError):
            WorkflowStore(session).save(stale, stale.version)


def test_contract_schemas_strict():
    for contract in (WorkflowRun, StageRecord, WorkflowFailure, WorkflowEvent):
        assert contract.model_json_schema()["additionalProperties"] is False


def test_missing_upstream_reference_fails_without_downstream_work(tmp_path):
    orchestrator, database_url, profile = _orchestrator(tmp_path)
    workflow = orchestrator.create_workflow(profile.dataset_version_id, "total revenue")
    workflow = orchestrator.run(workflow.workflow_run_id, max_stages=1)
    broken = workflow.model_copy(deep=True)
    broken.artifact_refs.analysis_request_id = None
    with Session(create_database_engine(database_url)) as session:
        WorkflowStore(session).save(broken, broken.version)
    failed = orchestrator.run(workflow.workflow_run_id)
    assert failed.status == WorkflowStatus.FAILED
    assert failed.failure.error_code == "LOOKUPERROR"
    assert not failed.artifact_refs.analysis_plan_id
    assert not failed.artifact_refs.report_artifact_id


def test_filesystem_read_failure_is_diagnosable(tmp_path):
    orchestrator, database_url, profile = _orchestrator(tmp_path)
    (tmp_path / "sales.csv").unlink()
    workflow = orchestrator.start(profile.dataset_version_id, "total revenue")
    assert workflow.status == WorkflowStatus.FAILED
    assert workflow.failure.retryable
    assert workflow.current_stage == WorkflowStage.ANALYSIS_EXECUTION
    assert not workflow.artifact_refs.computed_analysis_id


def test_report_persistence_failure_recovers_without_fabricated_success(tmp_path, monkeypatch):
    orchestrator, database_url, profile = _orchestrator(
        tmp_path, RetryPolicy(max_attempts=2)
    )
    workflow = orchestrator.create_workflow(profile.dataset_version_id, "total revenue")
    workflow = orchestrator.run(workflow.workflow_run_id, max_stages=6)
    assert workflow.current_stage == WorkflowStage.REPORT
    from reporting.store import ReportArtifactStore
    original = ReportArtifactStore.get_or_create

    def fail_persistence(*args, **kwargs):
        raise OSError("report storage unavailable")

    monkeypatch.setattr(ReportArtifactStore, "get_or_create", fail_persistence)
    interrupted = orchestrator.advance(workflow.workflow_run_id)
    assert interrupted.status == WorkflowStatus.RUNNING
    assert interrupted.failure.retryable
    assert not interrupted.artifact_refs.report_artifact_id
    with Session(create_database_engine(database_url)) as session:
        assert session.query(ReportSpecRecord).count() == 1
        assert session.query(ReportArtifactRecord).count() == 0

    monkeypatch.setattr(ReportArtifactStore, "get_or_create", original)
    completed = orchestrator.run(workflow.workflow_run_id)
    assert completed.status == WorkflowStatus.COMPLETED_WITH_WARNINGS
    assert completed.artifact_refs.report_artifact_id
    assert _stage(completed, WorkflowStage.REPORT).attempt == 2


def test_database_error_before_stage_claim_leaves_persisted_state(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    orchestrator, _, profile = _orchestrator(tmp_path)
    workflow = orchestrator.create_workflow(profile.dataset_version_id, "total revenue")
    original_save = orchestrator._save

    def unavailable(*args, **kwargs):
        raise OperationalError("UPDATE workflow_runs", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(orchestrator, "_save", unavailable)
    with pytest.raises(OperationalError):
        orchestrator.advance(workflow.workflow_run_id)
    monkeypatch.setattr(orchestrator, "_save", original_save)
    persisted = orchestrator.get(workflow.workflow_run_id)
    assert persisted.status == WorkflowStatus.PENDING
    assert not persisted.artifact_refs.analysis_request_id
