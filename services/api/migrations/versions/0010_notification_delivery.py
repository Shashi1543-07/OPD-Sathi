"""Add idempotency, retry and recipient-rate-limit fields for notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0010_notification_delivery"
down_revision = "0009_notification_opt_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(180)))
    op.add_column("notifications", sa.Column("recipient_hmac", sa.String(64)))
    op.add_column(
        "notifications",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("notifications", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("notifications", sa.Column("last_error_code", sa.String(64)))
    op.add_column(
        "notifications",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint(
        "uq_notifications_hospital_dedupe", "notifications", ["hospital_id", "dedupe_key"]
    )
    op.create_index(
        "ix_notifications_delivery_pending",
        "notifications",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_notifications_recipient_hourly",
        "notifications",
        ["hospital_id", "recipient_hmac", "created_at"],
        postgresql_where=sa.text("recipient_hmac IS NOT NULL"),
    )
    op.drop_constraint("ck_notifications_status", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_status",
        "notifications",
        "status IN ('pending', 'sending', 'sent', 'failed')",
    )
    op.create_check_constraint(
        "ck_notifications_attempt_count", "notifications", "attempt_count >= 0"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "GRANT SELECT (id, hospital_id, visit_id, channel, template, status, "
        "provider_message_id, created_at, dedupe_key, recipient_hmac, attempt_count, "
        "next_attempt_at, last_error_code, updated_at), UPDATE (status, provider_message_id, "
        "attempt_count, next_attempt_at, last_error_code, updated_at) "
        "ON notifications TO osa_worker; "
        "GRANT INSERT (id, hospital_id, visit_id, channel, template, status, dedupe_key, "
        "recipient_hmac, next_attempt_at, last_error_code) ON notifications TO osa_worker; "
        "GRANT SELECT (id, hospital_id, patient_id, department_id, status, urgency, service_date, "
        "token_number, token_label, age_years, pregnant, queued_at, created_at, "
        "consult_ended_at, assigned_doctor_id, language, sms_opt_in, sms_opt_in_at) "
        "ON visits TO osa_worker; "
        "GRANT SELECT (id, hospital_id, phone_enc, phone_hmac) ON patients TO osa_worker; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "REVOKE SELECT (id, hospital_id, visit_id, channel, template, status, "
        "provider_message_id, created_at, dedupe_key, recipient_hmac, attempt_count, "
        "next_attempt_at, last_error_code, updated_at), UPDATE (status, provider_message_id, "
        "attempt_count, next_attempt_at, last_error_code, updated_at) "
        "ON notifications FROM osa_worker; "
        "REVOKE INSERT (id, hospital_id, visit_id, channel, template, status, dedupe_key, "
        "recipient_hmac, next_attempt_at, last_error_code) ON notifications FROM osa_worker; "
        "REVOKE SELECT (id, hospital_id, patient_id, department_id, status, urgency, service_date, "
        "token_number, token_label, age_years, pregnant, queued_at, created_at, "
        "consult_ended_at, assigned_doctor_id, language, sms_opt_in, sms_opt_in_at) "
        "ON visits FROM osa_worker; "
        "REVOKE SELECT (id, hospital_id, phone_enc, phone_hmac) ON patients FROM osa_worker; "
        "END IF; END $$;"
    )
    op.drop_constraint("ck_notifications_attempt_count", "notifications", type_="check")
    op.drop_constraint("ck_notifications_status", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_status", "notifications", "status IN ('pending', 'sent', 'failed')"
    )
    op.drop_index("ix_notifications_recipient_hourly", table_name="notifications")
    op.drop_index("ix_notifications_delivery_pending", table_name="notifications")
    op.drop_constraint("uq_notifications_hospital_dedupe", "notifications", type_="unique")
    op.drop_column("notifications", "updated_at")
    op.drop_column("notifications", "last_error_code")
    op.drop_column("notifications", "next_attempt_at")
    op.drop_column("notifications", "attempt_count")
    op.drop_column("notifications", "recipient_hmac")
    op.drop_column("notifications", "dedupe_key")
