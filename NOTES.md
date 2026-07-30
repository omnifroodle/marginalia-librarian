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
- **2026-07-30 — scaffold bug: SDK vocabulary asserted from memory.** The
  Lesson 1 ping test asserted service names `{"kv", "n1ql", "fts"}` — those are
  the *wire/REST* names, and the Python SDK's `ServiceType` enum values are
  `key_value` / `query` / `search`. Matt's correct implementation failed a
  wrong test, which is the worst failure mode in this loop: it costs him a
  debugging round on someone else's mistake and undermines trust in the
  scaffold. **Rule going forward: any SDK-specific literal in a scaffold
  (enum values, exception names, attribute names) gets verified against the
  installed SDK by introspection before the test is committed** — not recalled.
  Deciding to write tests without importing the SDK does not license guessing
  about it. Cheap to check:
  `python -c "from couchbase.diagnostics import ServiceType; print({s.name: s.value for s in ServiceType})"`
- **2026-07-30 — the rubric has to live in tests, not prose.** Matt reported
  "4 tests passed but the code isn't what is required." True: the Lesson 1
  rubric asked for a version-constrained dependency, no blanket `except`, and
  timeouts actually reaching the SDK, and *nothing enforced any of it*. A
  healthy local cluster answers well inside every default, so dropped timeouts
  are invisible. Fixes: the three hygiene tests over `cb/`, and a behavioral
  timeout test that points at TEST-NET-1 and watches the clock. **Rule: every
  rubric line either has a test or is explicitly a judgement call raised in
  review** — a rubric item with no test is a rubric item that will pass.
- **2026-07-30 — "how would you build it?" is the pressure point.** The hard
  rule held all lesson, but the useful move when Matt was stuck wasn't prose
  about the API — it was a skeleton with the SDK calls elided (`options = ...
  # ClusterOptions(authenticator, timeout_options=...)`) plus a two-line REPL
  check he could run to *see* the failure himself (`dict(options)` before and
  after). Showing the shape and the diagnostic, not the answer. Worth reusing:
  when the blocker is structural (where does this line go) rather than
  conceptual, the skeleton unblocks without writing his code.
- **2026-07-30 — silent-drop APIs need behavioral tests, not shape tests.**
  Three separate Lesson 1 bugs were the SDK accepting something and ignoring
  it: floats where `timedelta` was required (accepted at construction, raised
  much later), `options.timeout_options = x` on a dict subclass (silently
  discarded), unrecognized `ClusterTimeoutOptions` keys (dropped, not
  rejected). No amount of asserting on the options object catches these. Every
  future lesson touching an options object should get at least one test that
  measures an *effect*.
