"""Indexes the review on a million visits proved missing.

Phase 7, task 7.8.4 (docs/SCALING.md, "Index review on a million visits"). Each one turns a
scan of a whole table into a few index pages:

- ix_visits_open: the visits not yet closed, a few hundred at any time. The 15-minute close-out
  read every visit to find visits still registering or awaiting confirmation on an earlier
  day, and the administrator's all-departments board read every visit to find today's line.
- ix_visits_notify_phone: phone numbers kept for queue messages, only until the nightly wipe,
  which read every visit every 15 minutes to find them.
- ix_visits_registered: the visit register (nurse console, Patients), newest first, sorted
  every visit of the hospital on each load.
- ix_audit_resource, ix_audit_actor: the audit log search by record and by person read the
  whole log when the record or person had few entries.

Built in about 4 seconds on a million visits; upgrades run outside OPD hours.
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_index_review"
down_revision = "0033_privacy_contact"
branch_labels = None
depends_on = None

OPEN_STATUSES = (
    "status IN ('registering', 'awaiting_confirmation', 'queued', 'called', 'in_consult', "
    "'skipped')"
)


def upgrade() -> None:
    op.create_index(
        "ix_visits_open",
        "visits",
        ["hospital_id", "service_date"],
        postgresql_where=sa.text(OPEN_STATUSES),
    )
    op.create_index(
        "ix_visits_notify_phone",
        "visits",
        ["service_date"],
        postgresql_where=sa.text("notify_phone_enc IS NOT NULL OR notify_phone_hmac IS NOT NULL"),
    )
    op.create_index("ix_visits_registered", "visits", ["hospital_id", "registered_at"])
    op.create_index(
        "ix_audit_resource",
        "audit_log",
        ["hospital_id", "resource_id", "created_at"],
        postgresql_where=sa.text("resource_id IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_actor",
        "audit_log",
        ["hospital_id", "actor_id", "created_at"],
        postgresql_where=sa.text("actor_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_audit_actor", table_name="audit_log")
    op.drop_index("ix_audit_resource", table_name="audit_log")
    op.drop_index("ix_visits_registered", table_name="visits")
    op.drop_index("ix_visits_notify_phone", table_name="visits")
    op.drop_index("ix_visits_open", table_name="visits")
