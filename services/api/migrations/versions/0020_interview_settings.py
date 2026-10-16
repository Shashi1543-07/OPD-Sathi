"""Hospital interview settings: the optional Ayurveda history module.

An AYUSH hospital can add six patient-reportable Ayurveda questions (appetite, digestion,
bowel habit, sleep, capacity for exertion, diet) to every kiosk and phone interview. The
answers fill the case sheet's AYUSH section, where the vaidya also records examination
findings. Off by default; each change is audited.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_interview_settings"
down_revision = "0019_queue_recall"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospital_interview_settings",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("ayurveda_module", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "updated_by"],
            ["users.hospital_id", "users.id"],
            name="fk_interview_settings_editor_tenant",
        ),
    )
    op.execute("ALTER TABLE hospital_interview_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hospital_interview_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hospital_interview_settings_tenant_isolation "
        "ON hospital_interview_settings "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT, UPDATE ON hospital_interview_settings TO osa_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospital_interview_settings")
