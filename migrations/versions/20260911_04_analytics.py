"""Persist V4 plans and computed analyses."""
from alembic import op
import sqlalchemy as sa
revision="20260911_04"; down_revision="20260911_03"; branch_labels=None; depends_on=None
def upgrade():
    op.create_table("analysis_plans",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("plan_id",sa.String(36),nullable=False,unique=True),sa.Column("identity_hash",sa.String(64),nullable=False,unique=True),sa.Column("request_id",sa.Integer(),sa.ForeignKey("analysis_requests.id"),nullable=False),sa.Column("plan_content",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("CURRENT_TIMESTAMP"),nullable=False))
    op.create_index("ix_analysis_plans_plan_id","analysis_plans",["plan_id"]); op.create_index("ix_analysis_plans_identity_hash","analysis_plans",["identity_hash"])
    op.create_table("computed_analyses",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("analysis_id",sa.String(36),nullable=False,unique=True),sa.Column("identity_hash",sa.String(64),nullable=False,unique=True),sa.Column("plan_id",sa.Integer(),sa.ForeignKey("analysis_plans.id"),nullable=False),sa.Column("analysis_content",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("CURRENT_TIMESTAMP"),nullable=False))
    op.create_index("ix_computed_analyses_analysis_id","computed_analyses",["analysis_id"]); op.create_index("ix_computed_analyses_identity_hash","computed_analyses",["identity_hash"])
def downgrade():
    op.drop_table("computed_analyses"); op.drop_table("analysis_plans")
