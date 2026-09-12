"""Persist V8 report specifications and artifacts."""
from alembic import op
import sqlalchemy as sa

revision = "20260911_08"
down_revision = "20260911_07"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_specs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_spec_id", sa.String(36), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("business_insight_id", sa.Integer(), sa.ForeignKey("business_insights.id"), nullable=False),
        sa.Column("critic_review_id", sa.Integer(), sa.ForeignKey("critic_reviews.id"), nullable=False),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("computed_analyses.id"), nullable=False),
        sa.Column("investigation_id", sa.Integer(), sa.ForeignKey("investigation_results.id"), nullable=True),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("report_rules_version", sa.String(64), nullable=False),
        sa.Column("provider_name", sa.String(128), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("spec_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "report_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.String(36), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("report_spec_id", sa.Integer(), sa.ForeignKey("report_specs.id"), nullable=False),
        sa.Column("business_insight_id", sa.Integer(), sa.ForeignKey("business_insights.id"), nullable=False),
        sa.Column("critic_review_id", sa.Integer(), sa.ForeignKey("critic_reviews.id"), nullable=False),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("computed_analyses.id"), nullable=False),
        sa.Column("investigation_id", sa.Integer(), sa.ForeignKey("investigation_results.id"), nullable=True),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("renderer_version", sa.String(64), nullable=False),
        sa.Column("report_rules_version", sa.String(64), nullable=False),
        sa.Column("artifact_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    indexes = {
        "report_specs": ("report_spec_id", "identity_hash", "business_insight_id", "critic_review_id", "analysis_id", "investigation_id", "dataset_version_id", "audience"),
        "report_artifacts": ("report_id", "identity_hash", "report_spec_id", "business_insight_id", "critic_review_id", "analysis_id", "investigation_id", "dataset_version_id", "status"),
    }
    for table, columns in indexes.items():
        for column in columns: op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("report_artifacts")
    op.drop_table("report_specs")
