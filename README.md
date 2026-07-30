# marginalia-librarian

Generic book-retrieval engine: ingests books into PageIndex trees and answers
questions with a **citation list** instead of a synthesized answer, via a
four-phase funnel (hybrid search → LLM shortlist → agentic tree reasoning →
per-citation liner notes). Serves a CLI and an HTTP/SSE API
(see `docs/api.md`) consumed by the [marginalia](../marginalia) reader.

Extracted from the marginalia monorepo. Currently migrating the storage/search
backend from OpenSearch to **Couchbase** (local Server EE + Capella) as a
hands-on training project — see `CURRICULUM.md` for the program and current
state, and `CLAUDE.md` for agent ground rules.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[api,dev]"
python -m pytest tests/ -q        # unit tests
docker compose up -d              # local Couchbase (one-time UI init — see compose header)
python -m pytest -m integration   # live-cluster tests
```

Config: copy `config.example.yaml` → `config.yaml`; set `NANOGPT_API_KEY` in
the env. Capella setup: `docs/capella-setup.md`.
