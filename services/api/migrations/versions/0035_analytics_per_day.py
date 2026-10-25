"""Count the reports one day at a time instead of recounting the whole history.

Phase 7, task 7.8.5 (docs/SCALING.md, "Index review on a million visits"). The reports read a
materialized view that the worker refreshed every 5 minutes. PostgreSQL refreshes a view only as
a whole, so each refresh recounted every day ever kept: 10 seconds at a million visits, reading
11.5 million answers and writing about 1 GB of temporary files, and growing with every day.

The rollup is now a table with the same name and columns, kept by two functions of the worker
role:

- refresh_daily_department(first_day, last_day) recounts the days given. It reads only their
  visits, each hospital's through ix_visits_hospital_date, and each visit's answers through the
  visit's own index. The worker runs it every 5 minutes for the hospital's today and yesterday,
  so its cost follows the patients of two days, not the size of the history.
- rebuild_daily_department() recounts every day, as the view's refresh did. The worker runs it
  once a night for changes to earlier days (a doctor's late edit to a case sheet), and the demo
  tools after rewriting history.

Both delete their days and insert them again in one transaction, so a report never finds a day
missing, and under one lock, so two runs never insert the same day twice. The reports read the
hospital-filtered view, as before.
"""

from alembic import op

revision = "0035_analytics_per_day"
down_revision = "0034_index_review"
branch_labels = None
depends_on = None

COLUMNS = (
    "hospital_id, department_id, service_date, patient_count, wait_seconds_total, "
    "wait_sample_count, consult_seconds_total, consult_sample_count, urgent_count, "
    "interview_seconds_total, interview_sample_count, confirmed_count, "
    "confirmed_without_edits_count, case_sheet_count, doctor_edited_count, language_mix"
)
LOCK_KEY = "hashtextextended('osa_analytics.analytics_daily_department', 0)"
TENANT_VIEW = (
    "CREATE VIEW osa_analytics.analytics_daily_department_tenant "
    "WITH (security_barrier = true) AS "
    "SELECT * FROM osa_analytics.analytics_daily_department WHERE hospital_id = "
    "NULLIF(current_setting('app.hospital_id', true), '')::uuid"
)
DAY_INDEX = (
    "CREATE INDEX ix_analytics_daily_hospital_day ON "
    "osa_analytics.analytics_daily_department (hospital_id, service_date)"
)


def _rollup_select(*, window: bool) -> str:
    """Revision 0014's rollup, of every visit or (window) of the days first_day to last_day."""
    visits = (
        "public.hospitals h JOIN public.visits v ON v.hospital_id = h.id "
        "AND v.service_date BETWEEN first_day AND last_day"
        if window
        else "public.visits v"
    )
    # A window reads the answers of its own visits only; the whole history reads them all.
    turns = (
        "WHERE (t.hospital_id, t.visit_id) IN (SELECT hospital_id, id FROM day_visits) "
        if window
        else ""
    )
    return (
        "WITH day_visits AS ("
        "SELECT v.hospital_id, v.department_id, v.service_date, v.id, v.language, v.urgency, "
        f"v.queued_at, v.consult_started_at, v.consult_ended_at FROM {visits}"
        "), interview_summary AS ("
        "SELECT t.hospital_id, t.visit_id, "
        "GREATEST(EXTRACT(EPOCH FROM (max(t.created_at) - min(t.created_at))), 0) AS seconds "
        f"FROM public.interview_turns t {turns}GROUP BY t.hospital_id, t.visit_id"
        "), visit_metrics AS ("
        "SELECT dv.hospital_id, dv.department_id, dv.service_date, dv.id, dv.language, "
        "dv.urgency, "
        "CASE WHEN dv.queued_at IS NOT NULL AND dv.consult_started_at IS NOT NULL "
        "THEN GREATEST(EXTRACT(EPOCH FROM (dv.consult_started_at - dv.queued_at)), 0) END "
        "AS wait_seconds, "
        "CASE WHEN dv.consult_started_at IS NOT NULL AND dv.consult_ended_at IS NOT NULL "
        "THEN GREATEST(EXTRACT(EPOCH FROM (dv.consult_ended_at - dv.consult_started_at)), 0) "
        "END AS consult_seconds, i.seconds AS interview_seconds, "
        "coalesce(cs.confirmed_by_patient, false) AS confirmed, "
        "(cs.confirmed_by_patient AND cs.last_edited_by IS NULL) AS confirmed_without_edits, "
        "(cs.visit_id IS NOT NULL) AS has_case_sheet, "
        "(cs.last_edited_by IS NOT NULL) AS doctor_edited "
        "FROM day_visits dv LEFT JOIN public.case_sheets cs ON cs.hospital_id = dv.hospital_id "
        "AND cs.visit_id = dv.id LEFT JOIN interview_summary i "
        "ON i.hospital_id = dv.hospital_id AND i.visit_id = dv.id"
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
        "count(*) FILTER (WHERE vm.has_case_sheet) AS case_sheet_count, "
        "count(*) FILTER (WHERE vm.doctor_edited) AS doctor_edited_count, "
        "lm.languages AS language_mix FROM visit_metrics vm JOIN language_mix lm "
        "ON lm.hospital_id = vm.hospital_id AND lm.department_id = vm.department_id "
        "AND lm.service_date = vm.service_date "
        "GROUP BY vm.hospital_id, vm.department_id, vm.service_date, lm.languages"
    )


