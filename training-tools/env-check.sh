#!/usr/bin/env bash
# Read-only status sweep: is this machine ready to run a lesson?
#
# Answers, in one shot, the questions every session otherwise re-derives:
# venv present, container up, cluster initialized, services healthy, bucket
# present, integration credentials exported. Mutates nothing.
set -uo pipefail

cd "$(dirname "$0")/.."

HOST="${LIBRARIAN_TEST_COUCHBASE_HOST:-localhost}"
PORT="${LIBRARIAN_TEST_COUCHBASE_PORT:-8091}"
USER_="${LIBRARIAN_TEST_COUCHBASE_USERNAME:-Administrator}"
PASS="${LIBRARIAN_TEST_COUCHBASE_PASSWORD:-}"
BUCKET="${LIBRARIAN_TEST_COUCHBASE_BUCKET:-librarian}"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

echo "== python =="
if [ -x .venv/bin/python ]; then
    ok ".venv present ($(.venv/bin/python -V 2>&1))"
    if .venv/bin/python -c "import librarian" 2>/dev/null; then
        ok "librarian importable"
    else
        bad "librarian not installed — run training-tools/dev-setup.sh"
    fi
    if .venv/bin/python -c "import couchbase" 2>/dev/null; then
        ok "couchbase SDK $(.venv/bin/python -c 'import couchbase; print(couchbase.__version__)' 2>/dev/null) installed"
    else
        warn "couchbase SDK not installed (expected until Lesson 1 adds the dependency)"
    fi
else
    bad ".venv missing — run training-tools/dev-setup.sh"
fi

echo "== container =="
if command -v docker >/dev/null 2>&1; then
    line=$(docker compose ps --format '{{.Service}} {{.State}} {{.Status}}' 2>/dev/null | grep '^couchbase ' || true)
    if [ -n "$line" ]; then
        ok "compose: $line"
    else
        bad "couchbase container not running — docker compose up -d"
    fi
else
    warn "docker not on PATH"
fi

echo "== cluster ($HOST:$PORT) =="
code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://$HOST:$PORT/pools" || echo 000)
case "$code" in
    401) ok "initialized (management API demands credentials)" ;;
    200) bad "reachable but NOT initialized — finish Lesson 0 via http://$HOST:$PORT (see docker-compose.yml header)" ;;
    000) bad "unreachable — docker compose up -d, or set LIBRARIAN_TEST_COUCHBASE_HOST" ;;
    *)   warn "unexpected status $code from /pools" ;;
esac

echo "== credentials =="
# The env var is only one of two places a password can live: the integration
# fixtures also read couchbase.password out of the gitignored config.yaml
# (tests/integration/conftest.py). Checking only the env var reports "tests
# will skip" at a machine where they run fine — a false alarm that costs a
# session real time. Same resolution order the fixtures use: env wins.
CRED_SRC="LIBRARIAN_TEST_COUCHBASE_PASSWORD"
if [ -z "$PASS" ] && [ -f config.yaml ] && [ -x .venv/bin/python ]; then
    PASS=$(.venv/bin/python -c '
import sys, yaml
try:
    cb = (yaml.safe_load(open("config.yaml")) or {}).get("couchbase") or {}
except Exception:
    sys.exit(0)
pw = str(cb.get("password") or "")
# ${VAR} placeholders are resolved from the environment at load time; an
# unresolved one is not a usable password.
print("" if pw.startswith("${") else pw)
' 2>/dev/null || true)
    [ -n "$PASS" ] && CRED_SRC="config.yaml"
fi

if [ -z "$PASS" ]; then
    bad "no password — export LIBRARIAN_TEST_COUCHBASE_PASSWORD or set couchbase.password in config.yaml (integration tests will skip)"
else
    ok "password from $CRED_SRC"
    auth_code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 -u "$USER_:$PASS" "http://$HOST:$PORT/pools/default" || echo 000)
    if [ "$auth_code" = "200" ]; then
        ok "authenticated as $USER_"
    else
        bad "auth failed for $USER_ (HTTP $auth_code) — check the password in $CRED_SRC"
    fi
fi

if [ -n "$PASS" ]; then
    echo "== services =="
    services=$(curl -s -m 3 -u "$USER_:$PASS" "http://$HOST:$PORT/pools/default" \
        | grep -o '"services":\[[^]]*\]' | head -1 || true)
    if [ -n "$services" ]; then
        ok "node ${services}"
        for svc in kv n1ql index fts; do
            case "$services" in *"\"$svc\""*) ok "$svc present" ;; *) bad "$svc MISSING from this node" ;; esac
        done
    else
        warn "could not read node services"
    fi

    echo "== buckets =="
    buckets=$(curl -s -m 3 -u "$USER_:$PASS" "http://$HOST:$PORT/pools/default/buckets" \
        | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | sort -u | tr '\n' ' ' || true)
    if [ -n "$buckets" ]; then
        ok "buckets: $buckets"
        case " $buckets " in *" $BUCKET "*) ok "bucket '$BUCKET' present" ;; *) bad "bucket '$BUCKET' MISSING — create it (Lesson 0)" ;; esac
    else
        bad "no buckets — create '$BUCKET' (Lesson 0)"
    fi
fi

echo
echo "Next: pytest tests/ -q   |   pytest -m integration -q"
