"""Hospital feature switches: SMS messages and phone check-in.

Phase 7, task 7.14.7. SMS queue messages are off until the hospital turns them on (they need
a provider contract and DLT registration, task 7.11.6); while off, kiosks and phones do not
offer the SMS tick and no message is queued. Phone check-in keeps working as before unless
the hospital turns it off, which stops every check-in QR at once. Each change is audited.
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_feature_switches"
down_revision = "0031_answered_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospital_feature_settings",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("sms_messages", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("phone_checkin", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "updated_by"],
            ["users.hospital_id", "users.id"],
            name="fk_feature_settings_editor_tenant",
        ),
    )
    op.execute("ALTER TABLE hospital_feature_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hospital_feature_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hospital_feature_settings_tenant_isolation "
        "ON hospital_feature_settings "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT, UPDATE ON hospital_feature_settings TO osa_app; "
        "END IF; END $$;"
    )
    # The worker queues the messages, so it reads the SMS switch and nothing else here.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "GRANT SELECT (hospital_id, sms_messages) ON hospital_feature_settings TO osa_worker; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospital_feature_settings")
