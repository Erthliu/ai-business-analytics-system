"""V8 migration, persistence, replay, and CLI integration tests."""
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from contracts.reporting import Audience
from database.models import (
    BusinessInsightRecord, DatasetProfileRecord, ReportArtifactRecord, ReportSpecRecord,
)
from database.persistence import create_database_engine
from reporting.store import ReportArtifactStore, ReportSpecStore
from scripts.generate_report import execute_report, main
from scripts.interpret_business import execute_interpretation
from scripts.investigate_analysis import execute_investigation
from scripts.review_analysis import execute_review
from test_v5_persistence_cli import _analysis


def _insight(tmp_path, *, investigation=False):
    database_url, _, _, analysis, _ = _analysis(tmp_path)
    investigation_id = None
    if investigation:
        investigation_id = execute_investigation(
            analysis.analysis_id, database_url, dimensions=["region"]
        ).result.investigation_id
    review = execute_review(analysis.analysis_id, database_url, investigation_id).review
    insight = execute_interpretation(review.review_id, database_url).insight
    return database_url, insight


def test_v8_migration_creates_both_tables(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'v8-migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    tables = inspect(create_engine(database_url)).get_table_names()
    assert {"report_specs", "report_artifacts"} <= set(tables)


def test_spec_and_report_create_read_and_replay(tmp_path):
    database_url, insight = _insight(tmp_path)
    first = execute_report(insight.insight_id, database_url)
    second = execute_report(insight.insight_id, database_url)
    assert not first.spec_replayed and not first.report_replayed
    assert second.spec_replayed and second.report_replayed
    assert first.spec.report_spec_id == second.spec.report_spec_id
    assert first.report.report_id == second.report.report_id
    with Session(create_database_engine(database_url)) as session:
        assert session.query(ReportSpecRecord).count() == 1
        assert session.query(ReportArtifactRecord).count() == 1
        assert ReportSpecStore(session).get(first.spec.report_spec_id) == first.spec
        assert ReportArtifactStore(session).get(first.report.report_id) == first.report


def test_v5_report_and_audience_create_distinct_specs(tmp_path):
    database_url, insight = _insight(tmp_path, investigation=True)
    executive = execute_report(insight.insight_id, database_url, Audience.EXECUTIVE)
    analyst = execute_report(insight.insight_id, database_url, Audience.ANALYST)
    assert executive.spec.report_spec_id != analyst.spec.report_spec_id
    assert analyst.spec.audience == Audience.ANALYST
    assert any(section.section_type == "METHODOLOGY" for section in analyst.report.rendered_sections)
    assert any(chart.chart_type == "BAR" for chart in executive.report.charts)


def test_identity_changes_for_versions_provider_and_renderer():
    spec_identity = ReportSpecStore.identity
    spec_values = {
        spec_identity("insight", "EXECUTIVE", "8", "prompt", "rules"),
        spec_identity("insight", "ANALYST", "8", "prompt", "rules"),
        spec_identity("insight", "EXECUTIVE", "9", "prompt", "rules"),
        spec_identity("insight", "EXECUTIVE", "8", "prompt-2", "rules"),
        spec_identity("insight", "EXECUTIVE", "8", "prompt", "rules-2"),
        spec_identity("insight", "EXECUTIVE", "8", "prompt", "rules", "provider", "model"),
    }
    assert len(spec_values) == 6
    report_identity = ReportArtifactStore.identity
    assert len({report_identity("spec", "insight", "8", "rules"),
                report_identity("spec", "insight", "9", "rules"),
                report_identity("spec", "insight", "8", "rules-2")}) == 3


def test_cli_json_markdown_analyst_and_replay(tmp_path, monkeypatch, capsys):
    database_url, insight = _insight(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["generate_report", insight.insight_id,
                                     "--audience", "analyst", "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_spec"]["audience"] == "ANALYST"
    assert payload["report"]["status"] == "COMPLETE"
    assert not payload["report_replayed"]
    monkeypatch.setattr("sys.argv", ["generate_report", insight.insight_id,
                                     "--audience", "analyst", "--markdown"])
    main()
    output = capsys.readouterr().out
    assert output.startswith("# Revenue Performance Report")
    assert "## Methodology" in output


def test_caveat_cli_and_blocked_persisted_input(tmp_path, monkeypatch, capsys):
    caveat_path = tmp_path / "caveat"; caveat_path.mkdir()
    database_url, _, _, analysis, _ = _analysis(caveat_path)
    with Session(create_database_engine(database_url)) as session:
        profile = session.query(DatasetProfileRecord).one()
        content = dict(profile.profile_content)
        content["warnings"] = ["Partial period coverage detected."]
        profile.profile_content = content
        session.commit()
    review = execute_review(analysis.analysis_id, database_url).review
    insight = execute_interpretation(review.review_id, database_url).insight
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("sys.argv", ["generate_report", insight.insight_id])
    main()
    output = capsys.readouterr().out
    assert "Status: COMPLETE_WITH_CAVEATS" in output
    assert "Partial period coverage detected." in output

    with Session(create_database_engine(database_url)) as session:
        record = session.query(BusinessInsightRecord).one()
        content = dict(record.insight_content)
        content["status"] = "BLOCKED"
        record.insight_content = content
        session.commit()
    with pytest.raises(ValueError, match="BLOCKED"):
        execute_report(insight.insight_id, database_url, Audience.OPERATIONS)


def test_missing_insight_is_explicit(tmp_path):
    database_url, _ = _insight(tmp_path)
    with pytest.raises(LookupError, match="BusinessInsight not found"):
        execute_report("missing", database_url)
