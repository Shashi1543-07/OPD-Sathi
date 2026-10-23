"""Probes the worker runs for the alerts, without extra exporters (Phase 7, task 7.9.2).

Each returns None when it cannot answer, so a missing value (and the alert on its absence)
is never confused with a healthy one.
"""

import json
import shutil
import socket
import ssl
from pathlib import Path

from cryptography import x509


def disk_free_ratio(path: str) -> float | None:
    """Free share of the filesystem holding ``path`` (a directory on the database's disk)."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.free / usage.total if usage.total else None


def certificate_expiry(address: str, timeout: float = 5.0) -> float | None:
    """Expiry (Unix time) of the certificate served at ``host:port``.

    Reads the certificate the way a browser receives it, so the worker never needs the
    certificate files or the key. Not verified: the point is to read the date, including
    on a certificate that is about to fail verification.
    """
    host, _, port = address.rpartition(":")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ValueError):
        return None
    if not der:
        return None
    return x509.load_der_x509_certificate(der).not_valid_after_utc.timestamp()


def backup_last_success(status_file: str) -> float | None:
    """When the backup job last succeeded, from the status file it writes
    (``{"last_success": <unix time>}``; scripts/backup-pitr.sh)."""
    try:
        value = json.loads(Path(status_file).read_text(encoding="utf-8"))["last_success"]
        return float(value)
    except (OSError, ValueError, KeyError, TypeError):
        return None
