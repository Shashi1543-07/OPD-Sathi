"""Kiosk device secrets rotate on a schedule, without anyone pairing the kiosk again.

Phase 7, task 7.3.9. When a kiosk renews its access token with a secret older than the
rotation interval (KIOSK_SECRET_ROTATION_DAYS), the answer carries a new secret. The previous
one keeps working until the device has used the new one once, so a renewal lost to a flaky
hospital network never strands the kiosk. ``secret_rotated_at`` starts at the pairing time.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_kiosk_secret_rotation"
down_revision = "0026_versioned_ciphertext"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kiosks", sa.Column("previous_secret_hash", sa.String(64)))
    op.add_column("kiosks", sa.Column("secret_rotated_at", sa.DateTime(timezone=True)))
    op.execute("ALTER TABLE kiosks NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE kiosks SET secret_rotated_at = coalesce(paired_at, created_at)")
    op.execute("ALTER TABLE kiosks FORCE ROW LEVEL SECURITY")
    op.create_index(
        "uq_kiosks_previous_secret_hash",
        "kiosks",
        ["previous_secret_hash"],
        unique=True,
        postgresql_where=sa.text("previous_secret_hash IS NOT NULL"),
    )
    op.execute("DROP POLICY kiosks_device_secret_lookup ON kiosks")
    op.execute(
        "CREATE POLICY kiosks_device_secret_lookup ON kiosks FOR SELECT USING ("
        "secret_hash = current_setting('app.kiosk_secret_hash', true) "
        "OR previous_secret_hash = current_setting('app.kiosk_secret_hash', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY kiosks_device_secret_lookup ON kiosks")
    op.execute(
        "CREATE POLICY kiosks_device_secret_lookup ON kiosks FOR SELECT "
        "USING (secret_hash = current_setting('app.kiosk_secret_hash', true))"
    )
    op.drop_index("uq_kiosks_previous_secret_hash", table_name="kiosks")
    op.drop_column("kiosks", "secret_rotated_at")
    op.drop_column("kiosks", "previous_secret_hash")
