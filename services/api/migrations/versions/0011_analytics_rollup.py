"""Create tenant-filtered daily department analytics and a refresh capability."""

from alembic import op

revision = "0011_analytics_rollup"
down_revision = "0010_notification_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT (hospital_id, department_id, service_date, id, language, urgency, "
        "queued_at, consult_started_at, consult_ended_at) ON public.visits "
        "TO osa_analytics_worker"
    )
    op.execute(
        "GRANT SELECT (hospital_id, visit_id, confirmed_by_patient, last_edited_by) "
        "ON public.case_sheets TO osa_analytics_worker"
    )
    op.execute(
        "GRANT SELECT (hospital_id, visit_id, created_at) ON public.interview_turns "
        "TO osa_analytics_worker"
    )
    op.execute("SET ROLE osa_analytics_worker")
    op.execute(
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
        "count(*) FILTER (WHERE vm.doctor_edited) AS doctor_edited_count, "
        "lm.languages AS language_mix FROM visit_metrics vm JOIN language_mix lm "
        "ON lm.hospital_id = vm.hospital_id AND lm.department_id = vm.department_id "
        "AND lm.service_date = vm.service_date "
        "GROUP BY vm.hospital_id, vm.department_id, vm.service_date, lm.languages"
    )
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
    op.execute("GRANT USAGE ON SCHEMA osa_analytics TO osa_app")
    op.execute("GRANT SELECT ON osa_analytics.analytics_daily_department_tenant TO osa_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("REVOKE SELECT (hospital_id, department_id, service_date, id, language, urgency, "
                "queued_at, consult_started_at, consult_ended_at) ON public.visits "
                "FROM osa_analytics_worker")
    op.execute("REVOKE SELECT (hospital_id, visit_id, confirmed_by_patient, last_edited_by) "
                "ON public.case_sheets FROM osa_analytics_worker")
    op.execute("REVOKE SELECT (hospital_id, visit_id, created_at) ON public.interview_turns "
                "FROM osa_analytics_worker")
    op.execute("SET ROLE osa_analytics_worker")
    # The schema itself belongs to database bootstrap (init-db.sh), so only drop what
    # this revision created; otherwise a later upgrade has nowhere to build the views.
    op.execute("DROP VIEW IF EXISTS osa_analytics.analytics_daily_department_tenant")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS osa_analytics.analytics_daily_department")
    op.execute("REVOKE USAGE ON SCHEMA osa_analytics FROM osa_app")
    op.execute("RESET ROLE")
