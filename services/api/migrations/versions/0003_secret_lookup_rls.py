"""Permit exact high-entropy secret lookup before tenant scope is known."""

from alembic import op

revision = "0003_secret_lookup_rls"
down_revision = "0002_identity_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE POLICY kiosks_activation_lookup ON kiosks FOR SELECT "
        "USING (activation_code_hash = current_setting('app.kiosk_activation_hash', true))"
    )
    op.execute(
        "CREATE POLICY refresh_tokens_secret_lookup ON refresh_tokens FOR SELECT "
        "USING (token_hash = current_setting('app.refresh_token_hash', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY refresh_tokens_secret_lookup ON refresh_tokens")
    op.execute("DROP POLICY kiosks_activation_lookup ON kiosks")
