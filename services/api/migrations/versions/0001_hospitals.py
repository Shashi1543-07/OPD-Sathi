"""Create the tenant root table."""

import sqlalchemy as sa
from alembic import op

revision = "0001_hospitals"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospitals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False, unique=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') "
        "THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE ON hospitals TO osa_app'; END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospitals")