TABLE = (
    "CREATE TABLE osa_analytics.analytics_daily_department ("
    "hospital_id uuid NOT NULL, department_id uuid NOT NULL, service_date date NOT NULL, "
    "patient_count bigint NOT NULL, wait_seconds_total numeric NOT NULL, "
    "wait_sample_count bigint NOT NULL, consult_seconds_total numeric NOT NULL, "
    "consult_sample_count bigint NOT NULL, urgent_count bigint NOT NULL, "
    "interview_seconds_total numeric NOT NULL, interview_sample_count bigint NOT NULL, "
    "confirmed_count bigint NOT NULL, confirmed_without_edits_count bigint NOT NULL, "
    "case_sheet_count bigint NOT NULL, doctor_edited_count bigint NOT NULL, "
    "language_mix jsonb NOT NULL, PRIMARY KEY (hospital_id, department_id, service_date))"
)
# A custom plan for each call, so a few days are read through the indexes. A refresh that finds
# the rollup busy (the nightly rebuild) returns NULL at once: the rebuild recounts its days too.
REFRESH = (
    "CREATE FUNCTION osa_analytics.refresh_daily_department(first_day date, last_day date) "
    "RETURNS integer LANGUAGE plpgsql SET plan_cache_mode = force_custom_plan AS $refresh$ "
    "DECLARE written integer; BEGIN "
    f"IF NOT pg_try_advisory_xact_lock({LOCK_KEY}) THEN RETURN NULL; END IF; "
    "DELETE FROM osa_analytics.analytics_daily_department "
    "WHERE service_date BETWEEN first_day AND last_day; "
    f"INSERT INTO osa_analytics.analytics_daily_department ({COLUMNS}) "
    f"{_rollup_select(window=True)}; "
    "GET DIAGNOSTICS written = ROW_COUNT; RETURN written; END $refresh$"
)
# The rebuild reads the whole history, where an index scan is the wrong plan: it would visit
# 11.5 million answers in index order, 90 seconds at a million visits instead of 20. The
# planner, which cannot run an INSERT in parallel, picks it all the same.
REBUILD = (
    "CREATE FUNCTION osa_analytics.rebuild_daily_department() "
    "RETURNS integer LANGUAGE plpgsql SET enable_indexscan = off AS $rebuild$ "
    f"DECLARE written integer; BEGIN PERFORM pg_advisory_xact_lock({LOCK_KEY}); "
    "DELETE FROM osa_analytics.analytics_daily_department; "
    f"INSERT INTO osa_analytics.analytics_daily_department ({COLUMNS}) "
    f"{_rollup_select(window=False)}; "
    "GET DIAGNOSTICS written = ROW_COUNT; RETURN written; END $rebuild$"
)


def upgrade() -> None:
    # The window reads each hospital's visits through (hospital_id, service_date).
    op.execute("GRANT SELECT (id) ON public.hospitals TO osa_analytics_worker")
    # Built by the worker role, like revisions 0011 and 0014, so the worker owns what it keeps.
    op.execute("SET ROLE osa_analytics_worker")
    op.execute("DROP VIEW osa_analytics.analytics_daily_department_tenant")
    op.execute("DROP MATERIALIZED VIEW osa_analytics.analytics_daily_department")
    op.execute(TABLE)
    op.execute(DAY_INDEX)
    op.execute(REFRESH)
    op.execute(REBUILD)
    for function in ("refresh_daily_department(date, date)", "rebuild_daily_department()"):
        op.execute(f"REVOKE ALL ON FUNCTION osa_analytics.{function} FROM PUBLIC")
    op.execute("SELECT osa_analytics.rebuild_daily_department()")
    op.execute(TENANT_VIEW)
    op.execute("GRANT SELECT ON osa_analytics.analytics_daily_department_tenant TO osa_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET ROLE osa_analytics_worker")
    op.execute("DROP VIEW osa_analytics.analytics_daily_department_tenant")
    op.execute("DROP FUNCTION osa_analytics.rebuild_daily_department()")
    op.execute("DROP FUNCTION osa_analytics.refresh_daily_department(date, date)")
    op.execute("DROP TABLE osa_analytics.analytics_daily_department")
    op.execute(
        "CREATE MATERIALIZED VIEW osa_analytics.analytics_daily_department AS "
        + _rollup_select(window=False)
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_analytics_daily_department ON "
        "osa_analytics.analytics_daily_department (hospital_id, department_id, service_date)"
    )
    op.execute(DAY_INDEX)
    op.execute(TENANT_VIEW)
    op.execute("GRANT SELECT ON osa_analytics.analytics_daily_department_tenant TO osa_app")
    op.execute("RESET ROLE")
    op.execute("REVOKE SELECT (id) ON public.hospitals FROM osa_analytics_worker")
