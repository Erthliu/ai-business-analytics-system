"""Create persisted data-profile records."""

from alembic import op
import sqlalchemy as sa

revision = "20260911_02"
down_revision = "20260911_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("profiler_version", sa.String(64), nullable=False),
        sa.Column("profile_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("profile_id"),
        sa.UniqueConstraint("dataset_version_id", "profiler_version", name="uq_profile_version"),
    )
    op.create_index("ix_dataset_profiles_profile_id", "dataset_profiles", ["profile_id"])
    op.create_index("ix_dataset_profiles_dataset_version_id", "dataset_profiles", ["dataset_version_id"])
    op.create_index("ix_dataset_profiles_pipeline_run_id", "dataset_profiles", ["pipeline_run_id"])


def downgrade() -> None:
    op.drop_table("dataset_profiles")
