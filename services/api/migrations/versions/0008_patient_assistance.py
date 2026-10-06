"""Persist the 90-second patient-help flag for the staff queue."""

import sqlalchemy as sa
from alembic import op

revision = "0008_patient_assistance"
down_revision = "0007_hospital_ai_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "visits",
        sa.Column("needs_assistance", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("visits", "needs_assistance")
