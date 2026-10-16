"""Phone check-in points: patients scan a QR and answer the interview on their own phone.

A check-in point is a kiosk record of kind ``mobile``. It is never a paired device: it has no
activation code or device secret, so it can never obtain a kiosk token. Its check-in code is
derived from the application HMAC key, the point and a version number (so an administrator
can show the QR again or replace it), and stored only as a SHA-256 digest for lookup before
the tenant is known, like other secrets. Visits started from it keep pointing at it, so
per-point activity works the same as for kiosks.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_mobile_checkin"
down_revision = "0016_kiosk_lifecycle_and_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kiosks", sa.Column("kind", sa.String(16), nullable=False, server_default="device")
    )
    op.add_column("kiosks", sa.Column("checkin_code_hash", sa.String(64)))
    op.add_column(
        "kiosks",
        sa.Column("checkin_code_version", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.create_check_constraint("ck_kiosks_kind", "kiosks", "kind IN ('device', 'mobile')")
    op.create_check_constraint(
        "ck_kiosks_kind_credentials",
        "kiosks",
        "(kind = 'device' AND checkin_code_hash IS NULL) OR "
        "(kind = 'mobile' AND secret_hash IS NULL AND activation_code_hash IS NULL)",
    )
    op.create_check_constraint(
        "ck_kiosks_checkin_code_hash", "kiosks", "checkin_code_hash ~ '^[0-9a-f]{64}$'"
    )
    op.create_check_constraint(
        "ck_kiosks_checkin_code_version", "kiosks", "checkin_code_version >= 1"
    )
    op.create_index(
        "uq_kiosks_checkin_code_hash",
        "kiosks",
        ["checkin_code_hash"],
        unique=True,
        postgresql_where=sa.text("checkin_code_hash IS NOT NULL"),
    )
    op.execute(
        "CREATE POLICY kiosks_checkin_code_lookup ON kiosks FOR SELECT "
        "USING (checkin_code_hash = current_setting('app.kiosk_checkin_hash', true))"
    )
    # Consent given on a patient's own phone is recorded as such.
    op.drop_constraint("ck_consents_channel", "consents", type_="check")
    op.create_check_constraint(
        "ck_consents_channel", "consents", "channel IN ('kiosk', 'nurse', 'mobile')"
    )


def downgrade() -> None:
    # The older schema has no phone channel: those consents become kiosk consents. This
    # touches every hospital, so row-level security is lifted for the owner meanwhile.
    op.execute("ALTER TABLE consents NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE consents SET channel = 'kiosk' WHERE channel = 'mobile'")
    op.execute("ALTER TABLE consents FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_consents_channel", "consents", type_="check")
    op.create_check_constraint("ck_consents_channel", "consents", "channel IN ('kiosk', 'nurse')")
    op.execute("DROP POLICY kiosks_checkin_code_lookup ON kiosks")
    op.drop_index("uq_kiosks_checkin_code_hash", table_name="kiosks")
    op.drop_constraint("ck_kiosks_checkin_code_version", "kiosks", type_="check")
    op.drop_constraint("ck_kiosks_checkin_code_hash", "kiosks", type_="check")
    op.drop_constraint("ck_kiosks_kind_credentials", "kiosks", type_="check")
    op.drop_constraint("ck_kiosks_kind", "kiosks", type_="check")
    op.drop_column("kiosks", "checkin_code_version")
    op.drop_column("kiosks", "checkin_code_hash")
    op.drop_column("kiosks", "kind")
