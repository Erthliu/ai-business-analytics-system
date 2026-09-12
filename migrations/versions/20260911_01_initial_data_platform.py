"""Create V1.2 data-platform tables."""

from alembic import op
import sqlalchemy as sa

revision = "20260911_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create pipeline operational metadata, lineage, and immutable versions."""
    op.create_table("pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False), sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("input_row_count", sa.Integer()),
        sa.Column("validation_passed", sa.Boolean()), sa.Column("validation_statistics", sa.JSON()), sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("run_id"), sa.UniqueConstraint("source_hash"))
    op.create_index("ix_pipeline_runs_run_id", "pipeline_runs", ["run_id"])
    op.create_index("ix_pipeline_runs_source_hash", "pipeline_runs", ["source_hash"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])
    op.create_table("dataset_versions",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False), sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("schema_fingerprint", sa.String(64), nullable=False), sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.UniqueConstraint("version_id"), sa.UniqueConstraint("content_hash"), sa.UniqueConstraint("pipeline_run_id"))
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])
    op.create_index("ix_dataset_versions_version_id", "dataset_versions", ["version_id"])
    op.create_index("ix_dataset_versions_content_hash", "dataset_versions", ["content_hash"])
    op.create_table("dataset_lineage",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("pipeline_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False), sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("output_name", sa.String(128), nullable=False), sa.Column("transformation", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("pipeline_run_id", "source_hash", "output_name", name="uq_lineage_edge"))
    op.create_index("ix_dataset_lineage_pipeline_run_id", "dataset_lineage", ["pipeline_run_id"])
    op.create_index("ix_dataset_lineage_source_hash", "dataset_lineage", ["source_hash"])


def downgrade() -> None:
    """Drop all V1.2 tables."""
    op.drop_table("dataset_lineage")
    op.drop_table("dataset_versions")
    op.drop_table("pipeline_runs")
