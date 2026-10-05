"""Add queue read-path indexes and high-entropy public status-token lookup."""

import sqlalchemy as sa
from alembic import op

revision = "0005_queue"
down_revision = "0004_domain_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE POLICY visits_status_token_lookup ON visits FOR SELECT "
        "USING (status_token_hash = current_setting('app.status_token_hash', true))"
    )
    op.create_index(
        "ix_visits_queue_rebuild",
        "visits",
        ["hospital_id", "department_id", "service_date", "queued_at", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_visits_completed_consults",
        "visits",
        ["hospital_id", "department_id", "consult_ended_at"],
        postgresql_where=sa.text("status = 'done' AND consult_ended_at IS NOT NULL"),
    )
    op.create_index(
        "ix_outbox_queue_pending",
        "outbox",
        ["created_at", "id"],
        postgresql_where=sa.text("processed_at IS NULL AND topic = 'queue.changed'"),
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "GRANT SELECT (id, hospital_id, topic, payload, processed_at, attempts, created_at), "
        "UPDATE (processed_at, attempts) ON outbox TO osa_worker; "
        "GRANT SELECT (id, hospital_id, department_id, status, urgency, service_date, "
        "token_number, token_label, age_years, pregnant, queued_at, created_at, "
        "consult_ended_at, assigned_doctor_id) ON visits TO osa_worker; "
        "GRANT SELECT (id, hospital_id, token_prefix, code, is_active) ON departments TO osa_worker; "
        "GRANT SELECT (id, hospital_id, role, department_id, is_active) ON users TO osa_worker; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute("DROP POLICY visits_status_token_lookup ON visits")
    op.drop_index("ix_outbox_queue_pending", table_name="outbox")
    op.drop_index("ix_visits_completed_consults", table_name="visits")
    op.drop_index("ix_visits_queue_rebuild", table_name="visits")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        "REVOKE SELECT (id, hospital_id, topic, payload, processed_at, attempts, created_at), "
        "UPDATE (processed_at, attempts) ON outbox FROM osa_worker; "
        "REVOKE SELECT (id, hospital_id, department_id, status, urgency, service_date, "
        "token_number, token_label, age_years, pregnant, queued_at, created_at, "
        "consult_ended_at, assigned_doctor_id) ON visits FROM osa_worker; "
        "REVOKE SELECT (id, hospital_id, token_prefix, code, is_active) ON departments FROM osa_worker; "
        "REVOKE SELECT (id, hospital_id, role, department_id, is_active) ON users FROM osa_worker; "
        "END IF; END $$;"
    )
