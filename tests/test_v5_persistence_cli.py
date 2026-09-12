"""V5 persistence, replay, and CLI integration tests."""
from datetime import date
import json

import pytest
from sqlalchemy.orm import Session

from agents.investigation_agent import InvestigationAgent
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest, DateRange
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import DatasetProfile
from contracts.investigation import InvestigationPlan, InvestigationResult
from database.models import AnalysisPlanRecord, AnalysisRequestRecord, ComputedAnalysisRecord
from database.persistence import create_database_engine
from investigation.stores import InvestigationPlanStore, InvestigationResultStore
from scripts.investigate_analysis import _print_text, execute_investigation, main
from scripts.run_analysis import execute_request
from test_v4_persistence_cli import _database, _persist_request


def _period(start, end):
    return DateRange(start=date.fromisoformat(start), end=date.fromisoformat(end), source="test")


def _analysis(tmp_path, metric="revenue"):
    database_url, profile, source_hash = _database(tmp_path)
    request = _persist_request(database_url, profile, "August vs July", metric=metric,
                               analysis_period=_period("2024-08-01", "2024-08-31"),
                               comparison_period=_period("2024-07-01", "2024-07-31"))
    analysis = execute_request(request.request_id, database_url).result
    return database_url, profile, request, analysis, source_hash


def _sources(database_url, analysis_id):
    with Session(create_database_engine(database_url)) as session:
        analysis_record = session.query(ComputedAnalysisRecord).filter_by(analysis_id=analysis_id).one()
        plan_record = session.get(AnalysisPlanRecord, analysis_record.plan_id)
        request_record = session.get(AnalysisRequestRecord, plan_record.request_id)
        return (AnalysisRequest.model_validate(request_record.request_content),
                AnalysisPlan.model_validate(plan_record.plan_content),
                ComputedAnalysis.model_validate(analysis_record.analysis_content))


def test_plan_result_create_read_replay_and_version_identities(tmp_path):
    database_url, profile, request, analysis, source_hash = _analysis(tmp_path)
    _, analysis_plan, _ = _sources(database_url, analysis.analysis_id)
    candidate = InvestigationAgent().build(request, analysis_plan, analysis, profile, selected_dimensions=["region"])
    with Session(create_database_engine(database_url)) as session:
        plan_store = InvestigationPlanStore(session)
        saved, replayed = plan_store.get_or_create(candidate)
        assert not replayed and plan_store.get(saved.investigation_plan_id) == saved
        assert plan_store.get_or_create(candidate.model_copy(update={"investigation_plan_id": "new"}))[1]
        changed_version = candidate.model_copy(update={"investigator_version": "5.1.0"})
        changed_prompt = candidate.model_copy(update={"prompt_version": "investigation-v2"})
        changed_method = candidate.model_copy(update={"top_n": 3})
        assert len({plan_store.identity(item) for item in (candidate, changed_version, changed_prompt, changed_method)}) == 4
        from investigation.engine import InvestigationEngine
        import pandas as pd
        result = InvestigationEngine().execute(pd.read_csv(tmp_path / "sales.csv"), saved, request, analysis_plan, analysis, profile, source_hash)
        result_store = InvestigationResultStore(session)
        identity = result_store.identity(saved.investigation_plan_id, source_hash, "5.0.0")
        saved_result, replayed = result_store.get_or_create(result, identity)
        assert not replayed and result_store.get(saved_result.investigation_id) == saved_result
        assert result_store.get_or_create(result.model_copy(update={"investigation_id": "new"}), identity)[1]
        assert len({result_store.identity(saved.investigation_plan_id, source_hash, "5.0.0"),
                    result_store.identity(saved.investigation_plan_id, "changed", "5.0.0"),
                    result_store.identity(saved.investigation_plan_id, source_hash, "5.1.0")}) == 3


def test_execute_and_replay_end_to_end(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    first = execute_investigation(analysis.analysis_id, database_url, dimensions=["region"], top_n=3)
    second = execute_investigation(analysis.analysis_id, database_url, dimensions=["region"], top_n=3)
    assert first.result.status == "COMPLETED"
    assert first.result.reconciliation[0].reconciled
    assert (first.plan_replayed, first.investigation_replayed) == (False, False)
    assert (second.plan_replayed, second.investigation_replayed) == (True, True)
    assert first.result.investigation_id == second.result.investigation_id


def test_cli_json_text_unsupported_metric_and_missing(tmp_path, monkeypatch, capsys):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["investigate_analysis", analysis.analysis_id, "--dimension", "region", "--top", "3", "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["investigation"]["status"] == "COMPLETED"
    _print_text(execute_investigation(analysis.analysis_id, database_url, dimensions=["region"], top_n=3))
    output = capsys.readouterr().out
    assert "Direction" not in output and "DRIVES_CHANGE" in output and "status=PASS" in output
    with pytest.raises(LookupError, match="not found"):
        execute_investigation("missing", database_url)

    derived_path = tmp_path / "derived"
    derived_path.mkdir()
    other_url, _, _, derived, _ = _analysis(derived_path, metric="average_order_value")
    with pytest.raises(ValueError, match="NON_ADDITIVE_METRIC"):
        execute_investigation(derived.analysis_id, other_url, dimensions=["region"])


def test_contract_roundtrip_schema():
    assert InvestigationPlan.model_json_schema()["additionalProperties"] is False
    assert InvestigationResult.model_json_schema()["additionalProperties"] is False
