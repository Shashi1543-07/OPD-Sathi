"""The worker may read what monitoring needs: urgent-alert times and kiosk check-ins.

Phase 7, tasks 7.9.1 and 7.9.2. The worker reports the age of the oldest unacknowledged urgent
alert (an alert to hospital IT fires after five minutes) and how many paired kiosks have gone
silent (an alert fires during OPD hours). It reads only the columns below: which hospital,
levels, flags and times. Never the visit, the rule, the evidence, labels or secrets.
"""

from alembic import op

revision = "0028_worker_monitoring"
down_revision = "0027_kiosk_secret_rotation"
branch_labels = None
depends_on = None

_GRANTS = (
    ("SELECT (hospital_id, level, created_at, acknowledged_at)", "red_flag_events"),
    ("SELECT (hospital_id, kind, is_active, paired_at, deleted_at, last_seen_at)", "kiosks"),
)


def _for_worker(statement: str) -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker') THEN "
        f"{statement} END IF; END $$;"
    )


def upgrade() -> None:
    for privilege, table in _GRANTS:
        _for_worker(f"GRANT {privilege} ON {table} TO osa_worker;")


def downgrade() -> None:
    for privilege, table in _GRANTS:
        _for_worker(f"REVOKE {privilege} ON {table} FROM osa_worker;")
