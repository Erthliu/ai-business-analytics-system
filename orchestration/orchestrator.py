"""Controlled V9 orchestration over independently validated V3–V8 services."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts.computed_analysis import ComputedAnalysis
from contracts.critic import ReviewStatus
from contracts.dataset_profile import DatasetProfile
from contracts.orchestration import (
    InvestigationPolicy, InvestigationRoutingConfig, StageRecord, StageStatus,
    WorkflowArtifactRefs, WorkflowEvent, WorkflowEventType, WorkflowFailure,
    WorkflowRun, WorkflowStage, WorkflowStatus,
)
from contracts.reporting import Audience
from database.models import ComputedAnalysisRecord, DatasetProfileRecord, DatasetVersion
from database.persistence import create_database_engine
from orchestration import INVESTIGATION_ROUTING_RULES_VERSION, WORKFLOW_RULES_VERSION
from orchestration.retry import RetryPolicy
from orchestration.routing import InvestigationRouter
from orchestration.stages import (
    analysis_execution_stage, analysis_plan_stage, business_stage, critic_stage,
    investigation_stage, report_stage, requirement_stage,
)
from orchestration.state_machine import WorkflowStateMachine
from orchestration.store import WorkflowStore

logger = logging.getLogger(__name__)

_TERMINAL = {
    WorkflowStatus.WAITING_FOR_CLARIFICATION, WorkflowStatus.BLOCKED,
    WorkflowStatus.FAILED, WorkflowStatus.COMPLETED,
    WorkflowStatus.COMPLETED_WITH_WARNINGS,
}


class WorkflowOrchestrator:
    """Advance one explicit workflow stage at a time and persist every boundary."""

    orchestrator_version = "9.0.0"
    workflow_rules_version = WORKFLOW_RULES_VERSION

    def __init__(self, database_url: str, retry_policy: RetryPolicy | None = None) -> None:
        self.database_url = database_url
        self.retry_policy = retry_policy or RetryPolicy()
        self.machine = WorkflowStateMachine()
        self.router = InvestigationRouter()

    def create_workflow(self, dataset_version_id: str, question: str,
                        audience: Audience = Audience.EXECUTIVE,
                        investigation_policy: InvestigationPolicy = InvestigationPolicy.AUTO,
                        investigation_config: InvestigationRoutingConfig | None = None) -> WorkflowRun:
        """Create one distinct auditable run; stage artifacts may still replay."""
        with Session(create_database_engine(self.database_url)) as session:
            version = session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == dataset_version_id))
            if version is None: raise LookupError(f"DatasetVersion not found: {dataset_version_id}")
            profile_record = session.scalar(
                select(DatasetProfileRecord).where(DatasetProfileRecord.dataset_version_id == version.id)
                .order_by(DatasetProfileRecord.id.desc())
            )
            if profile_record is None: raise LookupError("No DatasetProfile exists for the dataset version.")
            profile = DatasetProfile.model_validate(profile_record.profile_content)
            config = investigation_config or InvestigationRoutingConfig(
                routing_rules_version=INVESTIGATION_ROUTING_RULES_VERSION
            )
            identity = WorkflowStore.identity(
                dataset_version_id, question, audience, investigation_policy,
                self.orchestrator_version, self.workflow_rules_version,
                config.minimum_absolute_change, config.minimum_percentage_change,
                config.routing_rules_version,
            )
            records = [StageRecord(stage=stage) for stage in WorkflowStage if stage != WorkflowStage.COMPLETE]
            workflow = WorkflowRun(
                workflow_identity_hash=identity, dataset_version_id=dataset_version_id,
                profile_id=profile.profile_id, original_question=question,
                audience=audience, investigation_policy=investigation_policy,
                investigation_config=config, stage_records=records,
                warnings=list(profile.warnings), orchestrator_version=self.orchestrator_version,
                workflow_rules_version=self.workflow_rules_version,
            )
            event = self._event(workflow, WorkflowStage.REQUIREMENT,
                                WorkflowEventType.WORKFLOW_CREATED)
            return WorkflowStore(session).create(workflow, event)

    def start(self, dataset_version_id: str, question: str,
              audience: Audience = Audience.EXECUTIVE,
              investigation_policy: InvestigationPolicy = InvestigationPolicy.AUTO,
              investigation_config: InvestigationRoutingConfig | None = None) -> WorkflowRun:
        """Create and run a workflow until it reaches a terminal state."""
        workflow = self.create_workflow(
            dataset_version_id, question, audience, investigation_policy, investigation_config
        )
        return self.run(workflow.workflow_run_id)

    def get(self, workflow_run_id: str) -> WorkflowRun:
        with Session(create_database_engine(self.database_url)) as session:
            workflow = WorkflowStore(session).get(workflow_run_id)
            if workflow is None: raise LookupError(f"WorkflowRun not found: {workflow_run_id}")
            return workflow

    def events(self, workflow_run_id: str) -> list[WorkflowEvent]:
        with Session(create_database_engine(self.database_url)) as session:
            return WorkflowStore(session).events(workflow_run_id)

    def run(self, workflow_run_id: str, *, max_stages: int | None = None) -> WorkflowRun:
        """Advance deterministically until terminal or an explicit stage limit."""
        started = perf_counter()
        completed = 0
        workflow = self.get(workflow_run_id)
        while workflow.status not in _TERMINAL and workflow.current_stage != WorkflowStage.COMPLETE:
            if max_stages is not None and completed >= max_stages: break
            workflow = self.advance(workflow.workflow_run_id)
            completed += 1
        logger.info(
            "Workflow %s status=%s stage=%s warnings=%d elapsed=%.3fs orchestrator=%s rules=%s",
            workflow.workflow_run_id, workflow.status, workflow.current_stage,
            len(workflow.warnings), perf_counter() - started,
            self.orchestrator_version, self.workflow_rules_version,
        )
        return workflow

    def resume(self, workflow_run_id: str, *, clarified_question: str | None = None) -> WorkflowRun:
        """Resume a paused or interrupted workflow without mutating prior artifacts."""
        workflow = self.get(workflow_run_id)
        if workflow.status == WorkflowStatus.WAITING_FOR_CLARIFICATION:
            if not clarified_question or not clarified_question.strip():
                raise ValueError("A clarified question is required to resume this workflow.")
            workflow = workflow.model_copy(deep=True)
            workflow.clarified_question = clarified_question.strip()
            workflow.clarification_questions = []
            workflow.status = self.machine.status_transition(
                workflow.status, WorkflowStatus.RUNNING
            )
            workflow.failure = None
            workflow.current_stage = WorkflowStage.REQUIREMENT
            workflow = self._save(workflow, workflow.version, [
                self._event(workflow, WorkflowStage.REQUIREMENT,
                            WorkflowEventType.WORKFLOW_RESUMED,
                            metadata={"reason": "clarification"})
            ])
        elif workflow.status == WorkflowStatus.FAILED:
            record = self._record(workflow, workflow.current_stage)
            if not workflow.failure or not workflow.failure.retryable or record.attempt >= self.retry_policy.max_attempts:
                raise ValueError("Workflow failure is not eligible for retry.")
            workflow = workflow.model_copy(deep=True)
            workflow.status = self.machine.status_transition(
                workflow.status, WorkflowStatus.RUNNING
            )
            workflow.failure = None
            workflow = self._save(workflow, workflow.version, [
                self._event(workflow, workflow.current_stage,
                            WorkflowEventType.WORKFLOW_RESUMED,
                            metadata={"reason": "retryable_failure"})
            ])
        elif workflow.status in {WorkflowStatus.BLOCKED, WorkflowStatus.COMPLETED,
                                WorkflowStatus.COMPLETED_WITH_WARNINGS}:
            raise ValueError(f"Workflow in {workflow.status} cannot be resumed.")
        return self.run(workflow.workflow_run_id)

    def advance(self, workflow_run_id: str) -> WorkflowRun:
        """Claim and execute exactly one current stage."""
        workflow = self.get(workflow_run_id)
        if workflow.status in _TERMINAL or workflow.current_stage == WorkflowStage.COMPLETE:
            return workflow
        stage = workflow.current_stage
        started = perf_counter()
        working = workflow.model_copy(deep=True)
        record = self._record(working, stage)
        record.status = StageStatus.RUNNING
        record.started_at = datetime.now(timezone.utc)
        record.completed_at = None
        record.attempt += 1
        record.error_code = record.error_message = None
        working.status = self.machine.status_transition(
            working.status, WorkflowStatus.RUNNING
        )
        working = self._save(working, workflow.version, [
            self._event(working, stage, WorkflowEventType.STAGE_STARTED,
                        metadata={"attempt": record.attempt})
        ])
        try:
            completed = self._execute_stage(working, stage)
            logger.info(
                "Workflow %s stage=%s transition=%s replay=%s attempt=%d warnings=%d elapsed=%.3fs",
                completed.workflow_run_id, stage, completed.current_stage,
                self._record(completed, stage).replayed, record.attempt,
                len(completed.warnings), perf_counter() - started,
            )
            return completed
        except Exception as error:
            return self._fail(working.workflow_run_id, stage, error, record.attempt)

    def _execute_stage(self, workflow: WorkflowRun, stage: WorkflowStage) -> WorkflowRun:
        if stage == WorkflowStage.REQUIREMENT:
            question = workflow.clarified_question or workflow.original_question
            request, replayed = requirement_stage(question, workflow.profile_id, self.database_url)
            workflow.artifact_refs.analysis_request_id = request.request_id
            if request.status == "NEEDS_CLARIFICATION":
                questions = [item.question for item in request.clarification_questions]
                return self._wait(workflow, stage, [request.request_id], replayed, questions)
            if request.status != "READY":
                raise ValueError(f"Requirement interpretation did not produce READY: {request.status}.")
            return self._finish(workflow, stage, WorkflowStage.ANALYSIS_PLAN,
                                [request.request_id], replayed)
        if stage == WorkflowStage.ANALYSIS_PLAN:
            plan, replayed = analysis_plan_stage(
                self._required(workflow.artifact_refs.analysis_request_id, "AnalysisRequest"),
                self.database_url,
            )
            workflow.artifact_refs.analysis_plan_id = plan.plan_id
            return self._finish(workflow, stage, WorkflowStage.ANALYSIS_EXECUTION,
                                [plan.plan_id], replayed)
        if stage == WorkflowStage.ANALYSIS_EXECUTION:
            execution = analysis_execution_stage(
                self._required(workflow.artifact_refs.analysis_request_id, "AnalysisRequest"),
                self.database_url,
            )
            workflow.artifact_refs.computed_analysis_id = execution.result.analysis_id
            workflow.artifact_refs.analysis_plan_id = execution.result.plan_id
            self._warnings(workflow, execution.result.warnings)
            return self._finish(workflow, stage, WorkflowStage.INVESTIGATION_DECISION,
                                [execution.result.analysis_id], execution.analysis_replayed,
                                execution.result.warnings)
        if stage == WorkflowStage.INVESTIGATION_DECISION:
            analysis, profile = self._routing_inputs(workflow)
            decision = self.router.decide(
                analysis, profile, workflow.investigation_policy, workflow.investigation_config
            )
            if decision.run:
                return self._finish(workflow, stage, WorkflowStage.INVESTIGATION, [], False,
                                    [decision.reason])
            return self._skip_investigation(workflow, decision.reason)
        if stage == WorkflowStage.INVESTIGATION:
            analysis, profile = self._routing_inputs(workflow)
            decision = self.router.decide(
                analysis, profile, workflow.investigation_policy, workflow.investigation_config
            )
            execution = investigation_stage(analysis.analysis_id, self.database_url, decision.dimensions)
            workflow.artifact_refs.investigation_plan_id = execution.result.investigation_plan_id
            workflow.artifact_refs.investigation_result_id = execution.result.investigation_id
            self._warnings(workflow, execution.result.warnings)
            return self._finish(
                workflow, stage, WorkflowStage.CRITIC,
                [execution.result.investigation_plan_id, execution.result.investigation_id],
                execution.plan_replayed and execution.investigation_replayed,
                execution.result.warnings,
            )
        if stage == WorkflowStage.CRITIC:
            execution = critic_stage(
                self._required(workflow.artifact_refs.computed_analysis_id, "ComputedAnalysis"),
                self.database_url, workflow.artifact_refs.investigation_result_id,
            )
            workflow.artifact_refs.critic_review_id = execution.review.review_id
            self._warnings(workflow, execution.review.warnings)
            if execution.review.overall_status == ReviewStatus.FAIL:
                return self._block(workflow, stage, execution.review.review_id,
                                   "CRITIC_REJECTED", "CriticReview failed the hard gate.")
            return self._finish(workflow, stage, WorkflowStage.BUSINESS_INTERPRETATION,
                                [execution.review.review_id], execution.replayed,
                                execution.review.warnings)
        if stage == WorkflowStage.BUSINESS_INTERPRETATION:
            execution = business_stage(
                self._required(workflow.artifact_refs.critic_review_id, "CriticReview"),
                self.database_url,
            )
            workflow.artifact_refs.business_insight_id = execution.insight.insight_id
            self._warnings(workflow, execution.insight.caveats)
            return self._finish(workflow, stage, WorkflowStage.REPORT,
                                [execution.insight.insight_id], execution.replayed,
                                execution.insight.caveats)
        if stage == WorkflowStage.REPORT:
            execution = report_stage(
                self._required(workflow.artifact_refs.business_insight_id, "BusinessInsight"),
                self.database_url, workflow.audience,
            )
            workflow.artifact_refs.report_spec_id = execution.spec.report_spec_id
            workflow.artifact_refs.report_artifact_id = execution.report.report_id
            self._warnings(workflow, execution.report.caveats)
            return self._complete(
                workflow, stage, [execution.spec.report_spec_id, execution.report.report_id],
                execution.spec_replayed and execution.report_replayed,
            )
        raise ValueError(f"No executor exists for stage {stage}.")

    def _finish(self, workflow: WorkflowRun, stage: WorkflowStage, target: WorkflowStage,
                artifacts: list[str], replayed: bool,
                warnings: list[str] | None = None) -> WorkflowRun:
        updated = workflow.model_copy(deep=True)
        record = self._record(updated, stage)
        record.status = StageStatus.REPLAYED if replayed else StageStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        record.artifact_ids = artifacts
        record.replayed = replayed
        record.warnings = list(warnings or [])
        updated.current_stage = self.machine.transition(stage, target)
        event_type = WorkflowEventType.STAGE_REPLAYED if replayed else WorkflowEventType.STAGE_COMPLETED
        return self._save(updated, workflow.version, [self._event(updated, stage, event_type, artifacts)])

    def _wait(self, workflow: WorkflowRun, stage: WorkflowStage, artifacts: list[str],
              replayed: bool, questions: list[str]) -> WorkflowRun:
        updated = workflow.model_copy(deep=True)
        record = self._record(updated, stage)
        record.status = StageStatus.WAITING
        record.completed_at = datetime.now(timezone.utc)
        record.artifact_ids = artifacts
        record.replayed = replayed
        updated.status = self.machine.status_transition(
            updated.status, WorkflowStatus.WAITING_FOR_CLARIFICATION
        )
        updated.clarification_questions = questions
        return self._save(updated, workflow.version, [
            self._event(updated, stage, WorkflowEventType.CLARIFICATION_REQUIRED,
                        artifacts, {"question_count": len(questions)})
        ])

    def _skip_investigation(self, workflow: WorkflowRun, reason: str) -> WorkflowRun:
        updated = workflow.model_copy(deep=True)
        decision = self._record(updated, WorkflowStage.INVESTIGATION_DECISION)
        decision.status = StageStatus.COMPLETED
        decision.completed_at = datetime.now(timezone.utc)
        decision.warnings = [reason]
        skipped = self._record(updated, WorkflowStage.INVESTIGATION)
        skipped.status = StageStatus.SKIPPED
        skipped.completed_at = datetime.now(timezone.utc)
        skipped.warnings = [reason]
        updated.current_stage = self.machine.transition(WorkflowStage.INVESTIGATION_DECISION,
                                                        WorkflowStage.CRITIC)
        return self._save(updated, workflow.version, [
            self._event(updated, WorkflowStage.INVESTIGATION_DECISION,
                        WorkflowEventType.STAGE_COMPLETED, metadata={"decision": "skip"}),
            self._event(updated, WorkflowStage.INVESTIGATION,
                        WorkflowEventType.STAGE_SKIPPED, metadata={"reason": reason}),
        ])

    def _block(self, workflow: WorkflowRun, stage: WorkflowStage, artifact_id: str,
               code: str, message: str) -> WorkflowRun:
        updated = workflow.model_copy(deep=True)
        record = self._record(updated, stage)
        record.status = StageStatus.BLOCKED
        record.completed_at = datetime.now(timezone.utc)
        record.artifact_ids = [artifact_id]
        record.error_code, record.error_message = code, message
        updated.status = self.machine.status_transition(
            updated.status, WorkflowStatus.BLOCKED
        )
        updated.failure = WorkflowFailure(stage=stage, error_code=code, message=message,
                                          retryable=False, artifact_id=artifact_id)
        return self._save(updated, workflow.version, [
            self._event(updated, stage, WorkflowEventType.WORKFLOW_BLOCKED, [artifact_id])
        ])

    def _complete(self, workflow: WorkflowRun, stage: WorkflowStage,
                  artifacts: list[str], replayed: bool) -> WorkflowRun:
        updated = workflow.model_copy(deep=True)
        record = self._record(updated, stage)
        record.status = StageStatus.REPLAYED if replayed else StageStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        record.artifact_ids = artifacts
        record.replayed = replayed
        updated.current_stage = self.machine.transition(stage, WorkflowStage.COMPLETE)
        final_status = (WorkflowStatus.COMPLETED_WITH_WARNINGS
                        if updated.warnings else WorkflowStatus.COMPLETED)
        updated.status = self.machine.status_transition(updated.status, final_status)
        updated.completed_at = datetime.now(timezone.utc)
        return self._save(updated, workflow.version, [
            self._event(updated, stage, WorkflowEventType.STAGE_REPLAYED if replayed else WorkflowEventType.STAGE_COMPLETED,
                        artifacts),
            self._event(updated, WorkflowStage.COMPLETE, WorkflowEventType.WORKFLOW_COMPLETED,
                        artifacts, {"status": updated.status}),
        ])

    def _fail(self, workflow_run_id: str, stage: WorkflowStage,
              error: Exception, attempt: int) -> WorkflowRun:
        workflow = self.get(workflow_run_id).model_copy(deep=True)
        retryable = self.retry_policy.is_retryable(error)
        message = str(error) if isinstance(error, (ValueError, LookupError)) else f"Stage failed with {type(error).__name__}."
        code = type(error).__name__.upper()
        record = self._record(workflow, stage)
        record.status = StageStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error_code, record.error_message = code, message
        workflow.failure = WorkflowFailure(stage=stage, error_code=code, message=message,
                                           retryable=retryable)
        if self.retry_policy.should_retry(error, attempt):
            workflow.status = self.machine.status_transition(
                workflow.status, WorkflowStatus.RUNNING
            )
            self._warnings(workflow, [f"Retrying {stage} after retryable {code}."])
            return self._save(workflow, workflow.version, [])
        workflow.status = self.machine.status_transition(
            workflow.status, WorkflowStatus.FAILED
        )
        return self._save(workflow, workflow.version, [
            self._event(workflow, stage, WorkflowEventType.WORKFLOW_FAILED,
                        metadata={"error_code": code, "retryable": retryable})
        ])

    def _routing_inputs(self, workflow: WorkflowRun) -> tuple[ComputedAnalysis, DatasetProfile]:
        with Session(create_database_engine(self.database_url)) as session:
            analysis_record = session.scalar(select(ComputedAnalysisRecord).where(
                ComputedAnalysisRecord.analysis_id == workflow.artifact_refs.computed_analysis_id
            ))
            profile_record = session.scalar(select(DatasetProfileRecord).where(
                DatasetProfileRecord.profile_id == workflow.profile_id
            ))
            if analysis_record is None or profile_record is None:
                raise LookupError("Investigation routing inputs are missing.")
            return (ComputedAnalysis.model_validate(analysis_record.analysis_content),
                    DatasetProfile.model_validate(profile_record.profile_content))

    def _save(self, workflow: WorkflowRun, expected_version: int,
              events: list[WorkflowEvent]) -> WorkflowRun:
        with Session(create_database_engine(self.database_url)) as session:
            return WorkflowStore(session).save(workflow, expected_version, events)

    @staticmethod
    def _record(workflow: WorkflowRun, stage: WorkflowStage) -> StageRecord:
        return next(item for item in workflow.stage_records if item.stage == stage)

    @staticmethod
    def _event(workflow: WorkflowRun, stage: WorkflowStage,
               event_type: WorkflowEventType, artifacts: list[str] | None = None,
               metadata: dict | None = None) -> WorkflowEvent:
        return WorkflowEvent(workflow_run_id=workflow.workflow_run_id, stage=stage,
                             event_type=event_type, artifact_refs=artifacts or [],
                             metadata=metadata or {})

    @staticmethod
    def _warnings(workflow: WorkflowRun, warnings: list[str]) -> None:
        workflow.warnings = list(dict.fromkeys([*workflow.warnings, *warnings]))

    @staticmethod
    def _required(value: str | None, name: str) -> str:
        if value is None: raise LookupError(f"Workflow {name} reference is missing.")
        return value
