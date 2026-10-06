"""Make one durable red-flag event per visit and rule."""

import sqlalchemy as sa
from alembic import op

revision = "0006_redflag_events"
down_revision = "0005_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_red_flag_visit_rule",
        "red_flag_events",
        ["hospital_id", "visit_id", "rule_id"],
        unique=True,
        postgresql_where=sa.text("rule_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_red_flag_visit_rule", table_name="red_flag_events")
