"""Missed patients: skip and recall, one patient per doctor, and a visit timeline.

- ``visits.status`` gains ``skipped``: the patient was called but was not there. A skipped
  patient keeps their token, encounter and arrival time; staff recall them when they come back,
  and only then (or at the end of the day) can a skipped visit become a no-show.
- ``visits.called_by``: who called the patient. ``skipped_at``, ``recalled_at`` and
  ``skip_count`` record missed calls; nothing is decided automatically from the count.
- ``visit_events``: an append-only timeline of every queue change (from, to, who, why). The
  application role can add and read rows, never change or delete them.
- ``hospital_queue_settings``: how long after a call staff must wait before marking a patient
  absent (default 3 minutes).
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_queue_recall"
down_revision = "0018_patient_identity"
branch_labels = None
depends_on = None

_OLD_STATUSES = (
    "status IN ('registering', 'awaiting_confirmation', 'queued', 'called', "
    "'in_consult', 'done', 'cancelled', 'no_show')"
)
_NEW_STATUSES = (
    "status IN ('registering', 'awaiting_confirmation', 'queued', 'called', "
    "'in_consult', 'skipped', 'done', 'cancelled', 'no_show')"
)


def upgrade() -> None:
    op.drop_constraint("ck_visits_status", "visits", type_="check")
    op.create_check_constraint("ck_visits_status", "visits", _NEW_STATUSES)
    op.add_column("visits", sa.Column("called_by", sa.Uuid()))
    op.create_foreign_key(
        "fk_visits_called_by_tenant",
        "visits",
        "users",
        ["hospital_id", "called_by"],
        ["hospital_id", "id"],
    )
    op.add_column("visits", sa.Column("skipped_at", sa.DateTime(timezone=True)))
    op.add_column("visits", sa.Column("recalled_at", sa.DateTime(timezone=True)))
    op.add_column(
        "visits", sa.Column("skip_count", sa.SmallInteger(), nullable=False, server_default="0")
    )
    op.create_check_constraint("ck_visits_skip_count", "visits", "skip_count >= 0")
    # The patient a doctor is seeing (or has called) is looked up on every call and start.
    op.create_index(
        "ix_visits_doctor_active",
        "visits",
        ["hospital_id", "assigned_doctor_id"],
        postgresql_where=sa.text("status IN ('called', 'in_consult')"),
    )
    op.create_index(
        "ix_visits_missed",
        "visits",
        ["hospital_id", "department_id", "skipped_at"],
        postgresql_where=sa.text("status = 'skipped'"),
    )

    op.create_table(
        "visit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("reason", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_visit_events_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "actor_id"],
            ["users.hospital_id", "users.id"],
            name="fk_visit_events_actor_tenant",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'patient', 'system')", name="ck_visit_events_actor_type"
        ),
        # Staff changes always name the staff member; patient and system changes never do.
        sa.CheckConstraint(
            "(actor_type = 'user') = (actor_id IS NOT NULL)", name="ck_visit_events_actor"
        ),
    )
    op.create_index(
        "ix_visit_events_visit", "visit_events", ["hospital_id", "visit_id", "created_at"]
    )
    op.execute("ALTER TABLE visit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE visit_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY visit_events_tenant_isolation ON visit_events "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )

    op.create_table(
        "hospital_queue_settings",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "call_grace_seconds", sa.SmallInteger(), nullable=False, server_default="180"
        ),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "updated_by"],
            ["users.hospital_id", "users.id"],
            name="fk_queue_settings_editor_tenant",
        ),
        sa.CheckConstraint(
            "call_grace_seconds BETWEEN 0 AND 900", name="ck_queue_settings_call_grace"
        ),
    )
    op.execute("ALTER TABLE hospital_queue_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hospital_queue_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY hospital_queue_settings_tenant_isolation ON hospital_queue_settings "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT ON visit_events TO osa_app; "
        "REVOKE UPDATE, DELETE, TRUNCATE ON visit_events FROM osa_app; "
        "GRANT SELECT, INSERT, UPDATE ON hospital_queue_settings TO osa_app; "
        "END IF; "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        # End-of-day close-out records the visits it closes.
        "GRANT INSERT ON visit_events TO osa_worker; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.drop_table("hospital_queue_settings")
    op.drop_index("ix_visit_events_visit", table_name="visit_events")
    op.drop_table("visit_events")
    op.drop_index("ix_visits_missed", table_name="visits")
    op.drop_index("ix_visits_doctor_active", table_name="visits")
    op.drop_constraint("ck_visits_skip_count", "visits", type_="check")
    op.drop_column("visits", "skip_count")
    op.drop_column("visits", "recalled_at")
    op.drop_column("visits", "skipped_at")
    op.drop_constraint("fk_visits_called_by_tenant", "visits", type_="foreignkey")
    op.drop_column("visits", "called_by")
    # The older schema has no "skipped": a patient who was called and not there becomes a
    # no-show, as before. This touches every hospital, so row-level security is lifted for
    # the owner meanwhile.
    op.execute("ALTER TABLE visits NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE visits SET status = 'no_show' WHERE status = 'skipped'")
    op.execute("ALTER TABLE visits FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_visits_status", "visits", type_="check")
    op.create_check_constraint("ck_visits_status", "visits", _OLD_STATUSES)
