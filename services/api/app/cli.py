"""Operator-only bootstrap commands for synthetic local staff and kiosks."""

import argparse
import asyncio
import getpass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.core.security import create_opaque_secret, digest_secret, hash_password
from app.db.session import set_hospital_scope


async def _create_admin(
    *,
    hospital_id: UUID | None,
    hospital_name: str | None,
    hospital_code: str | None,
    name: str,
    email: str,
    password: str,
) -> UUID:
    settings = get_settings()
    selected_hospital_id = hospital_id or uuid4()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, selected_hospital_id)
            hospital = await connection.scalar(
                text("SELECT id FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": selected_hospital_id},
            )
            if hospital is None and (hospital_name is None or hospital_code is None):
                raise SystemExit(
                    "Provide an existing --hospital-id or a new hospital name and code."
                )
            if hospital is not None and (hospital_name is not None or hospital_code is not None):
                raise SystemExit(
                    "A new hospital can only be created when --hospital-id is omitted."
                )
            if hospital is None:
                await connection.execute(
                    text("INSERT INTO hospitals (id, name, code) VALUES (:id, :name, :code)"),
                    {"id": selected_hospital_id, "name": hospital_name, "code": hospital_code},
                )
            user_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                    "VALUES (:id, :hospital_id, 'admin', :name, :email, :password_hash)"
                ),
                {
                    "id": user_id,
                    "hospital_id": selected_hospital_id,
                    "name": name,
                    "email": email.strip().lower(),
                    "password_hash": hash_password(password),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', "
                    "'bootstrap.admin.created', 'user', :user_id, 'operator-cli', "
                    '\'{"role":"admin"}\'::jsonb)'
                ),
                {"id": uuid4(), "hospital_id": selected_hospital_id, "user_id": user_id},
            )
    finally:
        await engine.dispose()
    return selected_hospital_id


async def _create_kiosk(*, hospital_id: UUID, label: str) -> str:
    settings = get_settings()
    code = create_opaque_secret()
    kiosk_id = uuid4()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            hospital = await connection.scalar(
                text("SELECT id FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            if hospital is None:
                raise SystemExit("Hospital does not exist; create the first admin for it first.")
            await connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                    "VALUES (:id, :hospital_id, :label, :activation_hash)"
                ),
                {
                    "id": kiosk_id,
                    "hospital_id": hospital_id,
                    "label": label,
                    "activation_hash": digest_secret(code),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', 'kiosk.provisioned', "
                    "'kiosk', :kiosk_id, 'operator-cli', '{}'::jsonb)"
                ),
                {"id": uuid4(), "hospital_id": hospital_id, "kiosk_id": kiosk_id},
            )
    finally:
        await engine.dispose()
    return code


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="Create an initial hospital administrator")
    admin.add_argument("--hospital-id", type=UUID)
    admin.add_argument("--hospital-name")
    admin.add_argument("--hospital-code")
    admin.add_argument("--name")
    admin.add_argument("--email")
    kiosk = commands.add_parser("create-kiosk", help="Create a one-time kiosk activation code")
    kiosk.add_argument("--hospital-id", type=UUID, required=True)
    kiosk.add_argument("--label", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        hospital_name = args.hospital_name
        hospital_code = args.hospital_code
        if args.hospital_id is None:
            hospital_name = hospital_name or input("New hospital name: ").strip()
            hospital_code = hospital_code or input("Unique hospital code: ").strip()
        name = args.name or input("Administrator name: ").strip()
        email = args.email or input("Administrator email: ").strip()
        password = getpass.getpass("Initial password (12+ characters): ")
        hospital_id = asyncio.run(
            _create_admin(
                hospital_id=args.hospital_id,
                hospital_name=hospital_name,
                hospital_code=hospital_code,
                name=name,
                email=email,
                password=password,
            )
        )
        print(f"Administrator created for hospital {hospital_id}.")
        return
    code = asyncio.run(_create_kiosk(hospital_id=args.hospital_id, label=args.label))
    print(f"One-time kiosk activation code: {code}")


if __name__ == "__main__":
    main()
