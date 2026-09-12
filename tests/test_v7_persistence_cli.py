"""V7 persistence, replay, provenance, and CLI integration tests."""
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from business.store import BusinessInsightStore
from business.validator import BusinessInterpretationError
from database.models import BusinessInsightRecord, ComputedAnalysisRecord, DatasetProfileRecord
from database.persistence import create_database_engine
from scripts.interpret_business import _print_text, execute_interpretation, main
from scripts.investigate_analysis import execute_investigation
from scripts.review_analysis import execute_review
from test_v5_persistence_cli import _analysis


def test_v7_migration_creates_business_insight_table(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'v7-migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert "business_insights" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("business_insights")}
    assert {"identity_hash", "critic_review_id", "analysis_id", "investigation_id",
            "dataset_version_id", "insight_content"} <= columns


def test_v4_only_insight_create_read_and_idempotent_replay(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    review = execute_review(analysis.analysis_id, database_url).review

    first = execute_interpretation(review.review_id, database_url)
    second = execute_interpretation(review.review_id, database_url)

    assert first.insight.status == "COMPLETE"
    assert not first.replayed
    assert second.replayed
    assert first.insight.insight_id == second.insight.insight_id
    with Session(create_database_engine(database_url)) as session:
        assert session.query(BusinessInsightRecord).count() == 1
        assert BusinessInsightStore(session).get(first.insight.insight_id) == first.insight


def test_v5_evidence_is_loaded_from_review_provenance(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    investigation = execute_investigation(
        analysis.analysis_id, database_url, dimensions=["region"]
    ).result
    review = execute_review(
        analysis.analysis_id, database_url, investigation.investigation_id
    ).review

    insight = execute_interpretation(review.review_id, database_url).insight

    assert insight.investigation_id == investigation.investigation_id
    assert any(item.artifact_type == "InvestigationResult" for item in insight.evidence)
    assert any(claim.claim_type == "TOP_CONTRIBUTOR" for claim in insight.claims)


def test_identity_changes_for_every_behavior_version():
    identity = BusinessInsightStore.identity
    values = {
        identity("review", "analysis", None, "7", "prompt", "rules"),
        identity("review", "analysis", "investigation", "7", "prompt", "rules"),
        identity("review", "analysis", None, "8", "prompt", "rules"),
        identity("review", "analysis", None, "7", "prompt-2", "rules"),
        identity("review", "analysis", None, "7", "prompt", "rules-2"),
        identity("review", "analysis", None, "7", "prompt", "rules", "provider", "model"),
    }
    assert len(values) == 6


def test_cli_json_and_text_replay(tmp_path, monkeypatch, capsys):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    review = execute_review(analysis.analysis_id, database_url).review
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["interpret_business", review.review_id, "--json"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["insight"]["status"] == "COMPLETE"
    assert not payload["replayed"]
    _print_text(execute_interpretation(review.review_id, database_url))
    output = capsys.readouterr().out
    assert "Status: COMPLETE" in output
    assert "Headline:" in output
    assert "Replay: true" in output


def test_warning_review_is_preserved_as_business_caveat(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    with Session(create_database_engine(database_url)) as session:
        record = session.query(DatasetProfileRecord).one()
        content = dict(record.profile_content)
        content["warnings"] = ["Partial period coverage detected."]
        record.profile_content = content
        session.commit()
    review = execute_review(analysis.analysis_id, database_url).review

    insight = execute_interpretation(review.review_id, database_url).insight

    assert insight.status == "COMPLETE_WITH_CAVEATS"
    assert "Partial period coverage detected." in insight.caveats


def test_failed_review_blocks_and_missing_review_is_explicit(tmp_path):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    with Session(create_database_engine(database_url)) as session:
        record = session.query(ComputedAnalysisRecord).filter_by(
            analysis_id=analysis.analysis_id
        ).one()
        content = dict(record.analysis_content)
        rows = [dict(row) for row in content["rows"]]
        rows[0]["absolute_change"] = 999
        content["rows"] = rows
        record.analysis_content = content
        session.commit()
    review = execute_review(analysis.analysis_id, database_url).review
    assert review.overall_status == "FAIL"

    with pytest.raises(BusinessInterpretationError, match="FAIL blocks"):
        execute_interpretation(review.review_id, database_url)
    with pytest.raises(LookupError, match="CriticReview not found"):
        execute_interpretation("missing", database_url)
