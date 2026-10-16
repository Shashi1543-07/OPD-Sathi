"""Returning patients: an OPD card, shared phone numbers, and follow-up visits.

- ``patients.mrn``: the hospital registration number on the patient's OPD card (``26-000123``),
  unique within a hospital and assigned when a first visit receives its token, so abandoned
  check-ins never use one up. ``patient_number_counters`` holds the per-hospital, per-year
  sequence behind it.
- ``patients.card_version``: the card PIN is derived from the application HMAC key, the
  patient and this version, so it can be shown again, and replaced when a card is lost,
  without ever storing it.
- Phone numbers stop being unique: families share one phone, and a returning patient gives
  the same number again (both were refused before). A phone number is only a lookup hint for
  staff; it never identifies a patient on its own.
- ``visits.patient_link``: how a visit was joined to an existing patient ("card" or "staff");
  NULL for a first visit. ``visits.previous_visit_id``: the earlier visit a follow-up
  continues; the earlier visit itself is never modified.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_patient_identity"
down_revision = "0017_mobile_checkin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_patients_hospital_phone_hmac", table_name="patients")
    op.create_index(
        "ix_patients_hospital_phone_hmac",
        "patients",
        ["hospital_id", "phone_hmac"],
        postgresql_where=sa.text("phone_hmac IS NOT NULL"),
    )
    op.add_column("patients", sa.Column("mrn", sa.String(24)))
    op.add_column(
        "patients",
        sa.Column("card_version", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.create_check_constraint("ck_patients_mrn", "patients", "mrn ~ '^[0-9]{2}-[0-9]{6,9}$'")
    op.create_check_constraint("ck_patients_card_version", "patients", "card_version >= 1")
    op.create_index(
        "uq_patients_hospital_mrn",
        "patients",
        ["hospital_id", "mrn"],
        unique=True,
        postgresql_where=sa.text("mrn IS NOT NULL"),
    )

    op.create_table(
        "patient_number_counters",
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            # Bookkeeping, not clinical data: it goes with its hospital.
            sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("hospital_id", "year", name="pk_patient_number_counters"),
        sa.CheckConstraint("last_value >= 1", name="ck_patient_number_counters_value"),
    )
    op.execute("ALTER TABLE patient_number_counters ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE patient_number_counters FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY patient_number_counters_tenant_isolation ON patient_number_counters "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT, UPDATE ON patient_number_counters TO osa_app; "
        "END IF; END $$;"
    )

    op.add_column("visits", sa.Column("patient_link", sa.String(16)))
    op.create_check_constraint(
        "ck_visits_patient_link", "visits", "patient_link IS NULL OR patient_link IN ('card', 'staff')"
    )
    op.add_column("visits", sa.Column("previous_visit_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_visits_previous_visit_tenant",
        "visits",
        "visits",
        ["hospital_id", "previous_visit_id"],
        ["hospital_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_visits_previous_not_self", "visits", "previous_visit_id IS NULL OR previous_visit_id <> id"
    )
    op.create_index(
        "ix_visits_patient_history", "visits", ["hospital_id", "patient_id", "registered_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_visits_patient_history", table_name="visits")
    op.drop_constraint("ck_visits_previous_not_self", "visits", type_="check")
    op.drop_constraint("fk_visits_previous_visit_tenant", "visits", type_="foreignkey")
    op.drop_column("visits", "previous_visit_id")
    op.drop_constraint("ck_visits_patient_link", "visits", type_="check")
    op.drop_column("visits", "patient_link")
    op.drop_table("patient_number_counters")
    op.drop_index("uq_patients_hospital_mrn", table_name="patients")
    op.drop_constraint("ck_patients_card_version", "patients", type_="check")
    op.drop_constraint("ck_patients_mrn", "patients", type_="check")
    op.drop_column("patients", "card_version")
    op.drop_column("patients", "mrn")
    op.drop_index("ix_patients_hospital_phone_hmac", table_name="patients")
    # The older schema allows one patient per phone number. Shared numbers stay on the
    # earliest patient; the others lose the number (it can be entered again at a visit).
    # This touches every hospital, so row-level security is lifted for the owner meanwhile.
    op.execute("ALTER TABLE patients NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE patients p SET phone_hmac = NULL, phone_enc = NULL WHERE phone_hmac IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM patients q WHERE q.hospital_id = p.hospital_id "
        "AND q.phone_hmac = p.phone_hmac AND (q.created_at, q.id) < (p.created_at, p.id))"
    )
    op.execute("ALTER TABLE patients FORCE ROW LEVEL SECURITY")
    op.create_index(
        "uq_patients_hospital_phone_hmac",
        "patients",
        ["hospital_id", "phone_hmac"],
        unique=True,
        postgresql_where=sa.text("phone_hmac IS NOT NULL"),
    )
