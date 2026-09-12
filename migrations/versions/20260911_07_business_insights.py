"""Persist evidence-bound V7 business insights."""
from alembic import op
import sqlalchemy as sa

revision = "20260911_07"
down_revision = "20260911_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("insight_id", sa.String(36), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("critic_review_id", sa.Integer(), sa.ForeignKey("critic_reviews.id"), nullable=False),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("computed_analyses.id"), nullable=False),
        sa.Column("investigation_id", sa.Integer(), sa.ForeignKey("investigation_results.id"), nullable=True),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("business_rules_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("provider_name", sa.String(128), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("insight_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    for name, columns in (("ix_business_insights_insight", ["insight_id"]),
                          ("ix_business_insights_identity", ["identity_hash"]),
                          ("ix_business_insights_review", ["critic_review_id"]),
                          ("ix_business_insights_analysis", ["analysis_id"]),
                          ("ix_business_insights_investigation", ["investigation_id"]),
                          ("ix_business_insights_dataset", ["dataset_version_id"]),
                          ("ix_business_insights_status", ["status"])):
        op.create_index(name, "business_insights", columns)


def downgrade():
    op.drop_table("business_insights")
