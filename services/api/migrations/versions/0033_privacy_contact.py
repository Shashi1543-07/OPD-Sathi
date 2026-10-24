"""The hospital's grievance officer, shown with the privacy notice.

Phase 7, tasks 7.5.2 and 7.5.5. The full privacy notice tells patients to contact the
hospital's grievance officer first; each hospital records how (a name, a phone number, an
email, a room). Administrators set it in Settings; the public notice page shows it. Each
change is audited.
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_privacy_contact"
down_revision = "0032_feature_switches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospital_privacy_settings",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("grievance_contact", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "char_length(grievance_contact) <= 500", name="ck_privacy_contact_length"
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "updated_by"],
            ["users.hospital_id", "users.id"],
            name="fk_privacy_settings_editor_tenant",
        ),
    )
    op.execute("ALTER TABLE hospital_privacy_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hospital_privacy_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hospital_privacy_settings_tenant_isolation "
        "ON hospital_privacy_settings "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT, UPDATE ON hospital_privacy_settings TO osa_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospital_privacy_settings")
