"""Consent rows: only the answer and its withdrawal can change, and a withdrawal sticks.

Phase 7, task 7.3.8. The application role could update every column of ``consents``. It now
may change only the answer fields (language, notice version and hash, granted, time,
channel) and the withdrawal fields, never which visit, hospital or purpose a consent belongs
to. A trigger refuses to undo a withdrawal once the visit has left registration: until then a
patient may still change their answer, afterwards "stopped" stays stopped.
"""

from alembic import op

revision = "0025_consent_guard"
down_revision = "0024_staff_account_security"
branch_labels = None
depends_on = None

_ANSWER_COLUMNS = (
    "language, text_version, text_sha256, granted, granted_at, channel, withdrawn_at, withdrawn_by"
)


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "REVOKE UPDATE ON consents FROM osa_app; "
        f"GRANT UPDATE ({_ANSWER_COLUMNS}) ON consents TO osa_app; "
        "END IF; END $$;"
    )
    op.execute(
        """
        CREATE FUNCTION osa_consent_withdrawal_sticks() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.withdrawn_at IS NOT NULL AND NEW.withdrawn_at IS NULL AND EXISTS (
                SELECT 1 FROM visits
                WHERE hospital_id = OLD.hospital_id AND id = OLD.visit_id
                AND status <> 'registering'
            ) THEN
                RAISE EXCEPTION 'a withdrawn consent cannot be restored after registration'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER consents_withdrawal_sticks BEFORE UPDATE ON consents "
        "FOR EACH ROW EXECUTE FUNCTION osa_consent_withdrawal_sticks()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS consents_withdrawal_sticks ON consents")
    op.execute("DROP FUNCTION IF EXISTS osa_consent_withdrawal_sticks()")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        f"REVOKE UPDATE ({_ANSWER_COLUMNS}) ON consents FROM osa_app; "
        "GRANT UPDATE ON consents TO osa_app; "
        "END IF; END $$;"
    )
