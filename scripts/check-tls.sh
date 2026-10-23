#!/usr/bin/env bash
# Check the HTTPS set-up of an installation (Phase 7, task 7.2.1; docs/runbooks/certificate.md).
#
#   scripts/check-tls.sh opd.example-hospital.in [https port] [http port]
#
# Passes when: TLS 1.2 and 1.3 are offered, TLS 1.0 and 1.1 are refused, the certificate is
# valid for the name and has more than 21 days left, and the page sends HSTS. For a full grade,
# also run testssl.sh from the hospital network (it reports "A" for this configuration).

set -uo pipefail
HOST="${1:?Usage: scripts/check-tls.sh <host> [https port] [http port]}"
PORT="${2:-443}"
HTTP_PORT="${3:-80}"
# Staging with a self-signed certificate only: TLS_INSECURE=1 lets curl read the headers.
INSECURE=()
[[ "${TLS_INSECURE:-0}" == "1" ]] && INSECURE=(-k)
failures=0
check() { if [[ "$1" == "0" ]]; then echo "ok   $2"; else echo "FAIL $2"; failures=$((failures + 1)); fi; }
handshake() { openssl s_client -connect "$HOST:$PORT" -servername "$HOST" "$@" </dev/null >/dev/null 2>&1; }

handshake -tls1_3; check $? "TLS 1.3 offered"
handshake -tls1_2; check $? "TLS 1.2 offered"
for old in tls1_1 tls1; do
  if handshake "-$old"; then check 1 "$old refused"; else check 0 "$old refused"; fi
done

certificate="$(openssl s_client -connect "$HOST:$PORT" -servername "$HOST" </dev/null 2>/dev/null |
  openssl x509 2>/dev/null)"
if [[ -z "$certificate" ]]; then
  check 1 "certificate presented"
else
  openssl x509 -noout -checkhost "$HOST" <<<"$certificate" | grep -q "does match"
  check $? "certificate valid for $HOST"
  openssl x509 -noout -checkend $((21 * 86400)) <<<"$certificate" >/dev/null
  check $? "certificate valid for more than 21 days ($(openssl x509 -noout -enddate <<<"$certificate"))"
fi

curl -fsSI "${INSECURE[@]}" --max-time 10 "https://$HOST:$PORT/staff" 2>/dev/null |
  grep -qi '^strict-transport-security: max-age=63072000'
check $? "HSTS sent"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$HOST:$HTTP_PORT/staff")"
[[ "$code" == "301" ]]; check $? "HTTP redirects to HTTPS"

exit $failures
