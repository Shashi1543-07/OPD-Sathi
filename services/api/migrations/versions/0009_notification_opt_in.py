"""Persist explicit per-visit opt-in for queue status notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0009_notification_opt_in"
down_revision = "0008_patient_assistance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "visits",
        sa.Column("sms_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("visits", sa.Column("sms_opt_in_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_visits_sms_opt_in_timestamp",
        "visits",
        "(sms_opt_in AND sms_opt_in_at IS NOT NULL) OR (NOT sms_opt_in AND sms_opt_in_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_visits_sms_opt_in_timestamp", "visits", type_="check")
    op.drop_column("visits", "sms_opt_in_at")
    op.drop_column("visits", "sms_opt_in")
