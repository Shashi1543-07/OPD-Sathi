"""Patients' encrypted columns accept ciphertext from any key in the ring, not only key 1.

Phase 7, task 7.3.6. The checks from 0004 insisted every name, phone and ABHA value start with
``v1:`` (the first data key), so after a key rotation the first re-sealed name would have
been refused. They now accept ``v1:`` or ``v2:<key id>:`` and still refuse anything that is
not ciphertext. Downgrading is refused while any value is sealed with a rotated key: the
previous release could not read it.
"""

from alembic import op

revision = "0026_versioned_ciphertext"
down_revision = "0025_consent_guard"
branch_labels = None
depends_on = None

_SEALED = r"^v(1|2:[A-Za-z0-9_-]{1,16}):"
_COLUMNS = (
    ("name_enc", "ck_patients_name_encrypted", False),
    ("phone_enc", "ck_patients_phone_encrypted", True),
    ("abha_enc", "ck_patients_abha_encrypted", True),
)


def _check(column: str, nullable: bool, pattern: str) -> str:
    condition = f"{column} ~ '{pattern}'"
    return f"{column} IS NULL OR {condition}" if nullable else condition


def upgrade() -> None:
    for column, name, nullable in _COLUMNS:
        op.drop_constraint(name, "patients", type_="check")
        op.create_check_constraint(name, "patients", _check(column, nullable, _SEALED))


def downgrade() -> None:
    for column, name, nullable in _COLUMNS:
        op.drop_constraint(name, "patients", type_="check")
        condition = f"{column} LIKE 'v1:%'"
        op.create_check_constraint(
            name, "patients", f"{column} IS NULL OR {condition}" if nullable else condition
        )
