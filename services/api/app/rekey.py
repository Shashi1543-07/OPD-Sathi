"""Re-seal encrypted records with the current data key, so an old key can be retired.

Phase 7, task 7.3.6; the procedure is docs/runbooks/key-rotation.md. After a rotation the
API writes new records with the new key and reads old ones through the key ring
(APP_DATA_PREVIOUS_KEYS). This job walks every encrypted field, hospital by hospital, in
batches, and re-encrypts each value still sealed with an older key. Each value keeps its
associated data (the row it is bound to). A dry run (the default) only counts.

Usage: python -m app.cli reencrypt [--apply] [--batch 500]
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import LargeBinary, Text, column, select, table, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.types import TypeEngine

from app.core.crypto import (
    KeyRing,
    decrypt_bytes,
    decrypt_field,
    encrypt_bytes,
    encrypt_field,
    key_id_of,
)


@dataclass(frozen=True)
class EncryptedField:
    table: str
    key_column: str  # the column named in the associated data
    value_column: str
    context: str  # associated data prefix: "<context>:<key>"
    kind: str  # "text", "json" (a {"ciphertext": ...} object) or "bytes"


FIELDS = (
    EncryptedField("patients", "id", "name_enc", "patients:name_enc", "text"),
    EncryptedField("patients", "id", "phone_enc", "patients:phone_enc", "text"),
    EncryptedField("patients", "id", "abha_enc", "patients:abha_enc", "text"),
    EncryptedField(
        "interview_turns", "id", "text_original", "interview_turns:text_original", "text"
    ),
    EncryptedField("interview_turns", "id", "text_english", "interview_turns:text_english", "text"),
    EncryptedField("users", "id", "mfa_secret_enc", "users:mfa_secret_enc", "text"),
    EncryptedField("case_sheets", "visit_id", "data", "case_sheets:data", "json"),
    EncryptedField("documents", "id", "extracted", "documents:extracted", "json"),
    EncryptedField("document_contents", "document_id", "ciphertext", "documents:content", "bytes"),
)


@dataclass
class FieldReport:
    checked: int = 0
    resealed: int = 0
    unreadable: int = 0
    # Values under the current key that were decrypted to prove them readable (verify mode).
    verified: int = 0


def _sealed(value: Any, kind: str) -> str | bytes | None:
    if value is None:
        return None
    if kind == "json":
        stored = json.loads(value) if isinstance(value, str) else value
        ciphertext = stored.get("ciphertext") if isinstance(stored, dict) else None
        return ciphertext if isinstance(ciphertext, str) else None
    if kind == "bytes":
        return bytes(value)
    return str(value)


def _reseal(sealed: str | bytes, field: EncryptedField, row_key: Any, ring: KeyRing) -> Any:
    context = f"{field.context}:{row_key}"
    if isinstance(sealed, bytes):
        plain_bytes = decrypt_bytes(sealed, key=ring, associated_data=context)
        return encrypt_bytes(plain_bytes, key=ring, associated_data=context)
    plain = decrypt_field(sealed, key=ring, associated_data=context)
    fresh = encrypt_field(plain, key=ring, associated_data=context)
    return {"ciphertext": fresh} if field.kind == "json" else fresh


async def reencrypt_hospital(
    connection: AsyncConnection,
    *,
    hospital_id: UUID,
    ring: KeyRing,
    apply: bool,
    batch: int = 500,
    progress: Callable[[str], None] = lambda _message: None,
    verify: bool = False,
) -> dict[str, FieldReport]:
    """Re-seal one hospital's records (the caller has set its tenant scope).

    With ``verify`` (restore drills, task 7.7.5) every value is decrypted, including those
    already under the current key, and nothing is written.
    """
    apply = apply and not verify
    reports: dict[str, FieldReport] = {}
    for field in FIELDS:
        report = reports.setdefault(f"{field.table}.{field.value_column}", FieldReport())
        value_type: TypeEngine[Any] = (
            JSONB() if field.kind == "json" else LargeBinary() if field.kind == "bytes" else Text()
        )
        relation = table(
            field.table,
            column("hospital_id"),
            column(field.key_column),
            column(field.value_column, value_type),
        )
        key_col, value_col = relation.c[field.key_column], relation.c[field.value_column]
        after: Any = None
        while True:
            query = (
                select(key_col, value_col)
                .where(relation.c.hospital_id == hospital_id, value_col.is_not(None))
                .order_by(key_col)
                .limit(batch)
            )
            if after is not None:
                query = query.where(key_col > after)
            rows = (await connection.execute(query)).all()
            if not rows:
                break
            for row_key, value in rows:
                report.checked += 1
                sealed = _sealed(value, field.kind)
                if sealed is None:
                    continue
                try:
                    current = key_id_of(sealed) == ring.current_id
                    if current and not verify:
                        continue
                    fresh = _reseal(sealed, field, row_key, ring)
                except ValueError:
                    report.unreadable += 1
                    continue
                if current:
                    report.verified += 1
                    continue
                report.resealed += 1
                if apply:
                    statement = (
                        update(relation)
                        .where(relation.c.hospital_id == hospital_id, key_col == row_key)
                        .values({field.value_column: fresh})
                    )
                    await connection.execute(statement)
            after = rows[-1][0]
        readable = f"{report.verified} decrypted, " if verify else ""
        progress(
            f"  {field.table}.{field.value_column}: {report.checked} checked, {readable}"
            f"{report.resealed} {'re-sealed' if apply else 'to re-seal'}, "
            f"{report.unreadable} unreadable"
        )
    return reports
