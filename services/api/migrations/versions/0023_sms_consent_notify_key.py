"""Queue messages: a recorded SMS consent, and a phone copy only the worker can read.

SMS becomes a consent purpose of its own (``sms``), recorded like ``care`` and ``ai`` with the
version and hash of the sentence the patient agreed to. Until now the kiosk ticked the opt-in
by itself when a phone number was typed, so no existing opt-in was a clear, affirmative act:
every one is cleared here and pending messages are cancelled.

The worker sends the messages but must not hold the patient-data key. At check-in the API now
also encrypts an opted-in phone number with ``NOTIFY_KEY`` into ``visits.notify_phone_enc``
(bound to the visit), and the worker reads only that. Its grant on ``patients.phone_enc`` and
``patients.phone_hmac`` is revoked: it can no longer read any patient's stored phone number.
The copy is for today's messages only; the worker's day close-out clears it, and it may only
clear those two columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_sms_consent_notify_key"
down_revision = "0022_documents"
branch_labels = None
depends_on = None

_WORKER_GRANTS = (
    "GRANT SELECT (notify_phone_enc, notify_phone_hmac), "
    "UPDATE (notify_phone_enc, notify_phone_hmac) ON visits TO osa_worker; "
    "GRANT SELECT (id, hospital_id, visit_id, purpose, granted, withdrawn_at) "
    "ON consents TO osa_worker; "
    "REVOKE SELECT (phone_enc, phone_hmac) ON patients FROM osa_worker; "
)
_WORKER_REVOKES = (
    "REVOKE SELECT (notify_phone_enc, notify_phone_hmac), "
    "UPDATE (notify_phone_enc, notify_phone_hmac) ON visits FROM osa_worker; "
    "REVOKE SELECT (id, hospital_id, visit_id, purpose, granted, withdrawn_at) "
    "ON consents FROM osa_worker; "
    "GRANT SELECT (phone_enc, phone_hmac) ON patients TO osa_worker; "
)


def upgrade() -> None:
    op.add_column("visits", sa.Column("notify_phone_enc", sa.Text()))
    op.add_column("visits", sa.Column("notify_phone_hmac", sa.String(64)))
    op.create_check_constraint(
        "ck_visits_notify_phone_opt_in",
        "visits",
        "sms_opt_in OR (notify_phone_enc IS NULL AND notify_phone_hmac IS NULL)",
    )
    op.drop_constraint("ck_consents_purpose", "consents", type_="check")
    op.create_check_constraint(
        "ck_consents_purpose", "consents", "purpose IN ('care', 'ai', 'sms')"
    )
    # Opt-ins made by the self-ticking box (F2) were never the patient's own act. This touches
    # every hospital, so row-level security is lifted for the owner meanwhile.
    for table in ("visits", "notifications"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE notifications SET status = 'failed', last_error_code = 'consent_reset', "
        "next_attempt_at = NULL, updated_at = now() WHERE status IN ('pending', 'sending')"
    )
    op.execute("UPDATE visits SET sms_opt_in = false, sms_opt_in_at = NULL WHERE sms_opt_in")
    for table in ("visits", "notifications"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        f"{_WORKER_GRANTS}END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        f"{_WORKER_REVOKES}END IF; END $$;"
    )
    op.execute("ALTER TABLE consents NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM consents WHERE purpose = 'sms'")
    op.execute("ALTER TABLE consents FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_consents_purpose", "consents", type_="check")
    op.create_check_constraint("ck_consents_purpose", "consents", "purpose IN ('care', 'ai')")
    op.drop_constraint("ck_visits_notify_phone_opt_in", "visits", type_="check")
    op.drop_column("visits", "notify_phone_hmac")
    op.drop_column("visits", "notify_phone_enc")
