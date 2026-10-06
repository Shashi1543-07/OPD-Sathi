"""Add a tenant-scoped opt-in switch for AI features."""

import sqlalchemy as sa
from alembic import op

revision = "0007_hospital_ai_settings"
down_revision = "0006_redflag_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospital_ai_settings",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("llm_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("ALTER TABLE hospital_ai_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hospital_ai_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hospital_ai_settings_tenant_isolation ON hospital_ai_settings "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') "
        "THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON hospital_ai_settings TO osa_app'; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospital_ai_settings")
