"""Consent by purpose: care (required) and online AI help (optional), and its withdrawal.

Until now a visit had one consent: to collect the history for the clinic team. Patients are
now also asked, separately and without a pre-selected answer, whether online AI services may
process their answers, voice and photos. Each purpose is its own consent row with the
version and hash of the notice the patient heard, so a "no" is recorded as precisely as a
"yes". A granted consent can be withdrawn later, by the patient (from their status page) or
by staff at the desk; the row keeps when and by whom.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_consent_purposes"
down_revision = "0020_interview_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consents", sa.Column("purpose", sa.String(16), nullable=False, server_default="care")
    )
    op.add_column("consents", sa.Column("withdrawn_at", sa.DateTime(timezone=True)))
    op.add_column("consents", sa.Column("withdrawn_by", sa.String(16)))
    op.create_check_constraint("ck_consents_purpose", "consents", "purpose IN ('care', 'ai')")
    op.create_check_constraint(
        "ck_consents_withdrawal",
        "consents",
        "(withdrawn_at IS NULL) = (withdrawn_by IS NULL) "
        "AND (withdrawn_by IS NULL OR withdrawn_by IN ('patient', 'staff')) "
        "AND (withdrawn_at IS NULL OR granted)",
    )
    op.drop_constraint("uq_consents_visit", "consents", type_="unique")
    op.create_unique_constraint(
        "uq_consents_visit_purpose", "consents", ["hospital_id", "visit_id", "purpose"]
    )


def downgrade() -> None:
    # The older schema has one consent per visit: drop the AI ones. This touches every
    # hospital, so row-level security is lifted for the owner meanwhile.
    op.execute("ALTER TABLE consents NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM consents WHERE purpose <> 'care'")
    op.execute("ALTER TABLE consents FORCE ROW LEVEL SECURITY")
    op.drop_constraint("uq_consents_visit_purpose", "consents", type_="unique")
    op.create_unique_constraint("uq_consents_visit", "consents", ["hospital_id", "visit_id"])
    op.drop_constraint("ck_consents_withdrawal", "consents", type_="check")
    op.drop_constraint("ck_consents_purpose", "consents", type_="check")
    op.drop_column("consents", "withdrawn_by")
    op.drop_column("consents", "withdrawn_at")
    op.drop_column("consents", "purpose")
