#!/bin/sh
# Usage: ./deploy/smoke_test.sh https://loopp-support.pages.dev
set -eu
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 BASE_URL" >&2
    exit 2
fi
for dependency in curl python3; do
    command -v "$dependency" >/dev/null 2>&1 || { echo "FAIL: missing $dependency" >&2; exit 2; }
done
base=${1%/}
if ! python3 - "$base" <<'PY'
import sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1])
raise SystemExit(not (u.scheme in ('http', 'https') and u.netloc and not u.username and not u.password and not u.query and not u.fragment))
PY
then
    echo "FAIL: supply an HTTP(S) base URL without credentials, query, or fragment" >&2
    exit 2
fi
umask 077
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/loopp-smoke.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM
failures=0
check() {
    label=$1
    if [ "$2" = pass ]; then echo "PASS: $label"; else echo "FAIL: $label"; failures=$((failures + 1)); fi
}
request() {
    method=$1
    path=$2
    cookies=$3
    if [ "$cookies" = yes ]; then
        status=$(curl -q --silent --show-error --connect-timeout 5 --max-time 20 \
            --request "$method" --cookie "$tmpdir/cookies" --cookie-jar "$tmpdir/cookies" \
            --output "$tmpdir/body" --write-out '%{http_code}' "$base$path") || status=000
    else
        status=$(curl -q --silent --show-error --connect-timeout 5 --max-time 20 \
            --request "$method" --header 'Cookie:' \
            --output "$tmpdir/body" --write-out '%{http_code}' "$base$path") || status=000
    fi
}
json_check() {
    python3 - "$tmpdir/body" "$1" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1]))
    ok = isinstance(value, dict) and (value.get('status') == 'ready' if sys.argv[2] == 'ready' else isinstance(value.get('id'), str) and bool(value['id']))
except (OSError, ValueError, TypeError):
    ok = False
raise SystemExit(not ok)
PY
}
request GET /api/ready no
result=fail
if [ "$status" = 200 ] && json_check ready; then result=pass; fi
check '/api/ready' "$result"
# A new, private cookie jar isolates this smoke test from browser sessions.
: > "$tmpdir/cookies"
request POST /api/session yes
result=fail
if [ "$status" = 200 ] && python3 - "$tmpdir/cookies" <<'PY'
import sys
rows = [line.rstrip().split('\t') for line in open(sys.argv[1])]
raise SystemExit(not any(len(row) == 7 and row[5] == 'loopp_session' and row[6] for row in rows))
PY
then result=pass; fi
check 'POST /api/session sets session cookie' "$result"
request POST /api/chats yes
result=fail
if [ "$status" = 200 ] && json_check chat; then result=pass; fi
check 'POST /api/chats with session cookie' "$result"
request GET /api/chats no
result=fail
if [ "$status" = 401 ]; then result=pass; fi
check 'unauthenticated /api/chats returns 401' "$result"
[ "$failures" -eq 0 ]
