"""Idempotent persistence for versioned CriticReview artifacts."""
from __future__ import annotations

import hashlib
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.critic import CriticReview
from database.models import ComputedAnalysisRecord, CriticReviewRecord, DatasetVersion, InvestigationResultRecord


class CriticReviewStore:
    """Create, retrieve, and replay reviews by stable review identity."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def identity(analysis_id: str, investigation_id: str | None, critic_version: str,
                 qa_rules_version: str, prompt_version: str,
                 provider_name: str | None = None, model_name: str | None = None) -> str:
        value = "|".join((analysis_id, investigation_id or "none", critic_version,
                          qa_rules_version, prompt_version, provider_name or "deterministic",
                          model_name or "none"))
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, review_id: str) -> CriticReview | None:
        record = self.session.scalar(select(CriticReviewRecord).where(CriticReviewRecord.review_id == review_id))
        return CriticReview.model_validate(record.review_content) if record else None

    def get_by_identity(self, identity_hash: str) -> CriticReview | None:
        record = self.session.scalar(select(CriticReviewRecord).where(CriticReviewRecord.identity_hash == identity_hash))
        return CriticReview.model_validate(record.review_content) if record else None

    def get_or_create(self, review: CriticReview, identity_hash: str) -> tuple[CriticReview, bool]:
        existing = self.get_by_identity(identity_hash)
        if existing: return existing, True
        analysis = self.session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == review.analysis_id))
        investigation = None if review.investigation_id is None else self.session.scalar(select(InvestigationResultRecord).where(InvestigationResultRecord.investigation_id == review.investigation_id))
        version = self.session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == review.dataset_version_id))
        if analysis is None or version is None or (review.investigation_id and investigation is None):
            raise LookupError("Critic review provenance is incomplete.")
        self.session.add(CriticReviewRecord(
            review_id=review.review_id, identity_hash=identity_hash, analysis_id=analysis.id,
            investigation_id=investigation.id if investigation else None, dataset_version_id=version.id,
            status=review.overall_status, severity=review.severity,
            critic_version=review.critic_version, qa_rules_version=review.qa_rules_version,
            prompt_version=review.prompt_version, provider_name=review.provider_name,
            model_name=review.model_name, review_content=review.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_identity(identity_hash)
            if existing is None: raise
            return existing, True
        return review, False
