"""Hospital-wide report settings: the minutes each kiosk interview is assumed to save.

The Reports page kept this estimate in each administrator's browser, so two admins of
the same hospital could see different "time saved" totals. One row per hospital now holds
it for everyone, with who changed it and when (each change is also audited).
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_report_settings"
down_revision = "0014_analytics_case_sheets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospital_report_settings",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "minutes_saved_per_visit",
            sa.Numeric(4, 1),
            nullable=False,
            server_default=sa.text("3.0"),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "updated_by"],
            ["users.hospital_id", "users.id"],
            name="fk_report_settings_editor_tenant",
        ),
        sa.CheckConstraint(
            "minutes_saved_per_visit >= 0 AND minutes_saved_per_visit <= 60",
            name="ck_report_settings_minutes",
        ),
    )
    op.execute("ALTER TABLE hospital_report_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hospital_report_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hospital_report_settings_tenant_isolation ON hospital_report_settings "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') "
        "THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE ON hospital_report_settings TO osa_app'; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospital_report_settings")
