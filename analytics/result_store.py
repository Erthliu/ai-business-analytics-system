"""Persistence and idempotent replay for computed analyses."""
from __future__ import annotations

import hashlib
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.computed_analysis import ComputedAnalysis
from database.models import AnalysisPlanRecord, ComputedAnalysisRecord


class ComputedAnalysisStore:
    """Store deterministic results by plan, source, and engine provenance."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def identity(plan_id: str, dataset_version_id: str, source_hash: str,
                 engine_version: str, registry_version: str) -> str:
        value = "|".join((plan_id, dataset_version_id, source_hash, engine_version, registry_version))
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, analysis_id: str) -> ComputedAnalysis | None:
        record = self.session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == analysis_id))
        return ComputedAnalysis.model_validate(record.analysis_content) if record else None

    def get_by_identity(self, identity_hash: str) -> ComputedAnalysis | None:
        record = self.session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.identity_hash == identity_hash))
        return ComputedAnalysis.model_validate(record.analysis_content) if record else None

    def get_or_create(self, result: ComputedAnalysis, identity_hash: str) -> tuple[ComputedAnalysis, bool]:
        """Return a persisted result and whether an existing result was replayed."""
        existing = self.get_by_identity(identity_hash)
        if existing:
            return existing, True
        plan = self.session.scalar(select(AnalysisPlanRecord).where(AnalysisPlanRecord.plan_id == result.plan_id))
        if plan is None:
            raise LookupError(f"Analysis plan not found: {result.plan_id}")
        self.session.add(ComputedAnalysisRecord(
            analysis_id=result.analysis_id, identity_hash=identity_hash,
            plan_id=plan.id, analysis_content=result.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_identity(identity_hash)
            if existing is None:
                raise
            return existing, True
        return result, False
