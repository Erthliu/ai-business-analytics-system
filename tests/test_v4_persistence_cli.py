"""Persistence, replay, CLI, and end-to-end tests for V4."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from agents.analytics_engine import AnalyticsEngine
from agents.analytics_planner import AnalyticsPlanner
from analytics.plan_store import AnalysisPlanStore
from analytics.result_store import ComputedAnalysisStore
from contracts.analysis_plan import AnalysisPlan
from contracts.analysis_request import AnalysisRequest, DateRange, RequestStatus
from contracts.computed_analysis import ComputedAnalysis
from contracts.dataset_profile import ColumnProfile, DatasetProfile, NumericStatistics
from database.models import AnalysisRequestRecord, DatasetProfileRecord, DatasetVersion, PipelineRun
from database.persistence import create_database_engine, initialize_test_database
from scripts.run_analysis import _print_text, execute_request, main


def _period(start: str, end: str) -> DateRange:
    return DateRange(start=date.fromisoformat(start), end=date.fromisoformat(end), source="test")


def _profile(version_id: str, run_id: str) -> DatasetProfile:
    numeric = lambda: NumericStatistics(zero_count=0, negative_count=0)
    values = [
        ("revenue", "float64", numeric()), ("quantity", "int64", numeric()),
        ("order_id", "object", None), ("customer_id", "object", None),
        ("region", "object", None), ("product_id", "object", None), ("order_date", "object", None),
    ]
    columns = [ColumnProfile(name=name, dtype=dtype, null_count=0, null_percentage=0,
                             unique_count=4, uniqueness_ratio=1, numeric=stats,
                             minimum_date="2024-01-01" if name == "order_date" else None,
                             maximum_date="2024-08-31" if name == "order_date" else None)
               for name, dtype, stats in values]
    return DatasetProfile(profile_id=str(uuid4()), dataset_version_id=version_id, pipeline_run_id=run_id,
                          row_count=5, column_count=len(columns), schema_fingerprint="schema",
                          profiler_version="2.0.0", columns=columns)


def _database(tmp_path: Path) -> tuple[str, DatasetProfile, str]:
    source = tmp_path / "sales.csv"
    pd.DataFrame({
        "order_id": ["o1", "o2", "o3", "o4", "o5"], "order_date": ["2024-01-01", "2024-07-01", "2024-08-01", "2024-08-02", "2024-08-03"],
        "customer_id": ["c1", "c1", "c2", "c3", "c4"], "product_id": ["p1", "p2", "p1", "p3", "p2"],
        "region": ["N", "N", "S", "S", "N"], "quantity": [1, 2, 3, 4, 5],
        "unit_price": [0, 5, 10, 5, 2], "revenue": [0, 10, 30, 20, 10],
    }).to_csv(source, index=False)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    database_url = f"sqlite+pysqlite:///{tmp_path / 'v4.db'}"
    engine = create_database_engine(database_url)
    initialize_test_database(engine)
    with Session(engine) as session:
        run = PipelineRun(run_id=str(uuid4()), source_hash=source_hash, source_path=str(source), status="completed")
        session.add(run); session.flush()
        version = DatasetVersion(dataset_id=str(uuid4()), version_id=str(uuid4()), content_hash=source_hash,
                                 source_uri=source.resolve().as_uri(), storage_uri=source.resolve().as_uri(),
                                 schema_fingerprint="schema", row_count=5, pipeline_run_id=run.id)
        session.add(version); session.flush()
        profile = _profile(version.version_id, run.run_id)
        profile_record = DatasetProfileRecord(profile_id=profile.profile_id, dataset_version_id=version.id,
                                               pipeline_run_id=run.id, profiler_version=profile.profiler_version,
                                               profile_content=profile.model_dump(mode="json"))
        session.add(profile_record); session.commit()
    return database_url, profile, source_hash


def _persist_request(database_url: str, profile: DatasetProfile, question: str, metric="revenue", dimensions=None,
                     analysis_period=None, comparison_period=None, filters=None, status=RequestStatus.READY) -> AnalysisRequest:
    request = AnalysisRequest(
        original_question=question, primary_metric=metric, dimensions=dimensions or [], filters=filters or [],
        analysis_period=analysis_period, comparison_period=comparison_period,
        dataset_version_id=profile.dataset_version_id, profile_id=profile.profile_id,
        status=status, agent_version="3.0.0", prompt_version="requirement-v1",
    )
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        version = session.query(DatasetVersion).filter_by(version_id=profile.dataset_version_id).one()
        profile_record = session.query(DatasetProfileRecord).filter_by(profile_id=profile.profile_id).one()
        session.add(AnalysisRequestRecord(
            request_id=request.request_id, identity_hash=hashlib.sha256(request.request_id.encode()).hexdigest(),
            dataset_version_id=version.id, profile_id=profile_record.id, pipeline_run_id=version.pipeline_run_id,
            status=request.status, agent_version=request.agent_version, prompt_version=request.prompt_version,
            request_content=request.model_dump(mode="json"),
        ))
        session.commit()
    return request


def test_plan_and_result_store_create_read_replay_and_versions(tmp_path):
    database_url, profile, source_hash = _database(tmp_path)
    request = _persist_request(database_url, profile, "revenue")
    engine = create_database_engine(database_url)
    planner = AnalyticsPlanner()
    plan = planner.build(request, profile)
    with Session(engine) as session:
        store = AnalysisPlanStore(session)
        identity = store.identity(request, "4", "p1", "r1")
        saved, replayed = store.get_or_create(plan, identity)
        assert not replayed and store.get(saved.plan_id) == saved
        assert store.get_or_create(plan.model_copy(update={"plan_id": str(uuid4())}), identity)[1]
        assert len({store.identity(request, value, "p1", "r1") for value in ("4", "5")}) == 2
        assert len({store.identity(request, "4", value, "r1") for value in ("p1", "p2")}) == 2
        assert len({store.identity(request, "4", "p1", value) for value in ("r1", "r2")}) == 2
        assert store.identity(request, "4", "p1", "r1") != store.identity(request, "4", "p1", "r1", "provider", "model")
        result = AnalyticsEngine().execute(pd.read_csv(tmp_path / "sales.csv"), saved, source_hash)
        result_store = ComputedAnalysisStore(session)
        result_identity = result_store.identity(saved.plan_id, saved.dataset_version_id, source_hash, "4", "r1")
        saved_result, replayed = result_store.get_or_create(result, result_identity)
        assert not replayed and result_store.get(saved_result.analysis_id) == saved_result
        assert result_store.get_or_create(result.model_copy(update={"analysis_id": str(uuid4())}), result_identity)[1]
        identities = {
            result_store.identity(saved.plan_id, saved.dataset_version_id, source_hash, "4", "r1"),
            result_store.identity(saved.plan_id, saved.dataset_version_id, source_hash, "5", "r1"),
            result_store.identity(saved.plan_id, saved.dataset_version_id, "changed", "4", "r1"),
            result_store.identity(saved.plan_id, "new-version", source_hash, "4", "r1"),
        }
        assert len(identities) == 4


@pytest.mark.parametrize(("question", "metric", "dimensions", "analysis", "comparison", "filters", "result_type"), [
    ("total revenue", "revenue", [], None, None, [], "SCALAR"),
    ("revenue by region", "revenue", ["region"], None, None, [], "TABLE"),
    ("customer count", "customer_count", [], None, None, [], "SCALAR"),
    ("average order value", "average_order_value", [], None, None, [], "SCALAR"),
    ("August vs July revenue", "revenue", [], _period("2024-08-01", "2024-08-31"), _period("2024-07-01", "2024-07-31"), [], "PERIOD_COMPARISON"),
    ("August vs July revenue by region", "revenue", ["region"], _period("2024-08-01", "2024-08-31"), _period("2024-07-01", "2024-07-31"), [], "GROUPED_PERIOD_COMPARISON"),
    ("monthly revenue", "revenue", [], None, None, [], "TIME_SERIES"),
    ("top 5 products by revenue", "revenue", ["product_id"], None, None, [], "RANKING"),
    ("revenue where region is selected", "revenue", [], None, None, ["region IN (N)"], "SCALAR"),
    ("revenue where quantity threshold", "revenue", [], None, None, ["quantity GTE 3"], "SCALAR"),
    ("revenue in date range", "revenue", [], None, None, ["order_date BETWEEN 2024-08-01 AND 2024-08-31"], "SCALAR"),
])
def test_end_to_end_examples(tmp_path, question, metric, dimensions, analysis, comparison, filters, result_type):
    database_url, profile, _ = _database(tmp_path)
    request = _persist_request(database_url, profile, question, metric, dimensions, analysis, comparison, filters)
    first = execute_request(request.request_id, database_url)
    second = execute_request(request.request_id, database_url)
    assert first.result.result_type == result_type
    assert (first.plan_replayed, first.analysis_replayed) == (False, False)
    assert (second.plan_replayed, second.analysis_replayed) == (True, True)
    assert first.result.analysis_id == second.result.analysis_id


def test_cli_json_text_and_request_failures(tmp_path, monkeypatch, capsys):
    database_url, profile, _ = _database(tmp_path)
    request = _persist_request(database_url, profile, "total revenue")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["run_analysis", request.request_id, "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["analysis"]["execution_status"] == "COMPLETED"
    _print_text(execute_request(request.request_id, database_url))
    assert "Analysis replayed: true" in capsys.readouterr().out
    blocked = _persist_request(database_url, profile, "unclear", status=RequestStatus.NEEDS_CLARIFICATION)
    with pytest.raises(ValueError, match="not READY"):
        execute_request(blocked.request_id, database_url)
    with pytest.raises(LookupError, match="not found"):
        execute_request("missing", database_url)


def test_result_contract_json_schema():
    schema = ComputedAnalysis.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "source_dataset_hash" in schema["required"]
