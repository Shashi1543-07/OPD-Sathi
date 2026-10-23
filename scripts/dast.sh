#!/usr/bin/env bash
# Dynamic security tests against a running production-mode stack (Phase 7, tasks 7.4.4, 7.4.5;
# ADR 009). Run by the nightly workflow after scripts/prod_smoke.sh has started the stack.
#
#   SMOKE_STATE=<file> scripts/dast.sh https://localhost:8443 services/api/openapi.json [report-dir]
#
# SMOKE_STATE is the file scripts/prod_smoke.py writes (SMOKE_STATE_FILE): the scans then sign in
# as the smoke stack's synthetic administrator, with a fresh token per scan, and reach the code
# behind the authentication gate. Without it they test only the gate. Both tools are throttled
# below nginx's rate limits so that a 429 is not mistaken for a changed response.
#
# 1. OWASP ZAP baseline: passive scan of the web app's pages and headers.
# 2. OWASP ZAP API scan: active scan of every API operation from the OpenAPI document (the
#    production API does not publish it, so the schema generated from the source is used).
# 3. Schemathesis: property-based fuzzing of every operation; fails on any server error and on
#    any operation that answers without the authentication it requires.
#
# Synthetic data only; never point this at a hospital's installation.

set -euo pipefail
export MSYS_NO_PATHCONV=1

BASE="${1:?Usage: scripts/dast.sh <https base url> <openapi.json> [report-dir]}"
SCHEMA="${2:?Usage: scripts/dast.sh <https base url> <openapi.json> [report-dir]}"
REPORTS="${3:-dast-reports}"
ZAP_IMAGE="ghcr.io/zaproxy/zaproxy:2.17.0@sha256:5ccfff9a411743ab2615136a2edd7416bec9ce41572fa94e935df38c14adb2b6"
SCHEMATHESIS_IMAGE="schemathesis/schemathesis:4.32.4@sha256:b501f25d00fa6e2b393ddd1b525a11bb560d593fd06268491595c2e439090bf0"
# Containers reach the host's stack through the host network (Linux) or Docker Desktop's alias.
if [[ "$(uname -s)" == Linux* ]]; then
  NETWORK=(--network host)
  TARGET="$BASE"
else
  NETWORK=()
  TARGET="${BASE/localhost/host.docker.internal}"
fi
mkdir -p "$REPORTS"
cp "$SCHEMA" "$REPORTS/openapi.json"
cp "$(dirname "$0")/../infra/zap/zap-rules.tsv" "$REPORTS/zap-rules.tsv"
PYTHON="${PYTHON:-python3}"
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
MOUNT="$(host_path "$(cd "$REPORTS" && pwd)"):/zap/wrk:rw"
chmod 0777 "$REPORTS" 2>/dev/null || true
status=0
AUTH=()
SCHEMATHESIS_AUTH=()
sign_in() {
  [[ -n "${SMOKE_STATE:-}" ]] || return 0
  local token
  token="$("$PYTHON" scripts/dast_token.py "$(host_path "$SMOKE_STATE")")"
  AUTH=(-e "ZAP_AUTH_HEADER_VALUE=Bearer $token")
  SCHEMATHESIS_AUTH=(-H "Authorization: Bearer $token")
}

echo "==> ZAP baseline (passive) on $TARGET"
docker run --rm "${NETWORK[@]}" -v "$MOUNT" "$ZAP_IMAGE" zap-baseline.py \
  -t "$TARGET/staff" -c zap-rules.tsv -r baseline.html -J baseline.json -I || status=1

echo "==> ZAP API scan (active) on $TARGET/api"
# The schema's server is the API root behind nginx.
"$PYTHON" - "$(host_path "$REPORTS/openapi.json")" "$TARGET/api" <<'PY'
import json, sys
path, server = sys.argv[1], sys.argv[2]
schema = json.load(open(path, encoding="utf-8"))
schema["servers"] = [{"url": server}]
json.dump(schema, open(path, "w", encoding="utf-8"))
PY
# One thread with a pause between requests stays under the API's rate limits (a 429 among
# the boolean-injection probes reads as "different data"), and at most 9 minutes so the
# 10-minute access token lasts the whole scan.
sign_in
docker run --rm "${NETWORK[@]}" "${AUTH[@]}" -v "$MOUNT" "$ZAP_IMAGE" zap-api-scan.py \
  -t /zap/wrk/openapi.json -f openapi -c zap-rules.tsv -r api.html -J api.json -I \
  -z "-config scanner.threadPerHost=1 -config scanner.delayInMs=100 -config scanner.maxScanDurationInMins=9" \
  || status=1

echo "==> Schemathesis (fuzzing) on $TARGET/api"
sign_in
docker run --rm "${NETWORK[@]}" -v "$MOUNT" "$SCHEMATHESIS_IMAGE" run /zap/wrk/openapi.json \
  "${SCHEMATHESIS_AUTH[@]}" --url "$TARGET/api" --tls-verify false --max-examples 25 \
  --workers 2 --rate-limit 900/m \
  --checks not_a_server_error,ignored_auth \
  --report junit --report-dir /zap/wrk/schemathesis || status=1

# ZAP fails the run only on rules marked FAIL in zap-rules.tsv (high-risk findings); -I keeps
# warnings informational. Reports are in $REPORTS for review either way.
for report in baseline.json api.json; do
  if [[ -f "$REPORTS/$report" ]] && "$PYTHON" - "$(host_path "$REPORTS/$report")" <<'PY'
import json, sys
alerts = [a for site in json.load(open(sys.argv[1]))["site"] for a in site.get("alerts", [])]
high = [a["name"] for a in alerts if a.get("riskcode") == "3"]
print("\n".join(f"HIGH: {name}" for name in high))
sys.exit(1 if high else 0)
PY
  then :; else status=1; fi
done
exit $status
