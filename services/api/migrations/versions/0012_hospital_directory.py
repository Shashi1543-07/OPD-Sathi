"""Worker reads hospital names (sign-in picker) and closes out previous days' visits."""

from alembic import op

revision = "0012_hospital_directory"
down_revision = "0011_analytics_rollup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "GRANT SELECT (id, name, created_at) ON hospitals TO osa_worker; "
        "GRANT UPDATE (status) ON visits TO osa_worker; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "REVOKE SELECT (id, name, created_at) ON hospitals FROM osa_worker; "
        "REVOKE UPDATE (status) ON visits FROM osa_worker; "
        "END IF; END $$;"
    )
