# Librarian HTTP API

The wire contract for consumers (e.g. the marginalia reader). Run with
`librarian serve [--host --port]`; defaults come from the `api:` config block
(`127.0.0.1:8000`). No authentication — the API is expected to run on
localhost or behind something that provides it. CORS origins come from
`api.cors_origins` (default `["*"]`).

Interactive docs: FastAPI serves Swagger UI at `/docs` and the machine-readable
spec at `/openapi.json`. The JSON response shapes below are enforced by the
models in `src/librarian/api/schemas.py` — extra fields from the index are
filtered out, so the spec is authoritative. The SSE stream (`POST /search`)
cannot be expressed in OpenAPI; this file is its only contract.

Errors on the JSON endpoints use FastAPI's standard envelope:
`{"detail": "<message>"}` with a 4xx status. Request-validation failures
return 422 with FastAPI's usual detail list.

---

## GET /health

Liveness + OpenSearch reachability, for container healthchecks and smoke
tests. `200 {"status": "ok", "opensearch": true, "index_prefix": "…"}` when
OpenSearch answers a ping; `503` otherwise. Deliberately does **not** check
the LLM key — a keyless deployment still serves browsing and reading, and
the first search reports `llm_auth` through the SSE error event instead.

---

## POST /search

Runs the four-phase citation funnel and streams progress as Server-Sent
Events. The response is `text/event-stream`; every event is a single
`data: <json>` line (no `event:` field — dispatch on the JSON `type`), and
`: keepalive` comment lines are emitted after 15s of silence within a phase.
The stream always terminates with exactly one `done` or `error` event.

Request body (`application/json`):

| field      | type   | required | notes                                        |
|------------|--------|----------|----------------------------------------------|
| `question` | string | yes      | non-empty                                    |
| `doc_id`   | string | no       | scope to one document (select-text-and-ask); skips phases 1–2 |
| `top_k`    | int ≥ 1| no       | phase 1 candidate count (default from config) |

```bash
curl -N -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"question": "How do power stunts work?"}'
```

`doc_id` is a **hard** scope, not a hint: phase 3 withholds the `search_corpus`
tool, pins `search_pages`/`walk_up_from_node` to that document whatever the
model passes, and discards selections from any other document. Every citation
in the response is guaranteed to come from `doc_id`.

### Event vocabulary

Events arrive in phase order; `tool_call` events repeat during phase 3, and
`citation_ready` events arrive in completion order (use `rank` to sort — the
terminal `done` result is already rank-ordered).

**`phase_started`** — a funnel phase began. `name` is one of `discovery`,
`shortlist`, `tree_reasoning`, `notes` (or `tree_load` replacing 1–2 when
`doc_id` is given).

```json
{"type": "phase_started", "phase": 1, "name": "discovery",
 "msg": "Running hybrid BM25 + semantic search…"}
```

**`candidates`** — phase 1 results. Not emitted on the `doc_id` path.

```json
{"type": "candidates", "items": [
  {"doc_id": "00be3fa22fbb4364", "doc_name": "ICONS",
   "description": "ICONS is a superhero tabletop role-playing game…",
   "score": 0.7071}
]}
```

**`docs_shortlisted`** — phase 2 results. Not emitted on the `doc_id` path.

```json
{"type": "docs_shortlisted", "doc_ids": ["00be3fa22fbb4364"], "doc_names": ["ICONS"]}
```

**`tool_call`** — one step of agentic tree navigation (phase 3, zero or more).
`fn` is one of `view_tree`, `expand_node`, `walk_up_from_node`,
`search_corpus`, `search_pages`; `args` echoes the LLM's arguments.

```json
{"type": "tool_call", "fn": "expand_node",
 "args": {"doc_id": "00be3fa22fbb4364", "node_id": "0009"}}
```

**`selections`** — the reasoner's final picks (phase 3, once).

```json
{"type": "selections", "items": [
  {"doc_id": "00be3fa22fbb4364", "node_id": "0009",
   "rationale": "This is the Determination chapter containing the power stunt rules…",
   "title": "Determination", "start_page": 74, "end_page": 82}
]}
```

**`citation_ready`** — one citation's notes finished (phase 4, once per
selection, completion order). `citation` matches the objects in
`done.result.citations`:

Each citation and liner note carries both locators: `page_number` for a PDF,
`chapter_href` for an EPUB. Exactly one of the pair is set — a client anchors on
whichever it gets, and never has to know which kind of book it opened.

```json
{"type": "citation_ready", "rank": 0, "citation": {
  "doc_id": "00be3fa22fbb4364", "node_id": "0009", "doc_name": "ICONS",
  "title": "Determination", "rationale": "…", "blurb": "Pages 80-82 give the actual rules…",
  "start_page": 74, "end_page": 82, "chapter_href": null, "error": null,
  "liner_notes": [
    {"quote": "you can use Determination to perform stunts…",
     "comment": "Core definition of a power stunt.",
     "page_number": 81, "chapter_href": null,
     "char_start": 210, "char_end": 278,
     "anchor_status": "resolved"}
  ]
}}
```

