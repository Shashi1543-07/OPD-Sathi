"""Who answered for the patient: the patient, a parent or guardian, or a representative.

Phase 7, tasks 7.5.3 and 7.11.7. ``visits.answered_by`` says who gave the history (the case
sheet and its PDF show it to the doctor); ``consents.given_by`` says who agreed to each notice.
Both default to the patient, which is what every earlier visit recorded. The application may
set ``given_by`` like the other answer fields of a consent (0025) and only while the visit is
still registering.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_answered_by"
down_revision = "0030_review_of_systems"
branch_labels = None
depends_on = None

_WHO = "('patient', 'guardian', 'representative')"


def upgrade() -> None:
    op.add_column(
        "visits",
        sa.Column("answered_by", sa.String(16), nullable=False, server_default="patient"),
    )
    op.create_check_constraint("ck_visits_answered_by", "visits", f"answered_by IN {_WHO}")
    op.add_column(
        "consents",
        sa.Column("given_by", sa.String(16), nullable=False, server_default="patient"),
    )
    op.create_check_constraint("ck_consents_given_by", "consents", f"given_by IN {_WHO}")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT UPDATE (given_by) ON consents TO osa_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "REVOKE UPDATE (given_by) ON consents FROM osa_app; "
        "END IF; END $$;"
    )
    op.drop_constraint("ck_consents_given_by", "consents", type_="check")
    op.drop_column("consents", "given_by")
    op.drop_constraint("ck_visits_answered_by", "visits", type_="check")
    op.drop_column("visits", "answered_by")
