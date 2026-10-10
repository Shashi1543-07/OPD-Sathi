"""Count visits with a case sheet in the daily rollup.

The doctor edit rate divided edited case sheets by every visit, including check-ins that
were abandoned or never reached a doctor, so it understated how often doctors correct the
organized history. The rollup now also counts visits that have a case sheet, which is the
denominator the rate needs. The view is rebuilt with its indexes and tenant filter intact.
"""

from alembic import op

revision = "0014_analytics_case_sheets"
down_revision = "0013_triage_score"
branch_labels = None
depends_on = None


def _rollup_sql(*, with_case_sheets: bool) -> str:
    has_sheet = "(cs.visit_id IS NOT NULL) AS has_case_sheet, " if with_case_sheets else ""
    sheet_count = (
        "count(*) FILTER (WHERE vm.has_case_sheet) AS case_sheet_count, "
        if with_case_sheets
        else ""
    )
    return (
        "CREATE MATERIALIZED VIEW osa_analytics.analytics_daily_department AS "
        "WITH interview_summary AS ("
        "SELECT hospital_id, visit_id, "
        "GREATEST(EXTRACT(EPOCH FROM (max(created_at) - min(created_at))), 0) AS seconds, "
        "count(*) AS turn_count FROM interview_turns GROUP BY hospital_id, visit_id"
        "), visit_metrics AS ("
        "SELECT v.hospital_id, v.department_id, v.service_date, v.id, v.language, v.urgency, "
        "v.queued_at, v.consult_started_at, v.consult_ended_at, "
        "CASE WHEN v.queued_at IS NOT NULL AND v.consult_started_at IS NOT NULL "
        "THEN GREATEST(EXTRACT(EPOCH FROM (v.consult_started_at - v.queued_at)), 0) END "
        "AS wait_seconds, "
        "CASE WHEN v.consult_started_at IS NOT NULL AND v.consult_ended_at IS NOT NULL "
        "THEN GREATEST(EXTRACT(EPOCH FROM (v.consult_ended_at - v.consult_started_at)), 0) END "
        "AS consult_seconds, i.seconds AS interview_seconds, "
        "coalesce(cs.confirmed_by_patient, false) AS confirmed, "
        "(cs.confirmed_by_patient AND cs.last_edited_by IS NULL) AS confirmed_without_edits, "
        f"{has_sheet}"
        "(cs.last_edited_by IS NOT NULL) AS doctor_edited "
        "FROM visits v LEFT JOIN case_sheets cs ON cs.hospital_id = v.hospital_id "
        "AND cs.visit_id = v.id LEFT JOIN interview_summary i ON i.hospital_id = v.hospital_id "
        "AND i.visit_id = v.id"
        "), language_counts AS ("
        "SELECT hospital_id, department_id, service_date, language, count(*) AS patients "
        "FROM visit_metrics GROUP BY hospital_id, department_id, service_date, language"
        "), language_mix AS ("
        "SELECT hospital_id, department_id, service_date, "
        "jsonb_object_agg(language, patients) AS languages FROM language_counts "
        "GROUP BY hospital_id, department_id, service_date"
        ") SELECT vm.hospital_id, vm.department_id, vm.service_date, count(*) AS patient_count, "
        "coalesce(sum(vm.wait_seconds), 0) AS wait_seconds_total, "
        "count(vm.wait_seconds) AS wait_sample_count, "
        "coalesce(sum(vm.consult_seconds), 0) AS consult_seconds_total, "
        "count(vm.consult_seconds) AS consult_sample_count, "
        "count(*) FILTER (WHERE vm.urgency = 'urgent') AS urgent_count, "
        "coalesce(sum(vm.interview_seconds), 0) AS interview_seconds_total, "
        "count(vm.interview_seconds) AS interview_sample_count, "
        "count(*) FILTER (WHERE vm.confirmed) AS confirmed_count, "
        "count(*) FILTER (WHERE vm.confirmed_without_edits) AS confirmed_without_edits_count, "
        f"{sheet_count}"
        "count(*) FILTER (WHERE vm.doctor_edited) AS doctor_edited_count, "
        "lm.languages AS language_mix FROM visit_metrics vm JOIN language_mix lm "
        "ON lm.hospital_id = vm.hospital_id AND lm.department_id = vm.department_id "
        "AND lm.service_date = vm.service_date "
        "GROUP BY vm.hospital_id, vm.department_id, vm.service_date, lm.languages"
    )


def _rebuild(*, with_case_sheets: bool) -> None:
    # Built by the worker role, like revision 0011, so the worker can keep refreshing it.
    op.execute("SET ROLE osa_analytics_worker")
    op.execute("DROP VIEW IF EXISTS osa_analytics.analytics_daily_department_tenant")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS osa_analytics.analytics_daily_department")
    op.execute(_rollup_sql(with_case_sheets=with_case_sheets))
    op.execute(
        "CREATE UNIQUE INDEX uq_analytics_daily_department ON "
        "osa_analytics.analytics_daily_department (hospital_id, department_id, service_date)"
    )
    op.execute(
        "CREATE INDEX ix_analytics_daily_hospital_day ON "
        "osa_analytics.analytics_daily_department (hospital_id, service_date)"
    )
    op.execute(
        "CREATE VIEW osa_analytics.analytics_daily_department_tenant "
        "WITH (security_barrier = true) AS "
        "SELECT * FROM osa_analytics.analytics_daily_department WHERE hospital_id = "
        "NULLIF(current_setting('app.hospital_id', true), '')::uuid"
    )
    op.execute("GRANT SELECT ON osa_analytics.analytics_daily_department_tenant TO osa_app")
    op.execute("RESET ROLE")


def upgrade() -> None:
    _rebuild(with_case_sheets=True)


def downgrade() -> None:
    _rebuild(with_case_sheets=False)