`anchor_status` is `"resolved"` (quote located verbatim in the page record;
`page_number`/`char_start`/`char_end` are trustworthy) or `"unresolved"`
(quote didn't match after normalization; offsets are null and `page_number`
may be null). A per-citation LLM failure sets `error` and leaves
`liner_notes` empty rather than failing the stream.

**`done`** — terminal success. `result` is the full SearchResult:

```json
{"type": "done", "result": {
  "question": "How do power stunts work?",
  "candidates": [ {"doc_id": "…", "doc_name": "…", "description": "…", "score": 0.7} ],
  "shortlisted_doc_ids": ["00be3fa22fbb4364"],
  "citations": [ { "…": "same shape as citation_ready.citation" } ]
}}
```

An empty `citations` list with a `done` event is the "no results" case —
render it as such; it is not an error.

**`error`** — terminal failure (any exception in the funnel):

```json
{"type": "error", "code": "llm_payment", "message": "…", "detail": "…"}
```

`code` is the machine-readable classification; `message` is presentable
copy safe to show end users; `detail` is the raw exception string, for
diagnostics only. The code vocabulary:

| code | meaning |
|---|---|
| `llm_payment` | The LLM account is out of credit (HTTP 402 from the provider). |
| `llm_auth` | The LLM API key was rejected or is not configured (401/403). |
| `llm_rate_limit` | The provider kept returning 429 past the retry budget. |
| `llm_unavailable` | Transient LLM failure (5xx/timeouts) that exhausted retries. |
| `search_backend_unavailable` | OpenSearch is unreachable. |
| `internal` | Anything else. |

Clients should switch on `code` and fall back to rendering `message` for
codes they don't recognize — new codes may be added without notice.

Note: the server does not cancel the funnel if the client disconnects
mid-stream; it runs to completion and the events are dropped.

---

## GET /documents

All ingested documents.

```json
{"documents": [
  {"doc_id": "00be3fa22fbb4364", "doc_name": "ICONS",
   "description": "ICONS is a superhero tabletop role-playing game…",
   "source_type": "pdf", "collection": null,
   "page_count": 129, "node_count": 16}
]}
```

## GET /toc/{doc_id}

The document's reconstructed section tree. 404 if the doc_id is unknown.
Nodes nest via `nodes`; PDF nodes anchor with `start_page`/`end_page`
(1-based, inclusive), EPUB nodes with `chapter_href` / `heading_anchor`.

`source_type` (`"pdf" | "epub" | "markdown"`) is how a client picks its
renderer. For EPUBs, `start_page`/`end_page` are the chapter's **spine
position**, not a printed page — an ordering key, while `chapter_href` is the
real locator.

```json
{"doc_id": "00be3fa22fbb4364", "doc_name": "ICONS", "source_type": "pdf", "tree": [
  {"node_id": "0009", "title": "Determination", "summary": "…",
   "parent_node_id": null, "depth": 0, "sibling_order": 9,
   "start_page": 74, "end_page": 82,
   "chapter_href": null, "heading_anchor": null,
   "char_start": null, "char_end": null,
   "child_count": 0, "is_leaf": true, "token_count": 5763, "nodes": []}
]}
```

## GET /content/{doc_id}?node_id=|chapter=

Content records for one node (`?node_id=`, PDF: one record per physical page)
or one EPUB chapter (`?chapter=`, matched against `chapter_href`). Exactly one
selector is required: 400 if neither is given, 404 if nothing matches.

An EPUB chapter's renderable HTML lives in a single record per chapter (its
`node_id` is `"chapter:{n}"` and its `content` is empty, so it never competes in
page search). `content_html` is **sanitized on the way out** and its `<img src>`
rewritten to `/assets/{doc_id}/media/…` — nothing HTML-shaped is trusted from
the index, and tightening the allowlist costs a request rather than a re-ingest.

```json
{"doc_id": "e3f1…", "records": [
  {"doc_id": "e3f1…", "node_id": "chapter:9", "collection": null,
   "page_number": 9, "content": "",
   "content_html": "<div id=\"chapter3\"><h1>Data Modeling…</h1>…",
   "chapter_href": "ch03.html", "token_count": 0}
]}
```

## GET /assets/{doc_id}

Streams the original source file (`vault_root` + the document's
`source_path`). Content-Type by extension (`application/pdf`,
`application/epub+zip`, `text/markdown`, else `application/octet-stream`).
Supports HTTP Range requests (`Accept-Ranges: bytes`, 206 responses) — pdf.js
depends on this. 404 if the document or file is missing; 403 if the stored
`source_path` escapes the vault root.

```bash
curl -r 0-1023 http://127.0.0.1:8000/assets/00be3fa22fbb4364   # → 206
```

## GET /assets/{doc_id}/media/{path}

One entry out of an EPUB archive — the images its chapters reference. `path` is
the entry's path inside the zip, which is what `/content?chapter=` emits in the
rewritten `<img src>`. 404 unless the document is an EPUB and `path` is an entry
in it: **membership in the archive's namelist is the traversal guard**, so `..`
has nothing to escape into. Cached for a day.

```bash
curl -I http://127.0.0.1:8000/assets/e3f1…/media/OEBPS/assets/grdb_0301.png  # → 200 image/png
```
