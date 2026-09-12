"""Persist grounded requirement interpretations."""
from alembic import op
import sqlalchemy as sa
revision = "20260911_03"
down_revision = "20260911_02"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("analysis_requests",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("identity_hash", sa.String(64), nullable=False), sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("dataset_profiles.id"), nullable=False), sa.Column("pipeline_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False), sa.Column("provider_name", sa.String(128)), sa.Column("model_name", sa.String(128)),
        sa.Column("request_content", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("request_id"), sa.UniqueConstraint("identity_hash", name="uq_request_identity"))
    op.create_index("ix_analysis_requests_request_id", "analysis_requests", ["request_id"])
    op.create_index("ix_analysis_requests_identity_hash", "analysis_requests", ["identity_hash"])
def downgrade() -> None:
    op.drop_table("analysis_requests")
