"""Hospital interview settings: the optional review of systems.

Phase 7, task 7.11.2. A hospital can add one checklist screen ("In the last month, have you
had any of these?") with one choice per body system to every kiosk and phone interview;
systems another module of the interview already covers are left out. Off by default, like
every new clinical feature (task 7.14.7); each change is audited.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_review_of_systems"
down_revision = "0029_doctor_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hospital_interview_settings",
        sa.Column(
            "review_of_systems_module", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("hospital_interview_settings", "review_of_systems_module")
