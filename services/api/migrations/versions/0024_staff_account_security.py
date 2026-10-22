"""Staff accounts: two-step sign-in, password reset and change, and visible sessions.

- ``users.mfa_secret_enc``: the authenticator secret, encrypted with the data key and bound to
  the user. ``mfa_enabled_at`` is set once the first code is confirmed. ``mfa_last_step`` is
  the last accepted time step, so a code cannot be used twice. ``mfa_recovery_hashes`` holds
  the Argon2id hashes of ten single-use recovery codes; a used one becomes an empty string.
- ``users.must_change_password``: set by an administrator's reset; the person can do nothing
  but choose a new password at their next sign-in. ``password_changed_at`` records when.
- ``refresh_tokens.device``: a short label such as "Chrome on Windows" so a person can tell
  their sign-ins apart when ending them. ``last_used_at`` is when the session last renewed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_staff_account_security"
down_revision = "0023_sms_consent_notify_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret_enc", sa.Text()))
    op.add_column("users", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("mfa_last_step", sa.BigInteger()))
    op.add_column(
        "users",
        sa.Column(
            "mfa_recovery_hashes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_users_mfa_enabled_has_secret",
        "users",
        "mfa_enabled_at IS NULL OR mfa_secret_enc IS NOT NULL",
    )
    op.add_column("refresh_tokens", sa.Column("device", sa.String(80)))
    op.add_column("refresh_tokens", sa.Column("last_used_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "last_used_at")
    op.drop_column("refresh_tokens", "device")
    op.drop_constraint("ck_users_mfa_enabled_has_secret", "users", type_="check")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "mfa_recovery_hashes")
    op.drop_column("users", "mfa_last_step")
    op.drop_column("users", "mfa_enabled_at")
    op.drop_column("users", "mfa_secret_enc")
