"""Optimistic workflow persistence and append-only event history."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from contracts.orchestration import WorkflowEvent, WorkflowRun
from database.models import (
    DatasetProfileRecord, DatasetVersion, WorkflowRunRecord, WorkflowStageEventRecord,
)


class StaleWorkflowVersionError(RuntimeError):
    """Raised when another process advanced the workflow first."""


class WorkflowStore:
    """Persist workflow snapshots with compare-and-swap version protection."""

    def __init__(self, session: Session) -> None: self.session = session

    @staticmethod
    def identity(dataset_version_id: str, question: str, audience: str,
                 investigation_policy: str, orchestrator_version: str,
                 workflow_rules_version: str, minimum_absolute_change: float = 0,
                 minimum_percentage_change: float = 0,
                 routing_rules_version: str = "1.0") -> str:
        normalized = " ".join(question.lower().split())
        value = "|".join((dataset_version_id, normalized, audience, investigation_policy,
                          orchestrator_version, workflow_rules_version,
                          str(minimum_absolute_change), str(minimum_percentage_change),
                          routing_rules_version))
        return hashlib.sha256(value.encode()).hexdigest()

    def create(self, workflow: WorkflowRun, event: WorkflowEvent) -> WorkflowRun:
        """Create a distinct auditable invocation, even for an identical identity."""
        version = self.session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == workflow.dataset_version_id))
        profile = self.session.scalar(select(DatasetProfileRecord).where(DatasetProfileRecord.profile_id == workflow.profile_id))
        if version is None or profile is None or profile.dataset_version_id != version.id:
            raise LookupError("Workflow dataset version or profile is missing or mismatched.")
        record = WorkflowRunRecord(
            workflow_run_id=workflow.workflow_run_id,
            workflow_identity_hash=workflow.workflow_identity_hash,
            dataset_version_id=version.id, profile_id=profile.id,
            status=workflow.status, current_stage=workflow.current_stage,
            version=workflow.version, run_content=workflow.model_dump(mode="json"),
            updated_at=workflow.updated_at, completed_at=workflow.completed_at,
        )
        self.session.add(record)
        self.session.flush()
        self._add_event(record.id, event)
        self.session.commit()
        return workflow

    def get(self, workflow_run_id: str) -> WorkflowRun | None:
        record = self.session.scalar(select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_run_id == workflow_run_id))
        return WorkflowRun.model_validate(record.run_content) if record else None

    def save(self, workflow: WorkflowRun, expected_version: int,
             events: list[WorkflowEvent] | None = None) -> WorkflowRun:
        """Atomically replace a snapshot only when its version is current."""
        now = datetime.now(timezone.utc)
        updated = workflow.model_copy(update={"version": expected_version + 1, "updated_at": now})
        result = self.session.execute(
            update(WorkflowRunRecord)
            .where(WorkflowRunRecord.workflow_run_id == workflow.workflow_run_id,
                   WorkflowRunRecord.version == expected_version)
            .values(status=updated.status, current_stage=updated.current_stage,
                    version=updated.version, run_content=updated.model_dump(mode="json"),
                    updated_at=now, completed_at=updated.completed_at)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise StaleWorkflowVersionError(
                f"Workflow {workflow.workflow_run_id} was advanced by another process."
            )
        record_id = self.session.scalar(select(WorkflowRunRecord.id).where(
            WorkflowRunRecord.workflow_run_id == workflow.workflow_run_id
        ))
        for event in events or []: self._add_event(record_id, event)
        self.session.commit()
        return updated

    def events(self, workflow_run_id: str) -> list[WorkflowEvent]:
        records = self.session.scalars(
            select(WorkflowStageEventRecord)
            .join(WorkflowRunRecord, WorkflowStageEventRecord.workflow_run_id == WorkflowRunRecord.id)
            .where(WorkflowRunRecord.workflow_run_id == workflow_run_id)
            .order_by(WorkflowStageEventRecord.id)
        ).all()
        return [WorkflowEvent(
            event_id=item.event_id, workflow_run_id=workflow_run_id, stage=item.stage,
            event_type=item.event_type, artifact_refs=item.artifact_refs,
            occurred_at=item.occurred_at, metadata=item.event_metadata,
        ) for item in records]

    def _add_event(self, record_id: int, event: WorkflowEvent) -> None:
        self.session.add(WorkflowStageEventRecord(
            event_id=event.event_id, workflow_run_id=record_id, stage=event.stage,
            event_type=event.event_type, artifact_refs=event.artifact_refs,
            event_metadata=event.metadata, occurred_at=event.occurred_at,
        ))
