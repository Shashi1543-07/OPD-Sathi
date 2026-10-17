"""Old papers the patient brings: photos of prescriptions, test reports and discharge papers.

The ``documents`` table (from 0004) gets what review needs: the kind of paper, who added it
(the patient during check-in, or staff), the date printed on it, and the doctor's decision on
what AI read from it. The photo is encrypted (AES-GCM, bound to the document) and kept apart
in ``document_contents``, so listing documents never loads a photo; ``object_key`` names that
storage. What AI reads is stored encrypted in ``extracted`` and stays unconfirmed until a
doctor accepts it. A visit without the patient's consent to AI help keeps its photos unread.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_documents"
down_revision = "0021_consent_purposes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("kind", sa.String(24), nullable=False, server_default="other")
    )
    op.add_column(
        "documents", sa.Column("source", sa.String(16), nullable=False, server_default="patient")
    )
    op.add_column("documents", sa.Column("uploaded_by", sa.Uuid()))
    op.add_column("documents", sa.Column("document_date", sa.Date()))
    op.add_column(
        "documents",
        sa.Column("review_status", sa.String(16), nullable=False, server_default="unreviewed"),
    )
    op.add_column("documents", sa.Column("reviewed_by", sa.Uuid()))
    op.add_column("documents", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_documents_kind",
        "documents",
        "kind IN ('prescription', 'lab_report', 'discharge_summary', 'other')",
    )
    op.create_check_constraint(
        "ck_documents_source",
        "documents",
        "(source = 'patient' AND uploaded_by IS NULL) "
        "OR (source = 'staff' AND uploaded_by IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_documents_review",
        "documents",
        "review_status IN ('unreviewed', 'accepted', 'rejected') "
        "AND (review_status = 'unreviewed') = (reviewed_by IS NULL) "
        "AND (reviewed_by IS NULL) = (reviewed_at IS NULL)",
    )
    op.create_foreign_key(
        "fk_documents_uploader_tenant",
        "documents",
        "users",
        ["hospital_id", "uploaded_by"],
        ["hospital_id", "id"],
    )
    op.create_foreign_key(
        "fk_documents_reviewer_tenant",
        "documents",
        "users",
        ["hospital_id", "reviewed_by"],
        ["hospital_id", "id"],
    )
    # "skipped": not read, because the patient did not allow AI help or AI is off.
    op.drop_constraint("ck_documents_ocr_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_ocr_status",
        "documents",
        "ocr_status IN ('pending', 'complete', 'failed', 'skipped')",
    )
    op.create_table(
        "document_contents",
        sa.Column("hospital_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("hospital_id", "document_id"),
        sa.ForeignKeyConstraint(
            ["hospital_id", "document_id"],
            ["documents.hospital_id", "documents.id"],
            name="fk_document_contents_document_tenant",
            ondelete="CASCADE",
        ),
    )
    op.execute("ALTER TABLE document_contents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_contents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY document_contents_tenant_isolation ON document_contents "
        "USING (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid) "
        "WITH CHECK (hospital_id = nullif(current_setting('app.hospital_id', true), '')::uuid)"
    )
    # A patient can take back a photo before confirming; staff never delete documents.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "GRANT SELECT, INSERT, DELETE ON document_contents TO osa_app; "
        "GRANT DELETE ON documents TO osa_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_app') THEN "
        "REVOKE DELETE ON documents FROM osa_app; "
        "END IF; END $$;"
    )
    op.drop_table("document_contents")
    # The older schema has no "skipped" reading. This touches every hospital, so row-level
    # security is lifted for the owner meanwhile.
    op.execute("ALTER TABLE documents NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE documents SET ocr_status = 'failed' WHERE ocr_status = 'skipped'")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_documents_ocr_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_ocr_status", "documents", "ocr_status IN ('pending', 'complete', 'failed')"
    )
    op.drop_constraint("fk_documents_reviewer_tenant", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_uploader_tenant", "documents", type_="foreignkey")
    op.drop_constraint("ck_documents_review", "documents", type_="check")
    op.drop_constraint("ck_documents_source", "documents", type_="check")
    op.drop_constraint("ck_documents_kind", "documents", type_="check")
    for column in (
        "reviewed_at",
        "reviewed_by",
        "review_status",
        "document_date",
        "uploaded_by",
        "source",
        "kind",
    ):
        op.drop_column("documents", column)
