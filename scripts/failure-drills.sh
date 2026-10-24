#!/usr/bin/env bash
# Failure drills (Phase 7, tasks 7.7.8 and 7.13.4): on the production-mode stack, break one
# thing at a time and check what staff and IT would see, then that it recovers.
#
#   Redis down        API not ready (503, DEPENDENCY_UNAVAILABLE); alert RedisDown
#   PostgreSQL down   API not ready (503, DEPENDENCY_UNAVAILABLE); alert DatabaseDown
#   Worker down       API stays ready (check-in and the queue go on); alert WorkerDown
#   Disk full         alerts DiskSpaceLow and DiskSpaceCritical
#
# An alert counts once Prometheus holds it pending or firing: its condition is true. How long
# each waits before firing (its "for") is tested with promtool (infra/monitoring/alerts.test.yml).
# What to do for each alert: docs/runbooks/. The stack comes from scripts/prod_smoke.sh, which
# also runs the restore drill (task 7.7.5), so a nightly run covers restores too.
#
#   scripts/failure-drills.sh            # start the stack, run every drill, tear down
#   DRILLS="redis disk" scripts/failure-drills.sh
#
# Synthetic data only. Needs what scripts/prod_smoke.sh needs.

set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
HTTPS_PORT="${SMOKE_HTTPS_PORT:-8443}"
DRILLS="${DRILLS:-redis postgres worker disk}"
WORK="$(mktemp -d)"
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

echo "==> Starting the production stack (scripts/prod_smoke.sh)"
if ! KEEP=1 SMOKE_COMPOSE_FILE="$WORK/compose" bash scripts/prod_smoke.sh > "$WORK/stack.log" 2>&1; then
  echo "FAIL the production stack did not pass its smoke test:"; tail -40 "$WORK/stack.log"; exit 1
fi
grep -E "^(ok|FAIL) +restore drill" "$WORK/stack.log" || true
read -r -a C < "$WORK/compose"
cleanup() {
  "${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
failed=0

# The worker's disk probe, swapped for a small tmpfs during the disk drill.
cat > "$WORK/disk-drill.yml" <<'YAML'
services:
  worker:
    volumes: !override
      - backup-status:/var/lib/osa-backup:ro
    tmpfs: !override
      - /tmp
      - /var/lib/osa-disk:size=8m,mode=1777
YAML

readiness() {
  curl -sk -o "$WORK/ready.json" -w '%{http_code}' "https://localhost:$HTTPS_PORT/api/readyz" ||
    printf '000'
}

# wait_ready CODE [SECONDS]: readiness answers CODE (503s must name the dependency problem).
wait_ready() {
  local want="$1" deadline=$((SECONDS + ${2:-90})) got
  while ((SECONDS < deadline)); do
    got="$(readiness)"
    if [[ "$got" == "$want" ]]; then
      if [[ "$want" != "503" ]] || grep -q '"DEPENDENCY_UNAVAILABLE"' "$WORK/ready.json"; then
        return 0
      fi
    fi
    sleep 2
  done
  echo "     readiness answered $got, not $want: $(head -c 200 "$WORK/ready.json" 2>/dev/null)"
  return 1
}

# alert_state NAME: pending, firing, or inactive.
alert_state() {
  "${C[@]}" exec -T prometheus wget -qO- "http://localhost:9090/api/v1/alerts" 2>/dev/null |
    "$PYTHON" -c "
import json, sys
try:
    alerts = json.load(sys.stdin)['data']['alerts']
except ValueError:
    alerts = []
states = {a['state'] for a in alerts if a['labels'].get('alertname') == '$1'}
print('firing' if 'firing' in states else 'pending' if 'pending' in states else 'inactive')
"
}

# wait_alert NAME active|inactive [SECONDS]
wait_alert() {
  local name="$1" want="$2" deadline=$((SECONDS + ${3:-180})) state=""
  while ((SECONDS < deadline)); do
    state="$(alert_state "$name")"
    if [[ "$want" == "active" && "$state" != "inactive" ]] ||
      [[ "$want" == "inactive" && "$state" == "inactive" ]]; then
      return 0
    fi
    sleep 5
  done
  echo "     alert $name is $state, not $want"
  return 1
}

check() { # check DESCRIPTION COMMAND...
  local description="$1"; shift
  if "$@"; then echo "ok   $description"; else echo "FAIL $description"; failed=1; fi
}

stays_ready() { # The API must answer 200 throughout (ten checks over 20 s).
  for _ in $(seq 1 10); do [[ "$(readiness)" == "200" ]] || return 1; sleep 2; done
}

drill_redis() {
  echo "==> Redis down"
  "${C[@]}" stop redis >/dev/null
  check "API not ready while Redis is down (503, DEPENDENCY_UNAVAILABLE)" wait_ready 503
  check "alert RedisDown raised" wait_alert RedisDown active
  "${C[@]}" start redis >/dev/null
  check "API ready again once Redis is back" wait_ready 200 120
  check "alert RedisDown cleared" wait_alert RedisDown inactive
}

drill_postgres() {
  echo "==> PostgreSQL down"
  "${C[@]}" stop postgres >/dev/null
  check "API not ready while PostgreSQL is down (503, DEPENDENCY_UNAVAILABLE)" wait_ready 503
  check "alert DatabaseDown raised" wait_alert DatabaseDown active
  "${C[@]}" start postgres >/dev/null
  check "API ready again once PostgreSQL is back" wait_ready 200 180
  check "alert DatabaseDown cleared" wait_alert DatabaseDown inactive
}

drill_worker() {
  echo "==> Worker down"
  "${C[@]}" stop worker >/dev/null
  check "API stays ready while the worker is down" stays_ready
  check "alert WorkerDown raised" wait_alert WorkerDown active
  "${C[@]}" start worker >/dev/null
  check "alert WorkerDown cleared once the worker is back" wait_alert WorkerDown inactive
}

drill_disk() {
  echo "==> Disk full"
  "${C[@]}" -f "$(host_path "$WORK/disk-drill.yml")" up -d --no-deps worker >/dev/null 2>&1
  # Fill the probe (dd stops at "no space left", which is the point).
  "${C[@]}" exec -T -u root worker sh -c \
    'dd if=/dev/zero of=/var/lib/osa-disk/fill bs=64k 2>/dev/null; sync' || true
  check "alert DiskSpaceLow raised" wait_alert DiskSpaceLow active
  check "alert DiskSpaceCritical raised" wait_alert DiskSpaceCritical active
  "${C[@]}" up -d --no-deps worker >/dev/null 2>&1
  check "disk alerts cleared once space is back" wait_alert DiskSpaceCritical inactive
  check "alert DiskSpaceLow cleared" wait_alert DiskSpaceLow inactive
}

check "API ready before the drills" wait_ready 200
for drill in $DRILLS; do
  "drill_$drill"
done
exit $failed
