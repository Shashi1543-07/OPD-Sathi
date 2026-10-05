"""Add tenant-scoped clinical data, event, and document metadata tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_domain_data"
down_revision = "0003_secret_lookup_rls"
branch_labels = None
depends_on = None


def _tenant_policy(table_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_tenant_isolation ON {table_name} "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _hospital_id() -> sa.Column:
    return sa.Column(
        "hospital_id",
        sa.Uuid(),
        sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
        nullable=False,
    )


def upgrade() -> None:
    # Composite unique keys let tenant-owned foreign keys prove that both sides
    # belong to the same hospital, in addition to each table's global UUID key.
    op.create_index("uq_users_hospital_id", "users", ["hospital_id", "id"], unique=True)
    op.create_index("uq_kiosks_hospital_id", "kiosks", ["hospital_id", "id"], unique=True)

    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("token_prefix", sa.String(8), nullable=False),
        sa.Column("name_en", sa.String(200), nullable=False),
        sa.Column("name_hi", sa.String(200), nullable=False),
        sa.Column("name_mr", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_departments_hospital_id"),
        sa.UniqueConstraint("hospital_id", "code", name="uq_departments_hospital_code"),
        sa.UniqueConstraint(
            "hospital_id", "token_prefix", name="uq_departments_hospital_prefix"
        ),
    )

    op.add_column("users", sa.Column("department_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_users_department_tenant",
        "users",
        "departments",
        ["hospital_id", "department_id"],
        ["hospital_id", "id"],
    )

    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("name_enc", sa.Text(), nullable=False),
        sa.Column("phone_enc", sa.Text()),
        sa.Column("phone_hmac", sa.String(64)),
        sa.Column("abha_enc", sa.Text()),
        sa.Column("year_of_birth", sa.SmallInteger()),
        sa.Column("sex", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("preferred_language", sa.String(2), nullable=False, server_default="en"),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_patients_hospital_id"),
        sa.CheckConstraint("sex IN ('female', 'male', 'other', 'unknown')", name="ck_patients_sex"),
        sa.CheckConstraint(
            "preferred_language IN ('en', 'hi', 'mr')", name="ck_patients_language"
        ),
        sa.CheckConstraint("name_enc LIKE 'v1:%'", name="ck_patients_name_encrypted"),
        sa.CheckConstraint(
            "phone_enc IS NULL OR phone_enc LIKE 'v1:%'", name="ck_patients_phone_encrypted"
        ),
        sa.CheckConstraint(
            "abha_enc IS NULL OR abha_enc LIKE 'v1:%'", name="ck_patients_abha_encrypted"
        ),
        sa.CheckConstraint(
            "phone_hmac IS NULL OR phone_hmac ~ '^[0-9a-f]{64}$'",
            name="ck_patients_phone_hmac",
        ),
        sa.CheckConstraint(
            "year_of_birth IS NULL OR year_of_birth BETWEEN 1900 AND 2200",
            name="ck_patients_birth_year",
        ),
    )
    op.create_index(
        "uq_patients_hospital_phone_hmac",
        "patients",
        ["hospital_id", "phone_hmac"],
        unique=True,
        postgresql_where=sa.text("phone_hmac IS NOT NULL"),
    )

    op.create_table(
        "visits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="registering"),
        sa.Column("urgency", sa.String(16), nullable=False, server_default="routine"),
        sa.Column(
            "urgency_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("token_number", sa.Integer()),
        sa.Column("token_label", sa.String(24)),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("status_token_hash", sa.String(64), unique=True),
        sa.Column("language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("age_years", sa.SmallInteger()),
        sa.Column("pregnant", sa.Boolean()),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("called_at", sa.DateTime(timezone=True)),
        sa.Column("consult_started_at", sa.DateTime(timezone=True)),
        sa.Column("consult_ended_at", sa.DateTime(timezone=True)),
        sa.Column("assigned_doctor_id", sa.Uuid()),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_visits_hospital_id"),
        sa.UniqueConstraint(
            "hospital_id",
            "department_id",
            "service_date",
            "token_number",
            name="uq_visits_daily_token",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "patient_id"],
            ["patients.hospital_id", "patients.id"],
            name="fk_visits_patient_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "department_id"],
            ["departments.hospital_id", "departments.id"],
            name="fk_visits_department_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "assigned_doctor_id"],
            ["users.hospital_id", "users.id"],
            name="fk_visits_doctor_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('registering', 'awaiting_confirmation', 'queued', 'called', "
            "'in_consult', 'done', 'cancelled', 'no_show')",
            name="ck_visits_status",
        ),
        sa.CheckConstraint("urgency IN ('routine', 'priority', 'urgent')", name="ck_visits_urgency"),
        sa.CheckConstraint("language IN ('en', 'hi', 'mr')", name="ck_visits_language"),
        sa.CheckConstraint("age_years IS NULL OR age_years BETWEEN 0 AND 120", name="ck_visits_age"),
        sa.CheckConstraint("token_number IS NULL OR token_number > 0", name="ck_visits_token"),
    )
    op.create_index(
        "ix_visits_queue", "visits", ["hospital_id", "department_id", "status", "queued_at"]
    )
    op.create_index("ix_visits_hospital_date", "visits", ["hospital_id", "service_date"])

    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("text_version", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("channel", sa.String(16), nullable=False),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_consents_hospital_id"),
        sa.UniqueConstraint("hospital_id", "visit_id", name="uq_consents_visit"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_consents_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("language IN ('en', 'hi', 'mr')", name="ck_consents_language"),
        sa.CheckConstraint("channel IN ('kiosk', 'nurse')", name="ck_consents_channel"),
        sa.CheckConstraint(
            "(granted AND granted_at IS NOT NULL) OR (NOT granted AND granted_at IS NULL)",
            name="ck_consents_grant_timestamp",
        ),
    )

    op.create_table(
        "case_sheets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_by_patient", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("last_edited_by", sa.Uuid()),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_case_sheets_hospital_id"),
        sa.UniqueConstraint("hospital_id", "visit_id", name="uq_case_sheets_visit"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_case_sheets_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "last_edited_by"],
            ["users.hospital_id", "users.id"],
            name="fk_case_sheets_editor_tenant",
        ),
        sa.CheckConstraint("version >= 1", name="ck_case_sheets_version"),
        sa.CheckConstraint(
            "schema_version = '1.0'", name="ck_case_sheets_schema_version"
        ),
        sa.CheckConstraint(
            "(confirmed_by_patient AND confirmed_at IS NOT NULL) OR "
            "(NOT confirmed_by_patient AND confirmed_at IS NULL)",
            name="ck_case_sheets_confirmation_timestamp",
        ),
    )

    op.create_table(
        "interview_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(100), nullable=False),
        sa.Column("input_mode", sa.String(16), nullable=False),
        sa.Column("text_original", sa.Text()),
        sa.Column("text_english", sa.Text()),
        sa.Column("stt_provider", sa.String(64)),
        sa.Column("stt_confidence", sa.Float()),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_interview_turns_hospital_id"),
        sa.UniqueConstraint("hospital_id", "visit_id", "turn_index", name="uq_interview_turns_order"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_interview_turns_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("turn_index >= 0", name="ck_interview_turns_index"),
        sa.CheckConstraint(
            "input_mode IN ('voice', 'touch', 'text', 'nurse')", name="ck_interview_turns_mode"
        ),
        sa.CheckConstraint(
            "stt_confidence IS NULL OR stt_confidence BETWEEN 0 AND 1",
            name="ck_interview_turns_confidence",
        ),
    )
    op.create_index("ix_interview_turns_visit_order", "interview_turns", ["visit_id", "turn_index"])

    op.create_table(
        "red_flag_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("rule_id", sa.String(100)),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("evidence", sa.Text()),
        sa.Column("acknowledged_by", sa.Uuid()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_red_flag_events_hospital_id"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_red_flag_events_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id", "acknowledged_by"],
            ["users.hospital_id", "users.id"],
            name="fk_red_flag_events_user_tenant",
        ),
        sa.CheckConstraint("source IN ('tile', 'rule', 'llm', 'nurse')", name="ck_red_flag_source"),
        sa.CheckConstraint("level IN ('priority', 'urgent')", name="ck_red_flag_level"),
    )
    op.create_index("ix_red_flag_visit_created", "red_flag_events", ["hospital_id", "visit_id", "created_at"])

    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_outbox_hospital_id"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
    )
    op.create_index("ix_outbox_pending", "outbox", ["hospital_id", "created_at"], postgresql_where=sa.text("processed_at IS NULL"))

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("template", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("provider_message_id", sa.String(200)),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_notifications_hospital_id"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_notifications_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("channel IN ('sms', 'whatsapp', 'console')", name="ck_notifications_channel"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_notifications_status"),
    )
    op.create_index("ix_notifications_visit", "notifications", ["hospital_id", "visit_id", "created_at"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _hospital_id(),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("mime", sa.String(128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("ocr_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("extracted", postgresql.JSONB()),
        _created_at(),
        sa.UniqueConstraint("hospital_id", "id", name="uq_documents_hospital_id"),
        sa.UniqueConstraint("hospital_id", "object_key", name="uq_documents_object_key"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "visit_id"],
            ["visits.hospital_id", "visits.id"],
            name="fk_documents_visit_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("size >= 0", name="ck_documents_size"),
        sa.CheckConstraint("ocr_status IN ('pending', 'complete', 'failed')", name="ck_documents_ocr_status"),
    )
    op.create_index("ix_documents_visit", "documents", ["hospital_id", "visit_id", "created_at"])

    tenant_tables = (
        "departments",
        "patients",
        "visits",
        "consents",
        "case_sheets",
        "interview_turns",
        "red_flag_events",
        "outbox",
        "notifications",
        "documents",
    )
    for table_name in tenant_tables:
        _tenant_policy(table_name)

    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT, UPDATE ON departments, patients, visits, consents, case_sheets, "
        "interview_turns, red_flag_events, outbox, notifications, documents TO osa_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    tenant_tables = (
        "documents",
        "notifications",
        "outbox",
        "red_flag_events",
        "interview_turns",
        "case_sheets",
        "consents",
        "visits",
        "patients",
        "departments",
    )
    for table_name in tenant_tables:
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
    op.drop_table("documents")
    op.drop_table("notifications")
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_red_flag_visit_created", table_name="red_flag_events")
    op.drop_table("red_flag_events")
    op.drop_index("ix_interview_turns_visit_order", table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_table("case_sheets")
    op.drop_table("consents")
    op.drop_index("ix_visits_hospital_date", table_name="visits")
    op.drop_index("ix_visits_queue", table_name="visits")
    op.drop_table("visits")
    op.drop_index("uq_patients_hospital_phone_hmac", table_name="patients")
    op.drop_table("patients")
    op.drop_constraint("fk_users_department_tenant", "users", type_="foreignkey")
    op.drop_column("users", "department_id")
    op.drop_table("departments")
    op.drop_index("uq_kiosks_hospital_id", table_name="kiosks")
    op.drop_index("uq_users_hospital_id", table_name="users")
