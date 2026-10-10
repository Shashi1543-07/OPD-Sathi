"""Explainable triage score per visit: total points and the breakdown staff see."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_triage_score"
down_revision = "0012_hospital_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("visits", sa.Column("acuity_score", sa.SmallInteger(), nullable=True))
    op.add_column("visits", sa.Column("acuity", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_visits_acuity_score", "visits", "acuity_score IS NULL OR acuity_score BETWEEN 0 AND 100"
    )
    # The worker rebuilds the Redis queue order, which now depends on the score.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "GRANT SELECT (acuity_score) ON visits TO osa_worker; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "REVOKE SELECT (acuity_score) ON visits FROM osa_worker; "
        "END IF; END $$;"
    )
    op.drop_constraint("ck_visits_acuity_score", "visits", type_="check")
    op.drop_column("visits", "acuity")
    op.drop_column("visits", "acuity_score")
