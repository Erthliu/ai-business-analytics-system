"""Persistence and idempotent replay for validated analysis plans."""
from __future__ import annotations

import hashlib
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from database.models import AnalysisPlanRecord, AnalysisRequestRecord


class AnalysisPlanStore:
    """Store plans by a stable request-and-version identity."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def identity(request: AnalysisRequest, planner_version: str, prompt_version: str, registry_version: str,
                 provider_name: str | None = None, model_name: str | None = None) -> str:
        value = "|".join((request.request_id, request.dataset_version_id, request.profile_id,
                          planner_version, prompt_version, registry_version,
                          provider_name or "deterministic", model_name or "none"))
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, plan_id: str) -> AnalysisPlan | None:
        record = self.session.scalar(select(AnalysisPlanRecord).where(AnalysisPlanRecord.plan_id == plan_id))
        return AnalysisPlan.model_validate(record.plan_content) if record else None

    def get_by_identity(self, identity_hash: str) -> AnalysisPlan | None:
        record = self.session.scalar(select(AnalysisPlanRecord).where(AnalysisPlanRecord.identity_hash == identity_hash))
        return AnalysisPlan.model_validate(record.plan_content) if record else None

    def get_or_create(self, plan: AnalysisPlan, identity_hash: str) -> tuple[AnalysisPlan, bool]:
        """Return a persisted plan and whether an existing plan was replayed."""
        existing = self.get_by_identity(identity_hash)
        if existing:
            return existing, True
        request = self.session.scalar(select(AnalysisRequestRecord).where(AnalysisRequestRecord.request_id == plan.request_id))
        if request is None:
            raise LookupError(f"Analysis request not found: {plan.request_id}")
        self.session.add(AnalysisPlanRecord(
            plan_id=plan.plan_id, identity_hash=identity_hash,
            request_id=request.id, plan_content=plan.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_identity(identity_hash)
            if existing is None:
                raise
            return existing, True
        return plan, False
