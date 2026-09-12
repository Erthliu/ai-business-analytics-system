"""Persist V5 investigation plans and deterministic results."""
from alembic import op
import sqlalchemy as sa

revision = "20260911_05"
down_revision = "20260911_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "investigation_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_plan_id", sa.String(36), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("analysis_requests.id"), nullable=False),
        sa.Column("analysis_plan_id", sa.Integer(), sa.ForeignKey("analysis_plans.id"), nullable=False),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("computed_analyses.id"), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("dataset_profiles.id"), nullable=False),
        sa.Column("plan_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_investigation_plans_plan_id", "investigation_plans", ["investigation_plan_id"])
    op.create_index("ix_investigation_plans_identity", "investigation_plans", ["identity_hash"])
    op.create_table(
        "investigation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("investigation_plan_id", sa.Integer(), sa.ForeignKey("investigation_plans.id"), nullable=False),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("computed_analyses.id"), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("result_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_investigation_results_result_id", "investigation_results", ["investigation_id"])
    op.create_index("ix_investigation_results_identity", "investigation_results", ["identity_hash"])


def downgrade():
    op.drop_table("investigation_results")
    op.drop_table("investigation_plans")
