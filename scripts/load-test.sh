#!/usr/bin/env bash
# Load and soak tests (Phase 7, tasks 7.8.2 and 7.8.3; docs/SCALING.md, "Load and soak tests").
#
#   scripts/load-test.sh                          # 15 minutes at a district hospital's peak
#   DURATION=8h ARRIVALS_PER_HOUR=250 scripts/load-test.sh   # soak: a whole OPD day
#
# Starts the production-mode stack (scripts/prod_smoke.sh, synthetic data), prepares kiosks
# and doctors (scripts/load_prepare.py), runs infra/k6/opd-day.js inside the stack's private
# network against the API, then checks what a load test must prove beyond response times:
# no token number issued twice, and memory, database connections and Redis memory steady.
# Reports go to load-reports/ (or REPORT_DIR). Never point this at a hospital's server.

set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
REPORTS="${REPORT_DIR:-load-reports}"
DURATION="${DURATION:-15m}"
K6_IMAGE="grafana/k6:2.3.0@sha256:e66db15b860113878fa74670e31f5e274830b7b6e42c8bff28b2f2d86a257603"
WORK="$(mktemp -d)"
mkdir -p "$REPORTS"
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

echo "==> Starting the production-mode stack"
KEEP=1 SMOKE_STATE_FILE="$(host_path "$WORK/state.json")" \
  SMOKE_COMPOSE_FILE="$WORK/compose" bash scripts/prod_smoke.sh > "$REPORTS/stack.log" 2>&1 ||
  { tail -30 "$REPORTS/stack.log"; echo "FAIL the stack did not start"; exit 1; }
read -r -a C < "$WORK/compose"
cleanup() {
  if [[ "${KEEP:-0}" != "1" ]]; then
    "${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK"
  else
    echo "Stack and fixtures kept: $WORK (compose command in $WORK/compose)"
  fi
}
trap cleanup EXIT
NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' \
  "$("${C[@]}" ps -q api)")"

echo "==> Preparing kiosks and doctors"
"$PYTHON" scripts/load_prepare.py "$(host_path "$WORK/state.json")" \
  "$(host_path "$WORK/fixtures.json")" "${KIOSKS:-10}" "${DOCTORS:-8}"

snapshot() {  # name
  {
    echo "## $1 ($(date -u +%FT%TZ))"
    docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' $("${C[@]}" ps -q)
    echo "postgres connections: $("${C[@]}" exec -T postgres psql -U postgres -d osa -qAt -c \
      'SELECT count(*) FROM pg_stat_activity')"
    echo "redis used_memory: $("${C[@]}" exec -T redis sh -c \
      'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli info memory' |
      tr -d '\r' | sed -n 's/^used_memory_human://p')"
  } | tee -a "$REPORTS/resources.txt"
}
: > "$REPORTS/resources.txt"
snapshot before

echo "==> Running k6 for $DURATION"
status=0
docker run --rm --network "$NETWORK" -v "$(host_path "$WORK"):/load" \
  -v "$(host_path "$PWD/infra/k6"):/scripts:ro" "$K6_IMAGE" run --quiet \
  -e FIXTURES=/load/fixtures.json -e DURATION="$DURATION" \
  -e ARRIVALS_PER_HOUR="${ARRIVALS_PER_HOUR:-300}" -e BOARDS="${BOARDS:-20}" \
  -e STATUS_PAGES="${STATUS_PAGES:-200}" -e ANSWER_PAUSE="${ANSWER_PAUSE:-4}" \
  -e CONSULT_SECONDS="${CONSULT_SECONDS:-120}" \
  --summary-export /load/summary.json /scripts/opd-day.js | tee "$REPORTS/k6.txt" || status=1
cp "$WORK/summary.json" "$REPORTS/k6-summary.json" 2>/dev/null || true

snapshot after

echo "==> Server-side latency by route (Prometheus, p95 over the run)" | tee -a "$REPORTS/resources.txt"
# The API's own view, without the load generator's queueing on the same machine.
"${C[@]}" exec -T prometheus wget -qO- --post-data "query=sort_desc(histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{job=\"api\"}[$DURATION]))))" \
  http://localhost:9090/api/v1/query | "$PYTHON" -c "
import json, sys
for item in json.load(sys.stdin)['data']['result'][:12]:
    value = item['value'][1]
    if value != 'NaN':
        print(f\"  {float(value) * 1000:8.0f} ms  {item['metric'].get('route', '?')}\")
" | tee -a "$REPORTS/resources.txt"

echo "==> Tokens unique within each department and day"
duplicates="$("${C[@]}" exec -T postgres psql -U postgres -d osa -qAt -c "
  SELECT count(*) FROM (SELECT 1 FROM visits WHERE token_number IS NOT NULL
  GROUP BY hospital_id, department_id, service_date, token_number HAVING count(*) > 1) d")"
issued="$("${C[@]}" exec -T postgres psql -U postgres -d osa -qAt -c \
  "SELECT count(*) FROM visits WHERE token_number IS NOT NULL")"
if [[ "$duplicates" == "0" ]]; then echo "ok   $issued tokens issued, none twice"; else
  echo "FAIL $duplicates token number(s) issued twice"; status=1; fi | tee -a "$REPORTS/resources.txt"

echo "==> Worker errors during the run"
failed="$("${C[@]}" logs worker 2>&1 | grep -c '"level": "error"\|Traceback' || true)"
[[ "$failed" == "0" ]] && echo "ok   no worker errors" ||
  { echo "FAIL $failed worker error line(s)"; status=1; }

echo
echo "Reports in $REPORTS/: k6.txt (thresholds), k6-summary.json, resources.txt (before/after)."
exit $status
