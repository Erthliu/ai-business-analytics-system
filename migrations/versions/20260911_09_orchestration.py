"""Persist controlled V9 workflows and append-only stage events."""
from alembic import op
import sqlalchemy as sa

revision = "20260911_09"
down_revision = "20260911_08"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, unique=True),
        sa.Column("workflow_identity_hash", sa.String(64), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("dataset_profiles.id"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_stage", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "workflow_stage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("workflow_run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("artifact_refs", sa.JSON(), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    for column in ("workflow_run_id", "workflow_identity_hash", "dataset_version_id", "profile_id", "status", "current_stage", "created_at"):
        op.create_index(f"ix_workflow_runs_{column}", "workflow_runs", [column])
    for column in ("event_id", "workflow_run_id", "stage", "event_type", "occurred_at"):
        op.create_index(f"ix_workflow_stage_events_{column}", "workflow_stage_events", [column])


def downgrade():
    op.drop_table("workflow_stage_events")
    op.drop_table("workflow_runs")
