"""Dedicated persistence service for grounded requirement requests."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from contracts.analysis_request import AnalysisRequest
from database.models import AnalysisRequestRecord, DatasetProfileRecord, DatasetVersion


class AnalysisRequestStore:
    """Persist/reuse request payloads by stable interpretation identity."""
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, request: AnalysisRequest, identity_hash: str) -> AnalysisRequest:
        existing = self.session.scalar(select(AnalysisRequestRecord).where(AnalysisRequestRecord.identity_hash == identity_hash))
        if existing:
            return AnalysisRequest.model_validate(existing.request_content)
        version = self.session.scalar(select(DatasetVersion).where(DatasetVersion.version_id == request.dataset_version_id))
        profile = self.session.scalar(select(DatasetProfileRecord).where(DatasetProfileRecord.profile_id == request.profile_id))
        if version is None or profile is None:
            raise LookupError("Dataset version or profile not found.")
        self.session.add(AnalysisRequestRecord(
            request_id=request.request_id, identity_hash=identity_hash, dataset_version_id=version.id,
            profile_id=profile.id, pipeline_run_id=version.pipeline_run_id, status=request.status,
            agent_version=request.agent_version, prompt_version=request.prompt_version,
            provider_name=request.provider_name, model_name=request.model_name,
            request_content=request.model_dump(mode="json"),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(AnalysisRequestRecord).where(
                    AnalysisRequestRecord.identity_hash == identity_hash
                )
            )
            if existing is None:
                raise
            return AnalysisRequest.model_validate(existing.request_content)
        return request
