# Lesson 1 — Cluster connection + config plumbing

*Delivered 2026-07-30. Implementation commit `316eb78`. Originally written into
`CURRICULUM.md`'s lesson records; moved here when the brief archive was created,
unedited. The outcome — what Matt decided and what it cost — stays in
`CURRICULUM.md` § "Lesson 1 — record".*

---

## Why this lesson exists

Every backend touchpoint in librarian starts with "get me a connection". Today
that is `opensearch/client.py::create_client` — 14 lines that turn `Config`
into an `OpenSearch` object, called from four separate sites (`cli.py`,
`ingestion/pipeline.py`, `query/orchestrator.py`, `api/app.py`). The Couchbase
equivalent looks similar and behaves very differently, and the differences are
the lesson:

| | OpenSearch | Couchbase |
|---|---|---|
| What the object is | a stateless HTTP client; "connecting" is lazy | a live, stateful cluster connection with bootstrap, config push, and per-service connection pools |
| Cost of constructing one | negligible | real — bootstrap handshake, cluster map fetch, background threads |
| Correct number per process | doesn't much matter | **one** |
| TLS/Capella | a `use_ssl` flag | the `couchbases://` scheme, which changes bootstrap, ports, and cert handling |
| "Is it up?" | first request finds out | `ping()` / `diagnostics()` / `wait_until_ready()`, and they mean different things |

The "one per process" line is why Lesson 11 gets to consolidate those four
construction sites — but the constraint starts here.

## Concepts to have straight before you write

1. **Connection string scheme.** `couchbase://host` is plaintext; `couchbases://host`
   is TLS and is what Capella requires. It is not merely a flag: the scheme
   changes which ports are used and brings certificate verification into play.
   One knob (`config.couchbase_connection_string`) must be enough to move
   between local Docker and Capella — no `if capella:` anywhere.
2. **`PasswordAuthenticator` + `ClusterOptions`.** Credentials are an
   authenticator object, not kwargs. `ClusterOptions` carries the
   authenticator plus timeouts, transcoders, tracing.
3. **Timeouts, and `apply_profile`.** `ClusterTimeoutOptions` sets per-service
   timeouts (kv, query, search, connect…). `ClusterOptions.apply_profile(
   "wan_development")` bulk-relaxes them for high-latency links — the Capella
   answer in Lesson 13. Config already carries both the explicit seconds and
   an optional profile name; decide how they interact when both are present,
   and say why in a comment.
4. **`wait_until_ready()` vs `ping()` vs `diagnostics()`.** These are three
   different questions:
   - `wait_until_ready(timeout, services)` — *block until usable*. Active: it
     probes.
   - `ping(services)` — *actively probe right now*, returns per-endpoint
     latency and state. Costs a round trip per endpoint.
   - `diagnostics()` — *what does the SDK already believe* about its existing
     sockets. Passive, no round trip, can report stale state.
   The acceptance tests use `ping()`. What `create_cluster` uses internally is
   your call — but "the returned cluster is usable immediately" must be true
   without the caller sleeping.

## Docs

- Start here — Python SDK "Managing Connections":
  <https://docs.couchbase.com/python-sdk/current/howtos/managing-connections.html>
- Connection strings and options:
  <https://docs.couchbase.com/python-sdk/current/ref/client-settings.html>
- Health check APIs (ping / diagnostics / wait_until_ready):
  <https://docs.couchbase.com/python-sdk/current/howtos/health-check.html>
- Capella / TLS specifics (skim now, use in Lesson 13):
  <https://docs.couchbase.com/python-sdk/current/howtos/managing-connections.html#connecting-to-capella>
- Release notes / current version, for your pin:
  <https://docs.couchbase.com/python-sdk/current/project-docs/sdk-release-notes.html>

## Files

**You write:**

- `pyproject.toml` — add `couchbase` to `[project] dependencies`. Your first
  SDK decision: pick and justify the version constraint. (Note for the rubric:
  the codebase pins `>=` floors elsewhere. Vector search in Lesson 9 needs a
  4.x SDK against Server 7.6+; check what the current 4.x line is rather than
  copying a number from a blog post.) Then `pip install -e ".[api,dev]"`.
- `src/librarian/cb/client.py` — implement `create_cluster(config) -> Cluster`.
  Replace the `Any` return annotation with the real type once you import it.

**Already scaffolded (agent):**

- `src/librarian/config.py` — the `couchbase:` block and its accessors:
  `couchbase_connection_string`, `_username`, `_password`, `_bucket`, `_scope`,
  `_connect_timeout`, `_kv_timeout`, `_query_timeout`, `_search_timeout`,
  `_timeout_profile`, `_cert_path`. Timeouts are **seconds as floats** so
  config stays free of SDK types — you convert to `timedelta` at the boundary.
  Plus `COUCHBASE_CONNECTION_STRING` / `_USERNAME` / `_PASSWORD` env overrides.
- `config.example.yaml` — the documented `couchbase:` block.
- `tests/integration/conftest.py` — `couchbase_settings` (raw dict) and
  `cb_config` (a `Config`) fixtures, built from `LIBRARIAN_TEST_COUCHBASE_*`
  env vars so the suite needs no `config.yaml`.
- `tests/integration/test_l01_connect.py` — the three acceptance tests.
- `tests/test_config.py` — four passing unit tests for the config plumbing.

*Scoping note:* the top of this file lists "config and connection-string
handling" as Matt's, while Lesson 1's own scaffold list assigns the config
accessors to the agent. I followed the lesson-specific list, reading the
top-level line as being about how `create_cluster` *consumes* config. Say so if
you'd rather own the accessors too.

## Acceptance

```bash
export LIBRARIAN_TEST_COUCHBASE_PASSWORD=...   # see NOTES.md
pytest -m integration tests/integration/test_l01_connect.py -q
pytest tests/ -q                               # no regressions (117 expected)
```

1. `test_create_cluster_opens_the_configured_bucket` — cluster is usable the
   moment it is returned.
2. `test_ping_reports_core_services_healthy` — `kv`, `n1ql`, `fts` all present
   and `ok`.
3. `test_bad_credentials_are_not_swallowed` — a wrong password reaches the
   caller as `AuthenticationException`.

Note that the test file contains no `import couchbase`: SDK identities are
asserted by class name via `__mro__` and by enum `.value`. That is the hard
rule applied to the scaffold itself — every SDK import in this repo is yours.

## Rubric

- **One cluster per process.** Constructing a `Cluster` is expensive and
  stateful. Even though Lesson 11 does the actual consolidation, `create_cluster`
  must not encourage per-call construction.
- **Timeouts through options, never `sleep`.** No retry loops, no polling with
  a delay.
- **No credentials in code or git.** They come from `Config`, which interpolates
  `${VAR}`.
- **Nothing swallowed.** No bare `except Exception`. `AuthenticationException`
  must reach the caller unwrapped.
- **No `if capella:`.** The connection string is the only switch.
- `tests/test_hygiene.py` stays green: no `os.environ` writes, no `print()`,
  pyflakes-clean.

## Guiding questions (answer in comments or in the explain-back)

1. `wait_until_ready` or nothing? If `create_cluster` returns without waiting,
   who pays — and what does the first caller see on a cold cluster? If it does
   wait, which services does it wait for, and what happens on Capella when one
   of them isn't deployed?
2. Config offers both explicit timeouts *and* an optional `timeout_profile`.
   When both are set, which wins, and why is that the safer default?
3. Bad credentials: at which call does the SDK actually notice — construction,
   bucket open, or first operation? Try it. What does that tell you about when
   a customer's "it connected fine, then everything timed out" ticket is really
   an auth problem?
