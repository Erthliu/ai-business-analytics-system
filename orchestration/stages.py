"""Thin V9 adapters over existing V3–V8 services."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.analytics_planner import AnalyticsPlanner
from agents.request_store import AnalysisRequestStore
from agents.requirement_agent import RequirementAgent
from analytics.plan_store import AnalysisPlanStore
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest
from contracts.dataset_profile import DatasetProfile
from contracts.reporting import Audience
from database.models import AnalysisRequestRecord, DatasetProfileRecord
from database.persistence import create_database_engine
from scripts.generate_report import ReportExecution, execute_report
from scripts.interpret_business import BusinessExecution, execute_interpretation
from scripts.investigate_analysis import InvestigationExecution, execute_investigation
from scripts.review_analysis import ReviewExecution, execute_review
from scripts.run_analysis import AnalysisExecution, execute_request


def requirement_stage(question: str, profile_id: str, database_url: str) -> tuple[AnalysisRequest, bool]:
    """Interpret and persist one grounded V3 request."""
    with Session(create_database_engine(database_url)) as session:
        profile_record = session.scalar(select(DatasetProfileRecord).where(DatasetProfileRecord.profile_id == profile_id))
        if profile_record is None: raise LookupError(f"DatasetProfile not found: {profile_id}")
        profile = DatasetProfile.model_validate(profile_record.profile_content)
        agent = RequirementAgent()
        identity = agent.identity(question, profile, agent.agent_version, agent.prompt_version)
        existing = session.scalar(select(AnalysisRequestRecord).where(AnalysisRequestRecord.identity_hash == identity))
        if existing:
            return AnalysisRequest.model_validate(existing.request_content), True
        request = agent.interpret(question, profile)
        return AnalysisRequestStore(session).get_or_create(request, identity), False


def analysis_plan_stage(request_id: str, database_url: str) -> tuple[AnalysisPlan, bool]:
    """Build or replay only the V4 plan, without executing it."""
    with Session(create_database_engine(database_url)) as session:
        request_record = session.scalar(select(AnalysisRequestRecord).where(AnalysisRequestRecord.request_id == request_id))
        if request_record is None: raise LookupError(f"AnalysisRequest not found: {request_id}")
        request = AnalysisRequest.model_validate(request_record.request_content)
        profile_record = session.get(DatasetProfileRecord, request_record.profile_id)
        if profile_record is None: raise LookupError("DatasetProfile is missing.")
        profile = DatasetProfile.model_validate(profile_record.profile_content)
        planner = AnalyticsPlanner()
        identity = AnalysisPlanStore.identity(
            request, planner.planner_version, planner.prompt_version, planner.registry.version
        )
        store = AnalysisPlanStore(session)
        existing = store.get_by_identity(identity)
        if existing:
            planner.validator.validate(existing, profile=profile)
            return existing, True
        return store.get_or_create(planner.build(request, profile), identity)


def analysis_execution_stage(request_id: str, database_url: str) -> AnalysisExecution:
    return execute_request(request_id, database_url)


def investigation_stage(analysis_id: str, database_url: str,
                        dimensions: list[str]) -> InvestigationExecution:
    return execute_investigation(analysis_id, database_url, dimensions=dimensions)


def critic_stage(analysis_id: str, database_url: str,
                 investigation_id: str | None) -> ReviewExecution:
    return execute_review(analysis_id, database_url, investigation_id)


def business_stage(review_id: str, database_url: str) -> BusinessExecution:
    return execute_interpretation(review_id, database_url)


def report_stage(insight_id: str, database_url: str, audience: Audience) -> ReportExecution:
    return execute_report(insight_id, database_url, audience)