- **2026-07-30 — agent commit discipline.** `git add -A` swept Matt's
  in-progress `client.py` into an agent commit, violating "Matt commits his own
  lesson code with his own commit message." Recovered with `git reset --soft
  HEAD~1`. **Rule: agent commits always name explicit paths, never `-A`/`.`**
- **2026-07-30 — dry-run the scaffold against a throwaway implementation.**
  Lesson 1 established "verify SDK literals by introspection." Lesson 2 showed
  that isn't enough: the scaffold asserted `status()` must report an exact
  document count immediately after a write, and a probe had seemed to confirm
  `scan_consistency=request_plus` delivered it. It doesn't — the probe's
  earlier `sleep`s had let the metadata converge before the `request_plus`
  query ran, and the real answer is that *no* unindexed query can be exact
  (see the counting table below). Matt would have spent a round trying to
  satisfy an impossible test.

  Caught it by writing a reference `CollectionManager` in the scratchpad — a
  pytest plugin that rebinds `librarian.cb.manager.CollectionManager` before
  collection, so the suite runs against it without a line entering the repo:

  ```bash
  PYTHONPATH=<scratchpad> pytest -m integration -q -p refimpl_plugin
  ```

  12 of 13 passed; the 13th was the impossible one. **Rule: every acceptance
  scaffold gets run green against a throwaway implementation before handoff.**
  It stays on the agent's side of the hard rule (outside the repo, never shown
  to Matt, deleted after), and it is the only thing that reliably distinguishes
  "this test is hard" from "this test is wrong."
- **2026-07-30 — briefs archived as their own artifact** (`docs/briefs/`), at
  Matt's request: "it would be fun to record these briefs somewhere for review
  later if I want to use this approach again." Writing the archive's README
  forced the brief structure to be named rather than improvised per lesson, and
  named it in terms of *what each section prevents* — which is a better test of
  whether a section earns its place than "does a brief usually have one." The
  split that fell out: brief = teaching document (`docs/briefs/`), record =
  training state (`CURRICULUM.md` § "Lesson N — record").
- **2026-07-30 — a failing probe is worth more than a passing one.** Both
  Lesson 2 corrections came from measurements that contradicted a plausible
  belief (`request_plus` fixes counts; collection creation needs a readiness
  wait). Probes written to *confirm* an expectation kept confirming it. The
  ones that paid were the ones that varied a condition — count at four
  different delays, six different count strategies — because those show the
  shape of the behaviour rather than one point on it.

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

## Outstanding risk: the PDF ingest path

**2026-07-30 — PyPDF2 replaced by `pypdf`.** PyPDF2 3.0.1 was the project's
final release (renamed to `pypdf`, unmaintained since 2022). The swap is
mechanical and `tests/test_pdf_extraction.py` covers the API surface the
vendored fork uses, but that test reads a PDF it synthesized itself. **Nothing
has run a real ingest against `/Volumes/vault` on this version of the code**,
and the corpus contains scanned, encrypted, malformed and CJK PDFs.

When ingestion becomes runnable again (Lesson 11 wiring, realistically), do one
full ingest of a known-good PDF *before* trusting a batch, and check page text
and title extraction specifically. Details and the provenance note are in
`src/librarian/pageindex/__init__.py`.

## Python SDK gotchas (4.6.2)

Three ways the options objects fail *silently*. All verified by measurement on
this machine, not recalled — re-verify if the pinned version moves.

- **Timeouts must be `timedelta`, and floats fail late.**
  `ClusterTimeoutOptions(bootstrap_timeout=2.0)` constructs happily and even
  shows `{'bootstrap_timeout': 2.0}` under `dict()`. It blows up later, at
  `Cluster(...)`, with `InvalidArgumentException`. `timedelta(seconds=2)`
  works. This is why config keeps seconds-as-floats and the client converts at
  the boundary.
- **`options.timeout_options = ...` does nothing.** `ClusterOptions` is a dict
  subclass; attribute assignment sets a Python attribute nobody reads. It must
  be a constructor kwarg: `ClusterOptions(auth, timeout_options=...)`. Compare
  `dict(options)` before and after to see it — the post-hoc form leaves only
  `{'authenticator': ...}`.
- **`apply_profile(None)` raises** `InvalidArgumentException(<message=None is
  not a registered profile.>)`. The only registered profile is
  `wan_development`. Guard the call; a `None` profile is the local default.
- **Bootstrap is eager.** `Cluster.__init__` → `ClusterImpl` → `ClientAdapter.
  _execute_connect_request()` connects and raises there; it is not deferred to
  first use.

Introspect rather than trusting the docs, which lag the SDK:

```python
from couchbase.options import ClusterTimeoutOptions
print(ClusterTimeoutOptions.__doc__)              # arg list with types
print(ClusterTimeoutOptions._VALID_OPTS.keys())   # what is actually accepted
```

Unrecognized keys are dropped, not rejected — which is why
`tests/integration/test_l01_connect.py::test_configured_timeouts_reach_the_sdk`
measures wall-clock against an unroutable address (TEST-NET-1, `192.0.2.1`)
instead of asserting on the options object.

## Collection management (4.6.2) — measured, Lesson 2

`bucket.collections()` → the SDK's own `CollectionManager`.

- `create_collection(scope_name, collection_name, settings=None, *options)`.
  The `CollectionSpec` overload is deprecated as of 4.1.9 — don't reach for it.
- Exception names, for tests that assert through the MRO:
  `ScopeAlreadyExistsException`, `ScopeNotFoundException`,
  `CollectionAlreadyExistsException`, `CollectionNotFoundException`,
  `BucketNotFoundException`, and `KeyspaceNotFoundException` (SQL++ against a
  collection that doesn't exist).
- **`cluster.bucket("does-not-exist")` raises immediately** (~0.01s,
  `BucketNotFoundException`) — the handle is not lazy.
- **`get_all_scopes()` includes `_system`** (collections `_query`, `_mobile`)
  alongside `_default`. Anything that reports "the collections in this bucket"
  has to filter to the ones it owns.
- **`_default` scope cannot be dropped**: HTTP 400
  `{"errors":{"_":"Deleting _default scope is not allowed"}}`, surfaced as
  `UnsupportedOperation` — a name with no "scope" in it. So "reset = drop the
  scope and recreate" is not a viable general strategy; drop collections.
- **No KV propagation delay on this single-node cluster.** A collection is
  usable for KV upsert ~1ms after `create_collection` returns, on a cold
  cluster handle too. The window is real on multi-node/Capella; don't conclude
  from a local green test that it doesn't exist.

### Counting documents lags, and no option fixes it

Measured immediately after 300 KV upserts into a fresh, unindexed collection:

| query | result |
|---|---|
| `COUNT(*)`, default consistency | 242 / 300 |
| `COUNT(*)`, `request_plus` | 214 / 300 |
| `COUNT(META().id)`, `request_plus` | 241 / 300 |
| `COUNT(*) WHERE META().id IS NOT MISSING`, `request_plus` | 287 / 300 |
| `COUNT(*)` **with a primary index**, `request_plus` | **300 / 300** |

`COUNT(*)` on an unindexed collection is served by a `CountScan` operator
reading collection metadata, which trails the writes; adding a `WHERE` forces
a sequential scan, which is closer but still doesn't participate in
`request_plus`'s mutation-token protocol. **Accurate counts require an index**
— i.e. Lesson 5. It converges in well under a second, so tests poll rather than
asserting instantly (`test_l02_manager.py::test_status_counts_documents`).

Do not spend another session rediscovering that `request_plus` is the fix. It
isn't.

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
- **Editing the healthcheck in docker-compose.yml does nothing to a running
  container.** The healthcheck is baked in at container *creation*, so a
  container that predates the fix keeps failing forever (seen 2026-07-30:
  `Up 5 hours (unhealthy)`, `FailingStreak 3250`, exit code 22 = HTTP >= 400,
  while `curl /ui/index.html` returned 200 by hand). Confirm what the container
  actually has before debugging the server:

  ```bash
  docker inspect --format '{{json .Config.Healthcheck.Test}}' $(docker compose ps -q couchbase)
  ```

  Fix is a recreate, not a restart: `docker compose up -d --force-recreate`.
  The data volume survives; the manual cluster init does not need redoing.

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
CLI and the API. If you do keep one, the fixtures will read `couchbase.password`
from it, so nothing needs exporting. `env-check.sh` checks both places and says
which one it used (it used to look only at the env var, and reported "tests
will skip" on a machine where they were passing).

Gotcha (fixed 2026-07-30): the fixtures interpolate **only** the `couchbase:`
block, not the whole file. `load_config()` resolves every `${VAR}` in
config.yaml, so a file copied from the example used to skip the entire
integration suite over an unset `NANOGPT_API_KEY` — a missing LLM key has
nothing to do with whether a cluster is reachable. See
`_settings_from_config_file` in `tests/integration/conftest.py`.

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
