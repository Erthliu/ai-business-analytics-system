"""Explicit V9 workflow stage transitions."""
from contracts.orchestration import WorkflowStage, WorkflowStatus


class InvalidTransitionError(ValueError):
    """Raised when a caller attempts a transition outside the workflow graph."""


class WorkflowStateMachine:
    """Validate every normal workflow stage transition."""

    _allowed = {
        WorkflowStage.REQUIREMENT: {WorkflowStage.ANALYSIS_PLAN},
        WorkflowStage.ANALYSIS_PLAN: {WorkflowStage.ANALYSIS_EXECUTION},
        WorkflowStage.ANALYSIS_EXECUTION: {WorkflowStage.INVESTIGATION_DECISION},
        WorkflowStage.INVESTIGATION_DECISION: {WorkflowStage.INVESTIGATION, WorkflowStage.CRITIC},
        WorkflowStage.INVESTIGATION: {WorkflowStage.CRITIC},
        WorkflowStage.CRITIC: {WorkflowStage.BUSINESS_INTERPRETATION},
        WorkflowStage.BUSINESS_INTERPRETATION: {WorkflowStage.REPORT},
        WorkflowStage.REPORT: {WorkflowStage.COMPLETE},
        WorkflowStage.COMPLETE: set(),
    }
    _status_allowed = {
        WorkflowStatus.PENDING: {WorkflowStatus.RUNNING},
        WorkflowStatus.RUNNING: {
            WorkflowStatus.RUNNING, WorkflowStatus.WAITING_FOR_CLARIFICATION,
            WorkflowStatus.BLOCKED, WorkflowStatus.FAILED,
            WorkflowStatus.COMPLETED, WorkflowStatus.COMPLETED_WITH_WARNINGS,
        },
        WorkflowStatus.WAITING_FOR_CLARIFICATION: {WorkflowStatus.RUNNING},
        WorkflowStatus.FAILED: {WorkflowStatus.RUNNING},
        WorkflowStatus.BLOCKED: set(),
        WorkflowStatus.COMPLETED: set(),
        WorkflowStatus.COMPLETED_WITH_WARNINGS: set(),
    }

    def transition(self, current: WorkflowStage, target: WorkflowStage) -> WorkflowStage:
        """Return the validated target or reject the invalid edge."""
        if target not in self._allowed[current]:
            raise InvalidTransitionError(f"Invalid workflow transition: {current} -> {target}.")
        return target

    def status_transition(self, current: WorkflowStatus,
                          target: WorkflowStatus) -> WorkflowStatus:
        """Return a validated lifecycle status target."""
        if target not in self._status_allowed[current]:
            raise InvalidTransitionError(f"Invalid workflow status transition: {current} -> {target}.")
        return target
