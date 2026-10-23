"""The worker's own metrics (Phase 7, tasks 7.9.1 and 7.9.2; ADR 010).

Created on first use, never at import: the API imports ``app.workers.main`` for its start-up
close-out, and in multiprocess mode every metric that exists in an API process is exported
with a value of 0. A worker-only gauge such as free disk space would then read "0% free" from
each API process and fire false alerts.
"""

from dataclasses import dataclass
from functools import cache

from prometheus_client import Counter, Gauge


@dataclass(frozen=True)
class WorkerMetrics:
    jobs: Counter
    last_success: Gauge
    queue_waiting: Gauge
    outbox_oldest_age: Gauge
    notifications: Counter
    notifications_pending: Gauge
    urgent_alerts_open_age: Gauge
    kiosks_silent: Gauge
    dependency_up: Gauge
    disk_free_ratio: Gauge
    tls_cert_expiry: Gauge
    backup_last_success: Gauge


@cache
def worker_metrics() -> WorkerMetrics:
    return WorkerMetrics(
        jobs=Counter("worker_job_runs_total", "Worker job runs.", ("job", "outcome")),
        last_success=Gauge(
            "worker_job_last_success_timestamp_seconds",
            "When each worker job last finished without an error.",
            ("job",),
        ),
        queue_waiting=Gauge(
            "queue_waiting_patients",
            "Patients waiting today, per department.",
            ("hospital", "department"),
        ),
        outbox_oldest_age=Gauge(
            "outbox_oldest_pending_age_seconds",
            "Age of the oldest queue event not yet applied to the live boards (0 when none).",
        ),
        notifications=Counter(
            "notifications_total", "Queue messages to patients, by outcome.", ("outcome",)
        ),
        notifications_pending=Gauge("notifications_pending", "Queue messages waiting to be sent."),
        urgent_alerts_open_age=Gauge(
            "urgent_alerts_oldest_unacknowledged_age_seconds",
            "Age of the oldest danger-sign alert no one has acknowledged today (0 when none).",
        ),
        kiosks_silent=Gauge(
            "kiosks_silent",
            "Paired kiosks that have not checked in for 10 minutes, per hospital.",
            ("hospital",),
        ),
        dependency_up=Gauge(
            "dependency_up", "1 when the worker can reach the dependency, else 0.", ("dependency",)
        ),
        disk_free_ratio=Gauge(
            "data_disk_free_ratio", "Free share of the disk that holds the database and backups."
        ),
        tls_cert_expiry=Gauge(
            "tls_certificate_expiry_timestamp_seconds",
            "When the HTTPS certificate served expires.",
        ),
        backup_last_success=Gauge(
            "backup_last_success_timestamp_seconds", "When the last database backup completed."
        ),
    )
