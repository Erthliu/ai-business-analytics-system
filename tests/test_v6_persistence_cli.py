"""V6 persistence, replay, and CLI integration tests."""
import json

import pytest
from sqlalchemy.orm import Session

from database.models import ComputedAnalysisRecord, CriticReviewRecord, DatasetProfileRecord
from database.persistence import create_database_engine
from qa.review_store import CriticReviewStore
from scripts.investigate_analysis import execute_investigation
from scripts.review_analysis import _print_text, execute_review, main
from test_v5_persistence_cli import _analysis


def test_v4_only_review_create_read_and_replay(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    first = execute_review(analysis.analysis_id, database_url)
    second = execute_review(analysis.analysis_id, database_url)
    assert first.review.overall_status == "PASS" and not first.replayed
    assert second.replayed and first.review.review_id == second.review.review_id
    with Session(create_database_engine(database_url)) as session:
        assert session.query(CriticReviewRecord).count() == 1
        assert CriticReviewStore(session).get(first.review.review_id) == first.review


def test_v4_v5_review_and_identity_versions(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    investigation = execute_investigation(analysis.analysis_id, database_url, dimensions=["region"]).result
    reviewed = execute_review(analysis.analysis_id, database_url, investigation.investigation_id)
    replay = execute_review(analysis.analysis_id, database_url, investigation.investigation_id)
    assert reviewed.review.overall_status == "PASS"
    assert reviewed.review.investigation_id == investigation.investigation_id
    assert replay.replayed
    identity = CriticReviewStore.identity
    values = {
        identity(analysis.analysis_id, investigation.investigation_id, "6", "1", "p"),
        identity(analysis.analysis_id, investigation.investigation_id, "7", "1", "p"),
        identity(analysis.analysis_id, investigation.investigation_id, "6", "2", "p"),
        identity(analysis.analysis_id, investigation.investigation_id, "6", "1", "p2"),
        identity(analysis.analysis_id, investigation.investigation_id, "6", "1", "p", "provider", "model"),
    }
    assert len(values) == 5


def test_cli_json_and_text_replay(tmp_path, monkeypatch, capsys):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["review_analysis", analysis.analysis_id, "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"]["overall_status"] == "PASS" and not payload["replayed"]
    _print_text(execute_review(analysis.analysis_id, database_url))
    output = capsys.readouterr().out
    assert "Review: PASS" in output and "Replay: true" in output and "provenance: PASS" in output


def test_cli_fail_and_missing_artifacts(tmp_path, monkeypatch, capsys):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    with Session(create_database_engine(database_url)) as session:
        record = session.query(ComputedAnalysisRecord).filter_by(analysis_id=analysis.analysis_id).one()
        content = dict(record.analysis_content)
        rows = [dict(row) for row in content["rows"]]
        rows[0]["absolute_change"] = 999
        content["rows"] = rows
        record.analysis_content = content
        session.commit()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["review_analysis", analysis.analysis_id])
    main()
    assert "Review: FAIL" in capsys.readouterr().out
    with pytest.raises(LookupError, match="not found"):
        execute_review("missing", database_url)
    with pytest.raises(LookupError, match="Investigation result not found"):
        execute_review(analysis.analysis_id, database_url, "missing")


def test_cli_v5_json_and_warning_output(tmp_path, monkeypatch, capsys):
    v5_path = tmp_path / "v5"; v5_path.mkdir()
    database_url, _, _, analysis, _ = _analysis(v5_path)
    investigation = execute_investigation(analysis.analysis_id, database_url, dimensions=["region"]).result
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["review_analysis", analysis.analysis_id, "--investigation-id", investigation.investigation_id, "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"]["investigation_id"] == investigation.investigation_id

    warning_path = tmp_path / "warning"; warning_path.mkdir()
    warning_url, _, _, warning_analysis, _ = _analysis(warning_path)
    with Session(create_database_engine(warning_url)) as session:
        record = session.query(DatasetProfileRecord).one()
        content = dict(record.profile_content)
        content["warnings"] = ["Partial period coverage detected."]
        record.profile_content = content
        session.commit()
    monkeypatch.setenv("DATABASE_URL", warning_url)
    monkeypatch.setattr("sys.argv", ["review_analysis", warning_analysis.analysis_id])
    main()
    assert "Review: PASS_WITH_WARNINGS" in capsys.readouterr().out
