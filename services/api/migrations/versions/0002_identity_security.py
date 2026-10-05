"""Add user, kiosk, refresh-token, and append-only audit foundations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_identity_security"
down_revision = "0001_hospitals"
branch_labels = None
depends_on = None


def _create_tenant_policy(table_name: str, tenant_column: str = "hospital_id") -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_tenant_isolation ON {table_name} "
        f"USING ({tenant_column} = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        f"WITH CHECK ({tenant_column} = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('admin', 'doctor', 'nurse')", name="ck_users_role"),
    )
    op.create_index(
        "uq_users_hospital_email", "users", ["hospital_id", sa.text("lower(email)")], unique=True
    )

    op.create_table(
        "kiosks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("activation_code_hash", sa.String(64)),
        sa.Column("secret_hash", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_kiosks_hospital", "kiosks", ["hospital_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "replaced_by", sa.Uuid(), sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_refresh_tokens_user_family", "refresh_tokens", ["hospital_id", "user_id", "family_id"]
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hospital_id",
            sa.Uuid(),
            sa.ForeignKey("hospitals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("ip", sa.String(45)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'kiosk', 'patient', 'system')", name="ck_audit_actor_type"
        ),
    )
    op.create_index("ix_audit_hospital_created", "audit_log", ["hospital_id", "created_at"])

    _create_tenant_policy("hospitals", "id")
    for table_name in ("users", "kiosks", "refresh_tokens", "audit_log"):
        _create_tenant_policy(table_name)

    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "REVOKE INSERT, UPDATE ON hospitals FROM osa_app; "
        "GRANT SELECT ON hospitals TO osa_app; "
        "GRANT SELECT, INSERT, UPDATE ON users, kiosks, refresh_tokens TO osa_app; "
        "GRANT SELECT, INSERT ON audit_log TO osa_app; "
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM osa_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    for table_name in ("audit_log", "refresh_tokens", "kiosks", "users", "hospitals"):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
        if table_name == "hospitals":
            op.execute("ALTER TABLE hospitals NO FORCE ROW LEVEL SECURITY")
            op.execute("ALTER TABLE hospitals DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_audit_hospital_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_refresh_tokens_user_family", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_kiosks_hospital", table_name="kiosks")
    op.drop_table("kiosks")
    op.drop_index("uq_users_hospital_email", table_name="users")
    op.drop_table("users")
    op.execute("GRANT SELECT, INSERT, UPDATE ON hospitals TO osa_app")
