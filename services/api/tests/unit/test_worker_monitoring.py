"""The worker's alert probes answer, or say they cannot (Phase 7, task 7.9.2)."""

import json
import socket
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.workers import monitoring


def test_disk_free_ratio_reads_the_filesystem(tmp_path: Path) -> None:
    ratio = monitoring.disk_free_ratio(str(tmp_path))
    assert ratio is not None and 0 < ratio <= 1
    assert monitoring.disk_free_ratio(str(tmp_path / "missing")) is None


def test_backup_status_file(tmp_path: Path) -> None:
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"last_success": 1790000000}), encoding="utf-8")
    assert monitoring.backup_last_success(str(status)) == 1790000000.0
    status.write_text("{}", encoding="utf-8")
    assert monitoring.backup_last_success(str(status)) is None
    assert monitoring.backup_last_success(str(tmp_path / "missing.json")) is None


@pytest.fixture
def tls_server(tmp_path: Path) -> Iterator[tuple[str, datetime]]:
    """A local HTTPS-like server with a self-signed certificate expiring in 30 days."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(expires)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "tls.crt", tmp_path / "tls.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with context.wrap_socket(connection, server_side=True):
                pass
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}", expires
    listener.close()
    thread.join(timeout=5)


def test_certificate_expiry_is_read_from_the_served_certificate(
    tls_server: tuple[str, datetime],
) -> None:
    address, expires = tls_server
    assert monitoring.certificate_expiry(address) == expires.timestamp()


def test_certificate_expiry_is_none_when_nothing_answers() -> None:
    with socket.create_server(("127.0.0.1", 0)) as probe:
        port = probe.getsockname()[1]
    assert monitoring.certificate_expiry(f"127.0.0.1:{port}", timeout=1) is None
