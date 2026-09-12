"""Persist versioned V6 critic reviews."""
from alembic import op
import sqlalchemy as sa

revision = "20260911_06"
down_revision = "20260911_05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "critic_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.String(36), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("computed_analyses.id"), nullable=False),
        sa.Column("investigation_id", sa.Integer(), sa.ForeignKey("investigation_results.id"), nullable=True),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("critic_version", sa.String(64), nullable=False),
        sa.Column("qa_rules_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("provider_name", sa.String(128), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("review_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    for name, columns in (("ix_critic_reviews_review_id", ["review_id"]),
                          ("ix_critic_reviews_identity", ["identity_hash"]),
                          ("ix_critic_reviews_analysis", ["analysis_id"]),
                          ("ix_critic_reviews_investigation", ["investigation_id"]),
                          ("ix_critic_reviews_dataset", ["dataset_version_id"]),
                          ("ix_critic_reviews_status", ["status"]),
                          ("ix_critic_reviews_severity", ["severity"])):
        op.create_index(name, "critic_reviews", columns)


def downgrade():
    op.drop_table("critic_reviews")
