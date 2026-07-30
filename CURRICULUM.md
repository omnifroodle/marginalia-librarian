# Couchbase Training Curriculum

Matt is a Deployed Customer Engineer at Couchbase. This repo is his training
vehicle: migrate librarian from OpenSearch to Couchbase (local Server EE +
Capella), then survey Capella AI services. Two goals: Matt gets deeply fluent
with Couchbase, and the result is a killer AI-agent demo on the platform.

This file is the **single source of truth for training state**. A fresh agent
session must be able to resume from this file alone: read it, find the first
non-done lesson, and run the loop protocol below.

## ⚠️ THE HARD RULE (survives all context resets)

**Matt writes every line that calls the Couchbase SDK or Capella APIs.**

If a diff you (the agent) are about to write contains `import couchbase`,
`from couchbase`, or an `agentc` call — **stop**. That is Matt's code. Scaffold
the signature and the test instead.

The agent writes: tests, fixtures, stubs (`raise NotImplementedError` bodies),
concept explanations, non-Couchbase wiring (constructor plumbing), and this
file's updates. The agent never "just fixes" Matt's code — feedback, iteration,
and (after ~3 stuck rounds on one point) a *toy* example with different names
that Matt re-applies himself.

Matt writes: all SDK calls (connect, KV, bulk, SQL++, FTS/vector search,
management ops), FTS/vector index definition JSON, the hybrid fusion algorithm,
the `api/errors.py` Couchbase exception mapping, all Capella UI work, config
and connection-string handling, and the Phase 2 design memo.

## The training-loop protocol (per lesson)

1. **BRIEF** — write the concept into this file under the lesson: the Couchbase
   concept, why it maps to this librarian code, official doc links, and the
   exact files/signatures Matt will write.
2. **SCAFFOLD** — create stubs (`NotImplementedError`), the lesson's test
   file(s), fixtures. Run the tests: **all must fail for the right reason**.
   Commit as "Lesson N scaffold".
3. **HANDOFF** — tell Matt: files to edit, the exact pytest command to make
   pass, and 2–3 guiding questions (not answers).
4. **MATT WRITES** — the agent does not touch the stub files. Questions get
   concepts, doc pointers, or parallel toy examples — never the solution.
5. **RUN** — the lesson's tests plus the full suite (regressions).
6. **REVIEW** — against the lesson rubric plus the standing rubric:
   - idiomatic SDK usage (options objects, current APIs, no deprecated calls)
   - error handling: specific exception types, no swallowing, per-key
     inspection on bulk ops
   - durability/consistency choices made consciously and defended in a comment
   - performance: connection/collection reference reuse, batching, streaming,
     no N+1
   - `tests/test_hygiene.py` compliance (no `os.environ` writes, pyflakes-
     clean, no `print()` outside `cli.py`)
   Feedback is file:line-specific with doc links, severity-tagged:
   `[must-fix]` blocks the lesson; `[consider]` may be deferred with a note.
7. **ITERATE** — Matt revises → step 5.
8. **DONE** — tests green, no must-fixes. Update the status table and the
   lesson's record: date, what Matt demonstrated, deferred `[consider]` items,
   and an **explain-back** — Matt's own one-paragraph explanation of the
   concept, recorded verbatim. **Matt commits his own lesson code with his own
   commit message.**

## Status

| Lesson | Title | Status | Date |
|---|---|---|---|
| 0 | Environment: cluster init + Capella checklist | **done** (local; Capella checklist outstanding) | 2026-07-30 |
| 1 | Cluster connection + config plumbing | **done** | 2026-07-30 |
| 2 | Bucket/scope/collection management | pending | |
| 3 | Single-document KV upsert | pending | |
| 4 | Bulk operations | pending | |
| 5 | Delete + re-ingest semantics | pending | |
| 6 | SQL++ reads + scan consistency | pending | |
| 7 | FTS index + BM25 page search | pending | |
| 8 | Multi-field boosted document search | pending | |
| 9 | Vector search | pending | |
| 10 | Client-side hybrid fusion (capstone) | pending | |
| 11 | End-to-end wiring; OpenSearch removal | pending | |
| 12 | Error mapping + failure modes | pending | |
| 13 | Capella deployment | pending | |
| 14 | Capella AI: Model Service embeddings | pending | |
| 15 | Capella AI: auto-vectorization at ingest | pending | |
| 16 | Capella AI: Model Service chat completions | pending | |
| 17 | Agent Catalog + the PageIndex design memo | pending | |

