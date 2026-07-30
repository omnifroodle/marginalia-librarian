# marginalia-librarian

Generic Python retrieval engine, extracted from the marginalia project:
PageIndex tree ingestion, a four-phase citation funnel (hybrid search → LLM
shortlist → agentic tree reasoning → per-citation note generation), CLI +
FastAPI/SSE adapters. The marginalia reader (separate repo,
`~/Documents/code/marginalia`) consumes this over HTTP only; the wire contract
is `docs/api.md` and must not change observably.

## ⚠️ This repo is a training project — read CURRICULUM.md first

We are migrating OpenSearch → Couchbase (local Server EE + Capella) as a
hands-on Couchbase curriculum for Matt (a Couchbase Deployed Customer
Engineer). **`CURRICULUM.md` is the source of truth** for training state, the
lesson plan, and the loop protocol. Resume from it at session start.

**The hard rule:** Matt writes every line that calls the Couchbase SDK or
Capella APIs. If a diff you are about to write contains `import couchbase`,
`from couchbase`, or an `agentc` call — stop. Scaffold the signature and the
test instead. You write tests, fixtures, stubs, explanations, and
non-Couchbase plumbing. You review Matt's code against the lesson rubric; you
never "just fix it".

## Commands

```bash
source .venv/bin/activate          # plain venv + pip, no uv
pip install -e ".[api,dev]"
python -m pytest tests/ -q         # unit tests (integration excluded by default)
python -m pytest -m integration    # live-Couchbase tests (needs docker compose up)
librarian init-indexes | status | ingest PATH [--force] | query "..." [--json] | serve [--port]
docker compose up -d               # local Couchbase Server EE (see file header
                                   # for the one-time manual cluster init)
```

Config: `config.yaml` (gitignored; copy from `config.example.yaml`). Needs
`NANOGPT_API_KEY` in the env. Corpus root: `library.vault_root` (requires
`/Volumes/vault` mounted for ingestion). First CLI/pytest run after a fresh
venv is slow (litellm import); sub-second after.

## Architecture notes for the migration

- Backend touchpoints are confined to `src/librarian/opensearch/` +
  `src/librarian/query/search.py`. The transition seam is
  `src/librarian/backends.py` (`StoreProtocol` / `SearcherProtocol`) — the new
  `src/librarian/cb/` implementations must satisfy it. Both `opensearch/` and
  `backends.py` are deleted in Lesson 11.
- `CURRICULUM.md` § "Ground truth" documents the write/read paths, the
  composite-key scheme, the hybrid-fusion pipeline that has no Couchbase
  equivalent (fusion moves client-side), and the traps (silent phase-3
  fallback, filter bug, fields to drop).
- Core modules return structured data + emit typed events via `on_event`
  callbacks; CLI/API are thin adapters. Match this pattern.

## Environment gotchas

- **This repo lives in iCloud-synced ~/Documents.** If a process hangs in a
  blocking read (0 CPU, no network), suspect iCloud eviction:
  `brctl download <path>`, then restart. Fresh venvs are the usual victims.
- The old marginalia OpenSearch data still lives on the shared cluster at
  localhost:9200 (belongs to another project's docker stack — never touch the
  unprefixed indexes). This repo's Couchbase compose stack is independent of
  it; OpenSearch code leaves this repo entirely at Lesson 11.
- Capella access: see `docs/capella-setup.md`. Capella credentials live in
  `config.capella.yaml` (gitignored) / env vars — never in git.

## Code rules (enforced by tests/test_hygiene.py — binding on cb/ too)

- No `os.environ` writes; pass api_base/api_key/credentials explicitly via
  config (pageindex fork: `configure_llm()` once at process edge).
- No `print()` outside `cli.py`; library code uses `logging`, JSONL
  diagnostics only under configured `logging.dir`.
- Core modules return structured data + typed `on_event` events; adapters stay
  thin. Pyflakes-clean.

## Provenance

Extracted 2026-07-30 from `~/Documents/code/marginalia` (`librarian/`
subtree; full history remains there). The vendored PageIndex fork is
`src/librarian/pageindex/`; local changes vs upstream are listed in its
`__init__.py`.
