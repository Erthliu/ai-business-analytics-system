"""Idempotent persistence for evidence-bound BusinessInsight artifacts."""
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.business import BusinessInsight
from database.models import BusinessInsightRecord, ComputedAnalysisRecord, CriticReviewRecord, DatasetVersion, InvestigationResultRecord


class BusinessInsightStore:
    """Create, retrieve, and replay insights by versioned identity."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def identity(review_id: str, analysis_id: str, investigation_id: str | None,
                 agent_version: str, prompt_version: str, rules_version: str,
                 provider_name: str | None = None, model_name: str | None = None) -> str:
        value = "|".join((review_id, analysis_id, investigation_id or "none", agent_version,
                          prompt_version, rules_version, provider_name or "deterministic", model_name or "none"))
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, insight_id: str) -> BusinessInsight | None:
        record = self.session.scalar(select(BusinessInsightRecord).where(BusinessInsightRecord.insight_id == insight_id))
        return BusinessInsight.model_validate(record.insight_content) if record else None

    def get_by_identity(self, identity_hash: str) -> BusinessInsight | None:
        record = self.session.scalar(select(BusinessInsightRecord).where(BusinessInsightRecord.identity_hash == identity_hash))
        return BusinessInsight.model_validate(record.insight_content) if record else None

    def get_or_create(self, insight: BusinessInsight, identity_hash: str) -> tuple[BusinessInsight, bool]:
        existing = self.get_by_identity(identity_hash)
        if existing: return existing, True
        review = self.session.scalar(select(CriticReviewRecord).where(CriticReviewRecord.review_id == insight.critic_review_id))
        analysis = self.session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == insight.analysis_id))
        investigation = None if insight.investigation_id is None else self.session.scalar(select(InvestigationResultRecord).where(InvestigationResultRecord.investigation_id == insight.investigation_id))
        version = self.session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == insight.dataset_version_id))
        if review is None or analysis is None or version is None or (insight.investigation_id and investigation is None):
            raise LookupError("BusinessInsight provenance is incomplete.")
        self.session.add(BusinessInsightRecord(
            insight_id=insight.insight_id, identity_hash=identity_hash, critic_review_id=review.id,
            analysis_id=analysis.id, investigation_id=investigation.id if investigation else None,
            dataset_version_id=version.id, status=insight.status, agent_version=insight.agent_version,
            business_rules_version=insight.business_rules_version, prompt_version=insight.prompt_version,
            provider_name=insight.provider_name, model_name=insight.model_name,
            insight_content=insight.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_identity(identity_hash)
            if existing is None: raise
            return existing, True
        return insight, False