## Ground truth: the code being migrated

All paths relative to this repo. Line numbers are approximate — grep for the
function names.

- **OpenSearch is confined** to `src/librarian/opensearch/` (client factory in
  `client.py`, `DocumentStore` in `store.py`, `IndexManager` in
  `index_manager.py`, pure-data `mappings.py`) plus `src/librarian/query/search.py`
  (`DocumentSearcher`). Client construction sites: `cli.py` (init-indexes,
  status), `ingestion/pipeline.py`, `query/orchestrator.py`, `api/app.py`.
- **The seam already exists**: `src/librarian/backends.py` defines
  `StoreProtocol` and `SearcherProtocol` — the exact surface callers use. The
  `cb/` implementations must satisfy these. The Protocols (and all of
  `opensearch/`) are deleted in Lesson 11.
- **Write path** = 3 store methods: `upsert_document` (single index call, id =
  `doc_id`), `upsert_tree_nodes` (delete-by-query then bulk, id =
  `f"{doc_id}::{node_id}"`, routing = doc_id), `upsert_page_content` (same, id
  adds `::{page_number}`). Composite ids port directly as Couchbase document
  keys. OpenSearch `_routing` has no Couchbase equivalent and needs none
  (vBucket auto-sharding; colocation isn't required).
- **Read path** = `DocumentSearcher.hybrid_search` (dispatches hybrid vs BM25),
  `_hybrid_query` (multi_match over `description^3 / root_summary^2 /
  top_level_titles / collection` + knn on `description_embedding`),
  `_bm25_query`, `search_page_content` (BM25 + highlighting over
  `content_search`), `get_tree_nodes`, `get_page_content`.
  `reconstruct_tree` is pure Python — moves verbatim, not a lesson.
- **Known latent bug (in-code NOTE in `_hybrid_query`)**: filters wrap the BM25
  leg only; the kNN leg is unfiltered. Client-side fusion (Lesson 10) fixes
  this by construction. Enshrine with a regression test.
- **Hybrid fusion today** is a server-side OpenSearch search pipeline
  (`mappings.py hybrid_search_pipeline()`): min_max normalization +
  arithmetic_mean, weights [0.4 BM25, 0.6 kNN]. **No Couchbase equivalent →
  fusion moves client-side** in Lesson 10. `index_manager.py`'s
  `_ensure_pipeline` therefore has NO port — do not look for one.
- **Drop in migration**: `root_summary_embedding` (written at ingest, never
  queried) and the `content`/`content_search` duplication in
  `models.py PageContentRecord.to_os_doc()` (exists only because OpenSearch
  `index:false` needed a parallel analyzed field; FTS index definitions control
  indexing without duplicating data).
- **Embeddings**: `ingestion/embedding.py` (`generate_embedding`,
  `generate_embeddings_batch`) via litellm; model `openai/BAAI/bge-m3` @ 1024
  dims via NanoGPT; dim comes from `config.embedding_dimension`. Embeddings are
  optional — empty `models.embedding` disables the vector path entirely.
  Query-time embedding in `query/orchestrator.py _discover_docs` degrades
  non-fatally to BM25-only on failure. **Models stay on NanoGPT through all of
  Phase 1** — one variable at a time; Capella AI swaps start in Lesson 14.
- **Model access** is all litellm with a three-way endpoint split in
  `config.py` (`litellm_api_base/key` → pageindex + phase-2 shortlist;
  `embedding_api_base/key`; `reasoning_api_base/key` → phases 3/4). Nine call
  sites; the pageindex gateway is configured once via `configure_llm()` at the
  process edge (`ingestion/pipeline.py`).
- **Trap for Phase 2**: the phase-3 agentic loop (`query/tree_reasoner.py`,
  `_select_nodes_agentic`) calls `litellm.completion` with tools and
  `allowed_openai_params`, and **silently falls back to one-shot on ANY
  exception** — a broken model swap degrades quality quietly. Lesson 16 adds
  loud detection before any endpoint flip.
- **Error mapping**: `api/errors.py` maps `opensearchpy`
  ConnectionError/Timeout → SSE code `search_backend_unavailable` and
  classifies litellm exceptions. `llm_activity.py` registers litellm
  success/failure callbacks — useful as a test sensor.
- **Tests**: all existing tests are fake-based (`test_api.py`'s FakeOpenSearch,
  `test_tree_reasoner.py`'s FakeSearcher) — zero live-backend tests. The
  integration suite Matt builds IS the point. `tests/test_hygiene.py` is
  binding on all new code. Integration harness: `tests/integration/` +
  `pytest -m integration` (default runs exclude it; conftest skips cleanly
  when no cluster is reachable on :8091).
- **HTTP contract**: `docs/api.md` — the marginalia reader consumes only this
  (SSE vocabulary, error codes). It must not change observably.

## Target architecture

New module `src/librarian/cb/` (named `cb/` to avoid clashing with the SDK's
top-level `couchbase` package): `client.py`, `manager.py`, `store.py`,
`search.py`, `indexes/` (FTS index definition JSON). Data model: one bucket
(`librarian`), one scope, collections `documents` / `tree_nodes` /
`page_content`. Both environments from day one: local Docker EE (inner loop)
and Capella (`couchbases://` + TLS), selected by config.

---

# Phase 1 — Couchbase core (Lessons 0–13)

## Lesson 0 — Environment (Matt, manual; agent verifies)

`docker compose up -d`, then Matt initializes the cluster by hand via the web
UI at http://localhost:8091 (services: Data/Query/Index/Search; memory quotas;
admin credentials; bucket `librarian`). Deliberately manual — cluster init is
bread-and-butter DCE work. Separately: run the Capella checklist in
`docs/capella-setup.md` (provisioned now, first used in Lesson 13). Agent
verifies: `pytest -m integration tests/integration/test_harness.py` passes.

### Running the suites

Tests carry a `lesson(n)` marker; `--lesson N` runs everything through lesson N
(untagged pre-migration tests always run — they are the standing baseline).

```bash
./training-tools/env-check.sh                       # is the machine ready?
pytest tests/ -q                                    # unit
pytest -m integration -q                            # live cluster
pytest -m integration --lesson 1 -q                 # "what should be green by now"
```

## Lesson 1 — Cluster connection + config plumbing

- **Concept**: `couchbase://` vs `couchbases://` connection strings,
  `PasswordAuthenticator`, `ClusterOptions`, timeout profiles
  (`apply_profile("wan_development")` for Capella later), `wait_until_ready`
  vs `ping()`/`diagnostics()`.
- **Matt writes**: `cb/client.py` — `create_cluster(config) -> Cluster`, the
  analogue of `opensearch/client.py create_client`. Handles both connection
  styles from day one via config. Also adds `couchbase` to `pyproject.toml`
  dependencies (his first SDK decision: version pin).
- **Agent scaffolds**: a `couchbase:` config section (connection_string,
  username, password via `${ENV_VAR}` interpolation, bucket, scope, timeout
  knobs) with accessor properties on `Config`, mirroring the existing style;
  stub `cb/client.py`; `tests/integration/test_connect.py`.
- **Acceptance**: integration test connects to the local cluster, ping shows
  kv/query/search healthy; a bad-credentials test asserts
  `AuthenticationException` surfaces (not swallowed).
- **Rubric**: one cluster per process (this later consolidates the multiple
  per-call-site client constructions); timeouts via options, not sleeps; no
  credentials in code or git.

## Lesson 2 — Bucket/scope/collection management

- **Concept**: buckets vs scopes vs collections vs OpenSearch indexes;
  `CollectionManager`; idempotent creation (exception-driven vs
  check-then-create — discuss); why bucket creation on Capella belongs to the
  control plane.
- **Matt writes**: `cb/manager.py` — `ensure_collections()`, `reset()`,
  `status()`, replacing `opensearch/index_manager.py`. (Reminder: the search
  pipeline in `_ensure_pipeline` has NO port — client-side fusion, Lesson 10.)
- **Agent scaffolds**: stubs + `tests/integration/test_manager.py`
  (create/reset roundtrip; calling `ensure_collections()` twice is a no-op).
- **Acceptance**: collections exist after ensure; reset drops and recreates;
  double-ensure clean.
- **Rubric**: idempotency approach reasoned; no bare `except Exception`;
  `status()` output usable by the CLI `status` command.

## Lesson 3 — Single-document KV upsert

- **Concept**: KV as Couchbase's primary access path — the core mental-model
  shift from OpenSearch, where everything goes through a search index. Document
  keys; upsert vs insert vs replace; durability levels (when
  `Durability.MAJORITY` is worth it); JSON transcoding of pydantic models.
- **Matt writes**: `cb/store.py` — `upsert_document` (Protocol-conformant; see
  `backends.StoreProtocol`). Key = `doc_id`. Embedding stored as a plain float
  array. **Drop `root_summary_embedding`** (never queried).
- **Agent scaffolds**: `cb/store.py` stub; `tests/integration/test_store.py::
  test_upsert_document_roundtrip` (upsert → KV get → field-by-field compare
  against a `DocumentRecord` fixture); an upsert-twice-overwrites test.
- **Acceptance**: roundtrip green; double upsert overwrites cleanly.
- **Rubric**: `.model_dump()` at the boundary, no hand-rolled dicts; durability
  choice defended in a comment; bucket/scope/collection references held, not
  re-looked-up per call.

## Lesson 4 — Bulk operations

- **Concept**: `upsert_multi` and per-key result inspection — bulk ops don't
  raise a single exception; every key has its own outcome. Batching strategy.
  Why OpenSearch `_routing` needs no equivalent (vBucket auto-sharding).
- **Matt writes**: `upsert_tree_nodes` and `upsert_page_content` in
  `cb/store.py`. Composite keys unchanged: `f"{doc_id}::{node_id}"`,
  `f"{doc_id}::{node_id}::{page_number}"`.
- **Agent scaffolds**: a 500-node bulk test with count verification via direct
  KV gets on computed keys; a partial-failure test (one poison record) that
  asserts Matt's code detects and reports *which* keys failed rather than
  silently succeeding.
- **Acceptance**: both green.
- **Rubric**: per-key error inspection, not "did the call throw"; sensible
  batch sizing; not N sequential single upserts.

## Lesson 5 — Delete + re-ingest semantics

- **Concept**: there is no KV delete-by-query. Options: SQL++ `DELETE ...
  WHERE doc_id = $doc_id` (needs a GSI index), key-set deletion when keys are
  enumerable, collection drop. Rethink — don't transliterate — the OpenSearch
  delete-by-query-then-bulk pattern: node sets change between ingests, so
  deterministic keys alone don't find orphans.
- **Matt writes**: `delete_document_artifacts(doc_id)` in `cb/store.py` plus
  his first `CREATE INDEX` statements (GSI on `doc_id` for `tree_nodes` and
  `page_content`), issued from `cb/manager.py`.
- **Agent scaffolds**: the orphan test — ingest tree A (10 nodes), re-ingest
  tree A′ (7 nodes, 3 renamed), assert exactly 7 remain; delete of a
  nonexistent doc_id is a clean no-op.
- **Acceptance**: both green.
- **Rubric**: parameterized queries only — say "SQL++ injection" out loud;
  index-backed, not a primary-index scan; mutation visibility understood
  (foreshadows Lesson 6).

## Lesson 6 — SQL++ reads + scan consistency

- **Concept**: the Query service; named parameters; `query_context` / scoped
  queries; **scan consistency** (`not_bounded` vs `request_plus` — the
  read-your-own-writes question is core DCE consulting material); streaming
  result iteration.
- **Matt writes**: `get_tree_nodes(doc_id)` and `get_page_content(doc_id,
  node_ids)` in `cb/search.py` (see `backends.SearcherProtocol`). The agent
  moves `reconstruct_tree` verbatim (pure Python, not a lesson).
- **Agent scaffolds**: parity tests — same fixture doc, results satisfy the
  shapes the funnel expects (the FakeSearcher surface in
  `tests/test_tree_reasoner.py` documents that contract); an
  ingest-then-immediately-read test that forces the consistency conversation.
- **Acceptance**: parity + immediate-read green.
- **Rubric**: consistency choice defended in a comment; streamed iteration;
  named params; discuss the KV multi-get alternative (keys are computable!) —
  either implementation accepted if the reasoning is sound.

## Lesson 7 — FTS index + BM25 page search

- **Concept**: the Search (FTS) service; scoped index definitions as JSON;
  analyzers; per-collection type mappings; highlighting; index-time field
  control — which makes the `content`/`content_search` duplication
  unnecessary. **Drop `content_search`** from `models.py` (agent handles the
  model change; the index definition is Matt's).
- **Matt writes**: `cb/indexes/page_content_fts.json` + `search_page_content`
  in `cb/search.py` (BM25 + highlighting, optional doc_id scope) + index
  upload via `ScopeSearchIndexManager.upsert_index` (SDK surface = Matt's).
- **Agent scaffolds**: a tiny fixture "book" with known phrases; relevance
  tests (exact phrase ranks first; highlight fragments present); a poll-based
  wait-for-index-ready helper (no sleeps); index-definition sanity checks.
- **Acceptance**: relevance + highlight + doc_id-scoped tests green.
- **Rubric**: minimal index (only needed fields indexed; only `content` stored
  for snippets); index build latency handled by polling; scoped index, not
  legacy global.

## Lesson 8 — Multi-field boosted document search

- **Concept**: compound FTS queries — a disjunction of boosted match queries
  replacing OpenSearch `multi_match` over `description^3 / root_summary^2 /
  top_level_titles / collection`; conjunctions for filters (non-scoring).
- **Matt writes**: `cb/indexes/documents_fts.json` (text fields now; the
  vector field is added in Lesson 9) + the BM25 leg of document search in
  `cb/search.py`, filters included.
- **Agent scaffolds**: behavior tests with a small labeled corpus
  (title/description match beats body match; filter excludes correctly).
- **Acceptance**: ranking behavior green.
- **Rubric**: boosts in the query, not the index; filters as non-scoring
  conjuncts; field selection anticipates Lesson 10.

## Lesson 9 — Vector search

- **Concept**: vector fields inside FTS indexes (Server 7.6+ Enterprise);
  dimension from config (1024 today); similarity metric — OpenSearch used
  `cosinesimil`; use cosine if the running server supports it, else
  normalize + dot-product. **Matt verifies empirically and documents which.**
  `VectorSearch` / `VectorQuery` in the Python SDK.
- **Matt writes**: the vector field addition to `documents_fts.json` + the kNN
  leg querying `description_embedding` in `cb/search.py`.
- **Agent scaffolds**: synthetic-embedding fixtures (hand-built vectors with
  known nearest neighbors — no LLM calls); NN-ordering test; a dim-mismatch
  test (wrong-length vector must fail loudly, not return empty).
- **Acceptance**: ordering + dim-mismatch green.
- **Rubric**: dimension sourced from config, never hard-coded; metric stated
  and justified; k / candidate-count choices reasoned.

## Lesson 10 — Client-side hybrid fusion (capstone query lesson)

- **Concept**: why the server-side normalization pipeline has no Couchbase
  equivalent; fusion algorithms — weighted min-max vs RRF, trade-offs; the
  payoff: the old filters-only-on-BM25 bug is fixed *by construction* when
  both legs run explicitly with the same filters.
- **Matt writes**: `hybrid_search` in `cb/search.py` — run the Lesson 8 BM25
  leg and Lesson 9 vector leg (both filtered), fuse in pure Python. Matt picks
  min-max-weighted or RRF and defends the choice; weights/params from config.
- **Agent scaffolds**: pure-unit fusion-math tests (fixed score sets → exact
  expected fused ranking, with both algorithms as cases so Matt can compare);
  integration test: a doc matching both legs outranks single-leg matches;
  **a regression test proving filters constrain BOTH legs**; a
  no-embedding-degrades-to-BM25-only test (matching what
  `orchestrator._discover_docs` expects — non-fatal).
- **Acceptance**: all groups green.
- **Rubric**: fusion is a pure, separately unit-tested function; empty-leg and
  single-result normalization edge cases handled; weights from config.

## Lesson 11 — End-to-end wiring; OpenSearch removal

- **Agent writes (Matt reviews)**: rewire the client construction sites
  (`cli.py`, `ingestion/pipeline.py`, `query/orchestrator.py`, `api/app.py`)
  to `cb/`; fix the pipeline's hardwired store (inject it — tests currently
  reassign `_store` after construction); confirm the phase-3 teleport tools
  flow through the searcher unchanged; delete `src/librarian/opensearch/`,
  the `opensearch-py` dependency, and `backends.py` (the transition seam);
  update fakes (`test_api.py`, `test_tree_reasoner.py`) to duck-type the `cb`
  surface.
- **Matt writes**: nothing — a guided comprehension review. Matt narrates every
  backend touch through the four-phase funnel; the agent probes.
- **Acceptance**: full unit + integration suites green; `librarian ingest` and
  `librarian query` work end-to-end locally.

## Lesson 12 — Error mapping + failure modes

- **Concept**: the Couchbase exception hierarchy; **ambiguous vs unambiguous
  timeouts** — the most DCE-relevant SDK distinction (did the mutation
  possibly land?); which exceptions mean "backend unreachable" vs "bad
  request".
- **Matt writes**: rework `api/errors.py` — replace the `opensearchpy`
  mapping with `couchbase.exceptions` equivalents (`AmbiguousTimeoutException`,
  `UnAmbiguousTimeoutException`, `AuthenticationException`,
  `ServiceUnavailableException`, base `CouchbaseException`) → the existing SSE
  code vocabulary (`search_backend_unavailable` etc.); same treatment in
  `cli.py`.
- **Agent scaffolds**: extend `tests/test_errors.py` per exception class; a
  live container-down test (agent-managed fixture stops the container) —
  the API must return the SSE error, not a 500 traceback.
- **Acceptance**: all green, including container-down.
- **Rubric**: ambiguous timeout on a write path discussed explicitly (why do
  idempotent upserts save us? Matt must answer); no swallowing; the SSE
  contract in `docs/api.md` unchanged from the reader's perspective.

## Lesson 13 — Capella deployment

- **Concept**: `couchbases://` + TLS in practice; credential roles; allowed
  IPs; WAN latency and the `wan_development` timeout profile; what the SDK
  can/can't manage on Capella vs self-managed.
- **Matt writes**: `config.capella.yaml` (gitignored) + any `create_cluster`
  adjustments; FTS/vector index creation against Capella via his Lesson 7/9
  code (proving environment portability); full demo-book ingest into Capella.
- **Agent scaffolds**: environment-parameterized integration fixture
  (`LIBRARIAN_TEST_ENV=local|capella`) so the same suite runs against both.
- **Acceptance**: full integration suite green against Capella; a query
  answered from Capella-hosted data.
- **Rubric**: no TLS-verification disabling; WAN-appropriate timeouts;
  credentials via env/config, never committed.

---

# Phase 2 — Capella AI survey (Lessons 14–17)

Grounding: all model access is litellm behind the three-way endpoint split;
Capella Model Service is OpenAI-compatible, so endpoint swaps are mostly
config — the learning is on the Capella side. Confirm before starting: Matt's
Capella tier includes AI Services (Model Service / Vectorization / Agent
Catalog availability varies by tier and region).

## Lesson 14 — Model Service: embeddings (config-only swap)

- **Matt does**: deploy an embedding model in Capella AI (UI: model selection,
  sizing, endpoint provisioning); point `embedding_api_base/key` at it;
  determine the model's dimensionality. If ≠ 1024: update config dim, update
  the vector index definition, re-ingest — a real "the embedding model
  changed" drill.
- **Agent scaffolds**: a dim-guard test (embedding length must equal config
  dim — makes mismatch loud); before/after retrieval smoke comparison on the
  demo book.
- **Rubric**: Matt explains *why zero librarian code changed* (litellm +
  config split + OpenAI-compatible endpoint).

## Lesson 15 — Auto-vectorization at ingest

- **Concept**: Capella's Vectorization service embeds documents server-side on
  write. **Query-time embedding stays client-side** — the query vector still
  has to come from somewhere. Matt must articulate this asymmetry.
- **Matt does**: configure a vectorization workflow on `documents` in the
  Capella UI; then write the ingest-path change in `cb/store.py`: skip
  client-side `generate_embedding` when `vectorization_enabled`, adapt the
  document shape / vector field name to what the workflow emits.
- **Agent scaffolds**: integration test asserting ingest completes with zero
  embedding API calls (the `llm_activity` litellm callbacks are the sensor)
  while vector search still returns results after the async workflow catches
  up (polling helper).
- **Deliverable**: Matt writes a short keep-vs-revert recommendation —
  evaluating exactly this trade-off for a customer is the DCE job. The
  decision is deliberately left open.

## Lesson 16 — Model Service: chat completions

- **Matt does**: deploy an LLM in Model Service; repoint
  `reasoning_api_base` / `litellm_api_base` per the config split; exercise the
  full funnel.
- **Critical agent scaffold FIRST**: the phase-3 agentic loop silently falls
  back to one-shot on any exception — a broken swap degrades quietly. Before
  Matt flips any endpoint, add loud detection: an integration assertion (via
  `llm_activity` callbacks) that the tool-calling path actually executed with
  tools, plus log escalation on fallback. (Instrumentation = agent work.)
- **Also verify**: litellm exception classification in `api/errors.py` still
  maps sensibly for Model Service error shapes; tool-calling /
  `allowed_openai_params` compatibility of the chosen model.
- **Rubric**: Matt explains Model Service value-adds (caching, guardrails,
  in-VPC keyless access) as if to a customer.

## Lesson 17 — Agent Catalog + the PageIndex design memo (capstone)

- **Concrete exercise**: the phase-3 teleport tools (`_handle_search_corpus`,
  `_handle_search_pages` in `query/tree_reasoner.py`) are hand-rolled tool
  definitions today. Register them as Agent Catalog (`agentc`) tools and have
  the agentic loop fetch tool/prompt definitions from the catalog.
- **Matt writes**: the `agentc` registrations + the loop's catalog
  integration; then the deliverable he's especially here for — a design memo:
  **"PageIndex on Couchbase: indexers and searchers as Agent Catalog
  agents"** — how the vendored PageIndex fork's tree building (indexer) and
  the four-phase funnel (searcher) would decompose into cataloged
  tools/prompts/agents, what Couchbase-native tree storage buys, where the
  catalog's versioning/observability helps.
- **Agent scaffolds**: agentc project setup harness; an integration test that
  the loop's tools resolve from the catalog; a memo outline.
- **Acceptance**: tools resolve from the catalog; the memo exists and is
  reviewed. Prototype depth is time-boxed — the memo is the artifact.

---

# End-to-end verification (after Lesson 13; repeat after Phase 2)

1. `docker compose up -d` → collections/indexes via Matt's manager code →
   `librarian ingest` a small demo EPUB (real PageIndex + embeddings).
2. `librarian query "..."` — all four funnel phases ran (hybrid → shortlist →
   agentic with ≥1 teleport → notes); `llm_activity` logs are the sensor.
3. Boot the API; point the marginalia reader at it (`LIBRARIAN_URL` in the
   marginalia repo); open the demo book, ask a question, citations render.
   Kill the container mid-query → the reader shows the
   `search_backend_unavailable` copy, not a broken stream.
4. Repeat 1–3 against Capella with `config.capella.yaml`.

# Lesson records

(Appended by the agent as lessons complete — BRIEF content, DONE record,
explain-backs, deferred items.)

## Lesson 0 — record (2026-07-30)

Local cluster initialized by hand via the web UI: services Data/Query/Index/
Search, bucket `librarian`, admin credentials held only in the environment.
Verified by `pytest -m integration tests/integration/test_harness.py` (passes)
and `training-tools/env-check.sh`.

Two artifacts came out of it, both in `NOTES.md`:

- The compose healthcheck was wrong. It probed `/pools`, which returns 200 on
  an *uninitialized* node and 401 once credentials exist, so `curl -sf` marked
  a perfectly healthy initialized cluster `unhealthy` forever. Now probes
  `/ui/index.html`. **This doubles as the fastest "is the cluster initialized?"
  check there is:** `curl -o /dev/null -w '%{http_code}' :8091/pools` →
  200 = wizard waiting, 401 = initialized.
- The `nofile` warning in the container logs (wants 200000, gets 40960) is
  benign for a dev workload; compose already matches Couchbase's own published
  `docker run` line.

**Still outstanding:** the Capella checklist in `docs/capella-setup.md`.
Provisioned now, first *used* in Lesson 13 — so it doesn't block Lesson 1, but
don't let it slip past Lesson 12.

## Lesson 1 — BRIEF (2026-07-30)

### Why this lesson exists

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

### Concepts to have straight before you write

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

### Docs

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

### Files

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

### Acceptance

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

### Rubric

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

### Guiding questions (answer in comments or in the explain-back)

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

## Lesson 1 — record (2026-07-30)

**Done.** `316eb78` — `src/librarian/cb/client.py::create_cluster`,
`couchbase` added to `pyproject.toml` `[project] dependencies`.
120 unit passed, 5 integration passed, `cb/` pyflakes-clean.

Decisions Matt made:

- **Waits.** `wait_until_ready(bootstrap_timeout, WaitUntilReadyOptions(
  service_types=[KeyValue, Query, Search]))` — the factory blocks until the
  cluster is usable, so a cold or misconfigured cluster fails once at startup
  rather than scattered across the first N operations. `bootstrap_timeout`
  serves as both the SDK handshake budget and the readiness budget, so the knob
  has one meaning. Consequence to revisit in Lesson 13: this hard-requires the
  Search service on every deployment.
- **Profile beats explicit timeouts.** A `timeout_profile` is a complete
  policy; setting it makes the `timeouts:` block inert rather than merging.
  Rationale is recorded in `config.example.yaml` (letting per-service values
  partially override a WAN profile is how you get LAN timeouts over a WAN).
- **Version floor** on the SDK rather than a pin, matching the rest of
  `pyproject.toml`.
- Explain-back was considered but deliberately not written into code comments.
  The precedence rule survives in `config.example.yaml`; the readiness and
  auth-timing findings are in `NOTES.md` § "Python SDK gotchas (4.6.2)".

Agent-side defects this lesson surfaced (all fixed, all logged in NOTES.md):

- `ServiceType` enum values asserted from memory (`kv`/`n1ql`/`fts`) rather
  than introspected (`key_value`/`query`/`search`) — failed Matt's correct code.
- Rubric items that no test enforced, so a wrong implementation went green.
- `git add -A` swept Matt's in-progress file into an agent commit.
- The integration fixture called `load_config()`, so an unset `NANOGPT_API_KEY`
  skipped the whole suite over an unrelated config key.

