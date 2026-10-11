"""Kiosk pairing lifecycle, archive (delete) for admin records, and visit kiosk attribution.

Kiosks: a kiosk record is the hospital's long-lived identity for a registration point; the
device that sits there is *paired* to it. An administrator can issue a fresh one-time
activation code for the same record at any time (codes now expire), so replacing or
re-pairing a device keeps the kiosk's id and history. A paired device renews its access
token with its device secret, looked up before tenant scope is known like other secrets.

Delete: departments, staff and kiosks are referenced by clinical history and the audit log,
so "delete" archives the row (``deleted_at``) instead of removing it. Archived rows are
inactive, hidden from administration, and free their email / code / prefix for reuse.

Visits record which kiosk registered them, so per-kiosk activity survives re-pairing.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_kiosk_lifecycle_and_archive"
down_revision = "0015_report_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kiosks", sa.Column("activation_code_expires_at", sa.DateTime(timezone=True)))
    op.add_column("kiosks", sa.Column("paired_at", sa.DateTime(timezone=True)))
    op.add_column("kiosks", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("departments", sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.create_check_constraint(
        "ck_kiosks_archived_inactive", "kiosks", "deleted_at IS NULL OR NOT is_active"
    )
    op.create_check_constraint(
        "ck_users_archived_inactive", "users", "deleted_at IS NULL OR NOT is_active"
    )
    op.create_check_constraint(
        "ck_departments_archived_inactive", "departments", "deleted_at IS NULL OR NOT is_active"
    )

    op.execute(
        "CREATE POLICY kiosks_device_secret_lookup ON kiosks FOR SELECT "
        "USING (secret_hash = current_setting('app.kiosk_secret_hash', true))"
    )
    op.create_index(
        "uq_kiosks_secret_hash",
        "kiosks",
        ["secret_hash"],
        unique=True,
        postgresql_where=sa.text("secret_hash IS NOT NULL"),
    )

    # Archived records give their natural keys back for reuse.
    op.drop_index("uq_users_hospital_email", table_name="users")
    op.create_index(
        "uq_users_hospital_email",
        "users",
        ["hospital_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_constraint("uq_departments_hospital_code", "departments", type_="unique")
    op.drop_constraint("uq_departments_hospital_prefix", "departments", type_="unique")
    op.create_index(
        "uq_departments_hospital_code",
        "departments",
        ["hospital_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_departments_hospital_prefix",
        "departments",
        ["hospital_id", "token_prefix"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.add_column("visits", sa.Column("kiosk_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_visits_kiosk_tenant",
        "visits",
        "kiosks",
        ["hospital_id", "kiosk_id"],
        ["hospital_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_visits_kiosk_date",
        "visits",
        ["hospital_id", "kiosk_id", "service_date"],
        postgresql_where=sa.text("kiosk_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_visits_kiosk_date", table_name="visits")
    op.drop_constraint("fk_visits_kiosk_tenant", "visits", type_="foreignkey")
    op.drop_column("visits", "kiosk_id")

    op.drop_index("uq_departments_hospital_prefix", table_name="departments")
    op.drop_index("uq_departments_hospital_code", table_name="departments")
    op.create_unique_constraint(
        "uq_departments_hospital_prefix", "departments", ["hospital_id", "token_prefix"]
    )
    op.create_unique_constraint(
        "uq_departments_hospital_code", "departments", ["hospital_id", "code"]
    )
    op.drop_index("uq_users_hospital_email", table_name="users")
    op.create_index(
        "uq_users_hospital_email", "users", ["hospital_id", sa.text("lower(email)")], unique=True
    )

    op.drop_index("uq_kiosks_secret_hash", table_name="kiosks")
    op.execute("DROP POLICY kiosks_device_secret_lookup ON kiosks")
    op.drop_constraint("ck_departments_archived_inactive", "departments", type_="check")
    op.drop_constraint("ck_users_archived_inactive", "users", type_="check")
    op.drop_constraint("ck_kiosks_archived_inactive", "kiosks", type_="check")
    op.drop_column("departments", "deleted_at")
    op.drop_column("users", "deleted_at")
    op.drop_column("kiosks", "deleted_at")
    op.drop_column("kiosks", "paired_at")
    op.drop_column("kiosks", "activation_code_expires_at")
