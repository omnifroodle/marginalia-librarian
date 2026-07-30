# Environment notes (runbook)

Operational, repetitive, machine-specific things — **not** training content
(that's `CURRICULUM.md`) and not architecture (that's `CLAUDE.md`).

The rule: if a session spends tokens rediscovering something about *the
machine* rather than about Couchbase, it belongs here. If it's mechanical and
re-runnable, it belongs in [`training-tools/`](training-tools/) with a pointer
from here.

**Session start:** `./training-tools/env-check.sh` answers "is this machine
ready?" in one shot.

---

## Process journal

What's working and what isn't about the training loop itself (CLAUDE.md asks
the instructor to keep this).

- **2026-07-30 — cold start.** Kickoff prompt was "read CURRICULUM.md and start
  Lesson 1." It didn't work standalone: the repo had no `.venv` and the cluster
  wasn't running, so the session spent its first several tool calls on
  environment archaeology instead of Couchbase. Fixes applied: this file, the
  `training-tools/` scripts, and a CLAUDE.md rule pointing at both. The kickoff
  prompt should now work cold — `env-check.sh` reports what's missing in one
  call.
- **2026-07-30 — writing tests without importing the SDK.** The hard rule bans
  `from couchbase` in *any* diff the agent writes, including test files. That
  bites on assertions about SDK types (e.g. "bad credentials must raise
  `AuthenticationException`"). Resolution: assert by class *name* via the
  exception's MRO, and by enum `.value` for service types. Slightly unusual,
  but it keeps every SDK import in Matt's code and it survives Matt choosing a
  different SDK version. Worth reusing.
- **2026-07-30 — tests organized by lesson.** Added a `lesson(n)` pytest marker
  and `--lesson N` ("run everything through lesson N"). Integration tests get
  lesson-numbered filenames (`test_l01_connect.py`); unit tests that extend an
  existing module's suite (e.g. `test_config.py`) stay put and just carry the
  marker, so one class's tests don't end up scattered across a dozen files.
  Open question deliberately *not* resolved: pre-generating all lessons' tests
  up front. Rejected for now — Lessons 5, 6 and 10 explicitly leave the design
  to Matt ("either implementation accepted if the reasoning is sound"), and
  pre-written tests would pre-decide those. Shared *fixtures* and helpers can
  still be built ahead of time; test content stays just-in-time.

---

## Python environment

```bash
./training-tools/dev-setup.sh   # idempotent: creates .venv if missing, installs -e ".[api,dev]"
source .venv/bin/activate       # fish: source .venv/bin/activate.fish
```

- Plain `venv` + `pip`, no `uv` (deliberate — matches how the repo was built).
- Python 3.13 via mise on this machine; `requires-python = ">=3.10"`.
- A fresh install takes ~1–2 min (litellm pulls openai/tokenizers/grpcio).
- **First `pytest` or `librarian` run after a fresh venv is slow** (~10s,
  litellm import); sub-second after. Not a hang.
- `.venv/` is gitignored — a fresh clone has no venv. Bootstrap first.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q          # unit only (integration deselected by default)
.venv/bin/python -m pytest -m integration -q  # live-cluster tests
```

Baseline 2026-07-30: **113 unit passed, 1 integration passed.**
Integration tests skip cleanly (with instructions) when nothing is listening on
the cluster's management port — see `tests/integration/conftest.py`.

## Local Couchbase cluster

```bash
docker compose up -d
docker compose ps                 # health
docker compose logs -f couchbase
docker compose down               # stop, keep data
docker compose down -v            # WIPE cluster state (manual init must be redone)
```

One-time manual init (Lesson 0, deliberately by hand) is documented in the
`docker-compose.yml` header. Web UI: <http://localhost:8091> (plain http; TLS
on 18091).

**Is the cluster initialized?**
`curl -s -o /dev/null -w '%{http_code}' http://localhost:8091/pools`
→ `200` = uninitialized (setup wizard is waiting), `401` = initialized and
demanding credentials.

Gotchas seen on this machine:

- **The `nofile` warning in the logs is benign.** The server wants 200000 open
  files; compose sets 40960 per Couchbase's own documented `docker run` line.
  It complains and runs fine for a dev workload.
- **The healthcheck reported `unhealthy` forever after init.** It probed
  `/pools`, which starts returning 401 once the cluster has credentials, and
  `curl -sf` treats 401 as failure. Now probes `/ui/index.html`, which is 200
  in both states.

## Credentials for integration tests

Nothing is committed. The admin credentials chosen during the manual cluster
init are read from the environment:

```bash
export LIBRARIAN_TEST_COUCHBASE_PASSWORD=...              # required; tests skip without it
export LIBRARIAN_TEST_COUCHBASE_USERNAME=Administrator    # optional (this is the default)
export LIBRARIAN_TEST_COUCHBASE_HOST=localhost            # optional; a remote docker host works
export LIBRARIAN_TEST_COUCHBASE_BUCKET=librarian          # optional
```

Fish: `set -x LIBRARIAN_TEST_COUCHBASE_PASSWORD ...`

Library code (not tests) reads `COUCHBASE_CONNECTION_STRING`,
`COUCHBASE_USERNAME`, `COUCHBASE_PASSWORD` as overrides on the `couchbase:`
config block — see `Config.apply_env_overrides`.

`config.yaml` is gitignored; copy `config.example.yaml`. It is **not** required
for the integration suite (fixtures build a `Config` from env) — only for the
CLI and the API.

## Reusing this setup for another training repo

The portable pieces, in order of value:

1. `training-tools/` — the bootstrap + readiness sweep, and the convention of
   having them at all.
2. `tests/integration/conftest.py` — reachability probe, automatic
   `integration` marking, clean skip with instructions. Repo-agnostic apart
   from the port.
3. `docker-compose.yml` — EE stack with correct ulimits, port ranges, and a
   healthcheck that survives cluster init.
4. `CURRICULUM.md`'s loop protocol + hard rule, and this file.
