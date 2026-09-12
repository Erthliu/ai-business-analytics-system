"""Idempotent persistence for V5 investigation artifacts."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.investigation import InvestigationPlan, InvestigationResult
from database.models import (
    AnalysisPlanRecord, AnalysisRequestRecord, ComputedAnalysisRecord,
    DatasetProfileRecord, DatasetVersion, InvestigationPlanRecord, InvestigationResultRecord,
)


class InvestigationPlanStore:
    """Persist and replay investigation plans by stable configuration identity."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def identity(plan: InvestigationPlan) -> str:
        payload = plan.model_dump(mode="json", exclude={"investigation_plan_id", "created_at"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def get(self, plan_id: str) -> InvestigationPlan | None:
        record = self.session.scalar(select(InvestigationPlanRecord).where(InvestigationPlanRecord.investigation_plan_id == plan_id))
        return InvestigationPlan.model_validate(record.plan_content) if record else None

    def get_by_identity(self, identity_hash: str) -> InvestigationPlan | None:
        record = self.session.scalar(select(InvestigationPlanRecord).where(InvestigationPlanRecord.identity_hash == identity_hash))
        return InvestigationPlan.model_validate(record.plan_content) if record else None

    def get_or_create(self, plan: InvestigationPlan) -> tuple[InvestigationPlan, bool]:
        identity_hash = self.identity(plan)
        existing = self.get_by_identity(identity_hash)
        if existing:
            return existing, True
        request = self.session.scalar(select(AnalysisRequestRecord).where(AnalysisRequestRecord.request_id == plan.request_id))
        analysis_plan = self.session.scalar(select(AnalysisPlanRecord).where(AnalysisPlanRecord.plan_id == plan.analysis_plan_id))
        analysis = self.session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == plan.analysis_id))
        version = self.session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == plan.dataset_version_id))
        profile = self.session.scalar(select(DatasetProfileRecord).where(DatasetProfileRecord.profile_id == plan.profile_id))
        if None in (request, analysis_plan, analysis, version, profile):
            raise LookupError("Investigation plan provenance is incomplete.")
        self.session.add(InvestigationPlanRecord(
            investigation_plan_id=plan.investigation_plan_id, identity_hash=identity_hash,
            request_id=request.id, analysis_plan_id=analysis_plan.id, analysis_id=analysis.id,
            dataset_version_id=version.id, profile_id=profile.id, plan_content=plan.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_identity(identity_hash)
            if existing is None: raise
            return existing, True
        return plan, False


class InvestigationResultStore:
    """Persist and replay deterministic investigation results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def identity(plan_id: str, source_hash: str, engine_version: str) -> str:
        return hashlib.sha256("|".join((plan_id, source_hash, engine_version)).encode()).hexdigest()

    def get(self, result_id: str) -> InvestigationResult | None:
        record = self.session.scalar(select(InvestigationResultRecord).where(InvestigationResultRecord.investigation_id == result_id))
        return InvestigationResult.model_validate(record.result_content) if record else None

    def get_by_identity(self, identity_hash: str) -> InvestigationResult | None:
        record = self.session.scalar(select(InvestigationResultRecord).where(InvestigationResultRecord.identity_hash == identity_hash))
        return InvestigationResult.model_validate(record.result_content) if record else None

    def get_or_create(self, result: InvestigationResult, identity_hash: str) -> tuple[InvestigationResult, bool]:
        existing = self.get_by_identity(identity_hash)
        if existing:
            return existing, True
        plan = self.session.scalar(select(InvestigationPlanRecord).where(InvestigationPlanRecord.investigation_plan_id == result.investigation_plan_id))
        analysis = self.session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == result.analysis_id))
        version = self.session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == result.dataset_version_id))
        if None in (plan, analysis, version):
            raise LookupError("Investigation result provenance is incomplete.")
        self.session.add(InvestigationResultRecord(
            investigation_id=result.investigation_id, identity_hash=identity_hash,
            investigation_plan_id=plan.id, analysis_id=analysis.id, dataset_version_id=version.id,
            result_content=result.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_identity(identity_hash)
            if existing is None: raise
            return existing, True
        return result, False
