"""Idempotent persistence for V8 report specifications and artifacts."""
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.reporting import ReportArtifact, ReportSpec
from database.models import (
    BusinessInsightRecord, ComputedAnalysisRecord, CriticReviewRecord, DatasetVersion,
    InvestigationResultRecord, ReportArtifactRecord, ReportSpecRecord,
)


class ReportSpecStore:
    """Create, retrieve, and replay ReportSpecs by versioned identity."""

    def __init__(self, session: Session) -> None: self.session = session

    @staticmethod
    def identity(insight_id: str, audience: str, agent_version: str, prompt_version: str,
                 rules_version: str, provider_name: str | None = None,
                 model_name: str | None = None) -> str:
        value = "|".join((insight_id, audience, agent_version, prompt_version, rules_version,
                          provider_name or "deterministic", model_name or "none"))
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, spec_id: str) -> ReportSpec | None:
        record = self.session.scalar(select(ReportSpecRecord).where(ReportSpecRecord.report_spec_id == spec_id))
        return ReportSpec.model_validate(record.spec_content) if record else None

    def get_by_identity(self, identity_hash: str) -> ReportSpec | None:
        record = self.session.scalar(select(ReportSpecRecord).where(ReportSpecRecord.identity_hash == identity_hash))
        return ReportSpec.model_validate(record.spec_content) if record else None

    def get_or_create(self, spec: ReportSpec, identity_hash: str) -> tuple[ReportSpec, bool]:
        existing = self.get_by_identity(identity_hash)
        if existing: return existing, True
        insight, review, analysis, investigation, version = _provenance(self.session, spec)
        self.session.add(ReportSpecRecord(
            report_spec_id=spec.report_spec_id, identity_hash=identity_hash,
            business_insight_id=insight.id, critic_review_id=review.id, analysis_id=analysis.id,
            investigation_id=investigation.id if investigation else None, dataset_version_id=version.id,
            audience=spec.audience, agent_version=spec.agent_version, prompt_version=spec.prompt_version,
            report_rules_version=spec.report_rules_version, provider_name=spec.provider_name,
            model_name=spec.model_name, spec_content=spec.model_dump(mode="json"),
        ))
        return _commit_or_replay(self.session, self.get_by_identity, identity_hash, spec)


class ReportArtifactStore:
    """Create, retrieve, and replay ReportArtifacts by renderer identity."""

    def __init__(self, session: Session) -> None: self.session = session

    @staticmethod
    def identity(spec_id: str, insight_id: str, renderer_version: str,
                 rules_version: str) -> str:
        return hashlib.sha256("|".join((spec_id, insight_id, renderer_version, rules_version)).encode()).hexdigest()

    def get(self, report_id: str) -> ReportArtifact | None:
        record = self.session.scalar(select(ReportArtifactRecord).where(ReportArtifactRecord.report_id == report_id))
        return ReportArtifact.model_validate(record.artifact_content) if record else None

    def get_by_identity(self, identity_hash: str) -> ReportArtifact | None:
        record = self.session.scalar(select(ReportArtifactRecord).where(ReportArtifactRecord.identity_hash == identity_hash))
        return ReportArtifact.model_validate(record.artifact_content) if record else None

    def get_or_create(self, artifact: ReportArtifact, identity_hash: str) -> tuple[ReportArtifact, bool]:
        existing = self.get_by_identity(identity_hash)
        if existing: return existing, True
        spec = self.session.scalar(select(ReportSpecRecord).where(ReportSpecRecord.report_spec_id == artifact.report_spec_id))
        insight, review, analysis, investigation, version = _provenance(self.session, artifact)
        if spec is None: raise LookupError("ReportArtifact ReportSpec is missing.")
        self.session.add(ReportArtifactRecord(
            report_id=artifact.report_id, identity_hash=identity_hash, report_spec_id=spec.id,
            business_insight_id=insight.id, critic_review_id=review.id, analysis_id=analysis.id,
            investigation_id=investigation.id if investigation else None, dataset_version_id=version.id,
            status=artifact.status, renderer_version=artifact.renderer_version,
            report_rules_version=artifact.report_rules_version,
            artifact_content=artifact.model_dump(mode="json"),
        ))
        return _commit_or_replay(self.session, self.get_by_identity, identity_hash, artifact)


def _provenance(session, artifact):
    insight = session.scalar(select(BusinessInsightRecord).where(BusinessInsightRecord.insight_id == artifact.business_insight_id))
    review = session.scalar(select(CriticReviewRecord).where(CriticReviewRecord.review_id == artifact.critic_review_id))
    analysis = session.scalar(select(ComputedAnalysisRecord).where(ComputedAnalysisRecord.analysis_id == artifact.analysis_id))
    investigation = None if artifact.investigation_id is None else session.scalar(select(InvestigationResultRecord).where(InvestigationResultRecord.investigation_id == artifact.investigation_id))
    version = session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == artifact.dataset_version_id))
    if any(item is None for item in (insight, review, analysis, version)) or (artifact.investigation_id and investigation is None):
        raise LookupError("Report provenance is incomplete.")
    return insight, review, analysis, investigation, version


def _commit_or_replay(session, lookup, identity_hash, artifact):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = lookup(identity_hash)
        if existing is None: raise
        return existing, True
    return artifact, False
