#!/usr/bin/env bash
# Idempotent dev-environment bootstrap. Safe to re-run; cheap when up to date.
#
# Exists so no session spends tokens rediscovering how to build this
# environment. See NOTES.md.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
    echo "==> creating .venv"
    python3 -m venv .venv
else
    echo "==> .venv exists"
fi

echo "==> installing librarian[api,dev] (editable)"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[api,dev]"

echo "==> smoke check"
.venv/bin/python -c "import librarian, sys; print('librarian ok on Python', sys.version.split()[0])"

cat <<'EOF'

Ready. Activate with:
    source .venv/bin/activate          # bash/zsh
    source .venv/bin/activate.fish     # fish

Then:
    pytest tests/ -q                   # unit
    pytest -m integration -q           # live cluster (docker compose up -d first)
EOF
