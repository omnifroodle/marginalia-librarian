"""LLM-based tree reasoning: shortlist documents and select relevant nodes.

Ported from page_index_opensearch with three changes:
- selections carry the model's rationale (feeds citation blurbs / liner notes)
- no os.environ mutation; api_base/api_key passed per call
- no console output; diagnostics via logging, JSONL logs only when config.log_dir is set
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import litellm

from ..backends import SearcherProtocol
from ..config import Config

_log = logging.getLogger("librarian.query")


@dataclass
class Selection:
    """One node the reasoner picked, with its stated reason."""
    doc_id: str
    node_id: str
    rationale: str = ""


# ── JSON parsing helpers ──────────────────────────────────────────────────────

def _parse_llm_json(text: str, context: str, model: str = "", log_dir: Path | None = None) -> object:
    """Parse JSON from an LLM response with a multi-pass cleanup pipeline.

    Handles the most common failure modes from small/local models:
    - <think>...</think> blocks (Qwen3 / chain-of-thought models)
    - ```json fences
    - Python literals (None, True, False)
    - Trailing commas before ] or }
    - Trailing model chatter after the JSON object

    Returns the parsed value, or None on failure.
    Failures are logged to <log_dir>/json_parse_failures.jsonl when log_dir is set.
    """
    # 1. Strip <think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 2. Extract from ```json / ``` fences
    fence = cleaned.find("```json")
    if fence != -1:
        cleaned = cleaned[fence + 7:]
        end = cleaned.rfind("```")
        if end != -1:
            cleaned = cleaned[:end]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
        end = cleaned.rfind("```")
        if end != -1:
            cleaned = cleaned[:end]
    cleaned = cleaned.strip()

    # 3. Normalise Python literals
    cleaned = cleaned.replace("None", "null").replace("True", "true").replace("False", "false")

    # 4. First attempt — raw_decode tolerates trailing model chatter
    first_error = ""
    try:
        result, _ = json.JSONDecoder().raw_decode(cleaned)
        return result
    except json.JSONDecodeError as e:
        first_error = str(e)

    # 5. Second attempt — strip trailing commas then retry
    twice_cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
    try:
        result, _ = json.JSONDecoder().raw_decode(twice_cleaned)
        return result
    except json.JSONDecodeError:
        pass

    _append_jsonl(log_dir, "json_parse_failures.jsonl", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "context": context,
        "model": model,
        "error": first_error,
        "response": text,
    })
    _log.warning("JSON parse failed (%s)", context)
    return None


def _append_jsonl(log_dir: Path | None, filename: str, entry: dict) -> None:
    """Append a record to a JSONL diagnostic log; no-op when log_dir is unset."""
    if not log_dir:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / filename).open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # never let logging break the main flow


# ── Tool definitions for agentic Phase 3 ─────────────────────────────────────

# search_corpus is the only tool that can pull a *new* document into the run, so
# it is kept separate: single-doc mode (the reader's select-and-ask path) must
# not offer it, or "ask about this book" could answer out of a different book.
SEARCH_CORPUS_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "search_corpus",
            "description": (
                "Re-run a full-corpus search when your accumulated understanding suggests "
                "a different document is more relevant than the ones you were given. "
                "Returns a ranked list of doc_ids with descriptions — same shape as the "
                "initial shortlist. Use this when the answer seems to live in a document "
                "you haven't seen yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Re-framed search query based on what you've learned"},
                    "top_k": {"type": "integer", "description": "Number of results to return (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
]

SINGLE_DOC_TELEPORT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_pages",
            "description": (
                "BM25 search over raw page content text. Use this when you suspect the "
                "right passage exists but the tree summaries aren't surfacing it — for "
                "example, when a chapter has a vague or misleading title. Returns page-level "
                "hits with snippets. After a hit, call walk_up_from_node to recover the "
                "hierarchical context before deciding to include the passage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query against raw page text"},
                    "top_k": {"type": "integer", "description": "Number of results to return (default 10)"},
                    "doc_id": {"type": "string", "description": "Scope to a specific document (optional)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "walk_up_from_node",
            "description": (
                "Given a node_id from a search_pages hit, return the node's full summary, "
                "its parent chain to the root (titles + summaries), and its immediate siblings "
                "(titles only). Use this after search_pages to understand where in the document "
                "hierarchy the hit lives before selecting it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Document ID"},
                    "node_id": {"type": "string", "description": "Node ID from a search_pages hit"},
                },
                "required": ["doc_id", "node_id"],
            },
        },
    },
]

TELEPORT_TOOLS = SEARCH_CORPUS_TOOL + SINGLE_DOC_TELEPORT_TOOLS

PHASE3_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "view_tree",
            "description": (
                "View the hierarchical structure of a document. Returns top-level "
                "sections with summaries, and their immediate children with titles only. "
                "Use expand_node to see details of deeper sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Document ID to view"},
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_node",
            "description": (
                "Drill into a specific section. Returns the node's full summary "
                "plus all children recursively with their summaries. Use this after "
                "view_tree to explore promising branches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Document ID"},
                    "node_id": {"type": "string", "description": "Node ID to expand"},
                },
                "required": ["doc_id", "node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_selections",
            "description": (
                "Submit your final node selections. Call this when you have identified "
                "the 1-3 sections per document most likely to contain the answer. "
                "For each selection, explain in one or two sentences why this specific "
                "section is relevant to the query — your rationale is shown to the "
                "reader alongside the citation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "doc_id": {"type": "string"},
                                "node_id": {"type": "string"},
                                "rationale": {
                                    "type": "string",
                                    "description": "1-2 sentences: why this section answers the query",
                                },
                            },
                            "required": ["doc_id", "node_id", "rationale"],
                        },
                        "description": "Array of {doc_id, node_id, rationale} selections",
                    },
                },
                "required": ["selections"],
            },
        },
    },
]


class TreeReasoner:
    def __init__(self, config: Config, searcher: SearcherProtocol | None = None) -> None:
        self._config = config
        self._searcher = searcher  # injected for teleport tools
        self._log_dir = config.log_dir

    def shortlist_documents(
        self,
        query: str,
        candidates: list[dict],
        n: int | None = None,
    ) -> list[str]:
        """Phase 2: Use fast model to pick the most relevant candidate doc_ids."""
        if n is None:
            n = self._config.shortlist_size

        if len(candidates) <= n:
            return [c["doc_id"] for c in candidates]

        doc_list = "\n".join(
            f"- doc_id: {c['doc_id']}\n  name: {c['doc_name']}\n  description: {c['description']}"
            for c in candidates
        )

        prompt = f"""You are helping answer a research question by selecting the most relevant documents.

Query: {query}

Candidate documents:
{doc_list}

Select the {n} document IDs most likely to contain the answer to the query.
Return ONLY a JSON array of doc_id strings, e.g.: ["abc123", "def456"]
No explanation needed."""

        response = self._call_llm(self._config.tree_generation_model, prompt)
        _log.debug("shortlist response: %s", response)

        parsed = _parse_llm_json(response, "shortlist", self._config.tree_generation_model, self._log_dir)
        if isinstance(parsed, list):
            return [str(s) for s in parsed[:n]]

        # Fallback: return top-n by original score order
        return [c["doc_id"] for c in candidates[:n]]

    def select_nodes(
        self,
        query: str,
        doc_trees: dict[str, list[dict]],
        on_event=None,
        single_doc: bool = False,
    ) -> list[Selection]:
        """Phase 3: Select relevant nodes from document trees, with rationale.

        Routes to agentic (tool-calling) or one-shot mode based on config.
        on_event: optional callable(dict) for streaming progress events.
        single_doc: hard-scope the run to the one document in doc_trees — no
        search_corpus tool, page search pinned to that doc, foreign selections
        dropped. One-shot mode can only pick from the trees it is given, so it
        needs no equivalent guard.
        """
        _log.info("tree reasoning over %d document(s), mode=%s", len(doc_trees), self._config.reasoning_mode)

        if self._config.reasoning_mode == "one_shot":
            return self._select_nodes_one_shot(query, doc_trees, on_event=on_event)
        return self._select_nodes_agentic(
            query, doc_trees, on_event=on_event, single_doc=single_doc
        )

    # ── agentic mode ─────────────────────────────────────────────────────────

    def _select_nodes_agentic(
        self,
        query: str,
        doc_trees: dict[str, list[dict]],
        on_event=None,
        single_doc: bool = False,
    ) -> list[Selection]:
        """Navigate document trees via tool calls: view_tree → expand_node → submit_selections.

        When a searcher is available, also exposes the teleport tools:
        search_corpus (corpus-wide runs only), search_pages, walk_up_from_node.
        """
        teleport_enabled = self._searcher is not None
        teleport_count = 0
        teleport_max = self._config.teleport_max
        # In single-doc mode every tool that touches the corpus is pinned to this
        # doc_id; the model is never given the means to reach another document.
        scoped_doc_id = next(iter(doc_trees), None) if single_doc else None

        # Build document listing for the system prompt
        doc_listing = []
        for doc_id, tree in doc_trees.items():
            root_title = tree[0].get("title", "Untitled") if tree else "Empty"
            node_count = _count_nodes(tree)
            doc_listing.append({"doc_id": doc_id, "title": root_title, "node_count": node_count})

        teleport_hint = ""
        if teleport_enabled and single_doc:
            teleport_hint = f"""
Teleport tools (use sparingly — max {teleport_max} calls to search_pages):
- search_pages(query): BM25 search over the raw page text of this document — use when a chapter title is vague but you suspect the answer is inside. Returns page hits with snippets.
- walk_up_from_node(doc_id, node_id): After a search_pages hit, recover hierarchical context (parent chain + siblings) before selecting the node.

This query is scoped to a single document. Answer from it alone — there is no corpus-wide search available, and selections from any other document will be discarded."""
        elif teleport_enabled:
            teleport_hint = f"""
Teleport tools (use sparingly — max {teleport_max} total across search_corpus and search_pages):
- search_corpus(query): Re-run a corpus-wide search when you believe the answer is in a document not yet in your list. Returns doc_ids with descriptions.
- search_pages(query, doc_id?): BM25 search over raw page text — use when a chapter title is vague but you suspect the answer is inside. Returns page hits with snippets.
- walk_up_from_node(doc_id, node_id): After a search_pages hit, recover hierarchical context (parent chain + siblings) before selecting the node.

Use teleport when: (a) you've exhausted the current documents and suspect a better one exists, or (b) tree summaries are not surfacing the answer but you believe it's there."""

        system_msg = f"""You are navigating document trees to find sections that answer a research query.

Available documents:
{json.dumps(doc_listing)}

Navigation tools:
- view_tree(doc_id): See a document's top-level structure with summaries
- expand_node(doc_id, node_id): Drill into a section to see its children with summaries
- submit_selections(selections): Submit your final [{{"doc_id": "...", "node_id": "...", "rationale": "..."}}] picks
{teleport_hint}
Workflow: Start by calling view_tree for each document. Identify promising branches from the top-level summaries. Use expand_node to drill deeper where needed. When you've found the most relevant sections, call submit_selections with 1-3 node_ids per relevant document, each with a short rationale explaining why that section answers the query."""

        if not teleport_enabled:
            teleport_tools = []
        elif single_doc:
            teleport_tools = SINGLE_DOC_TELEPORT_TOOLS
        else:
            teleport_tools = TELEPORT_TOOLS
        all_tools = PHASE3_TOOLS + teleport_tools

        messages: list[dict] = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Find the sections most likely to answer this query: {query}"},
        ]

        max_iterations = 20
        for iteration in range(max_iterations):
            try:
                response = litellm.completion(
                    model=self._config.reasoning_model,
                    messages=messages,
                    tools=all_tools,
                    tool_choice="auto",
                    temperature=0,
                    api_base=self._config.reasoning_api_base,
                    api_key=self._config.reasoning_api_key,
                    num_retries=self._config.num_retries,
                    timeout=self._config.completion_timeout,
                    # LiteLLM's capability map for OpenAI-compatible gateways
                    # (e.g. nano-gpt) claims no tool support and refuses to send
                    # these; force them through — the model behind the gateway
                    # does support them, and a genuine rejection still triggers
                    # the one_shot fallback.
                    allowed_openai_params=["tools", "tool_choice"],
                )
            except Exception as e:
                _log.warning("agentic reasoning failed (%s), falling back to one_shot", e)
                return self._select_nodes_one_shot(query, doc_trees, on_event=on_event)

            msg = response.choices[0].message

            # Append assistant message to conversation history
            messages.append(msg.model_dump(exclude_none=True))

            tool_calls = msg.tool_calls
            if not tool_calls:
                # LLM responded without tool calls — probably doesn't support them
                _log.info("no tool calls in response, falling back to one_shot")
                return self._select_nodes_one_shot(query, doc_trees, on_event=on_event)

            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                _log.debug("tool: %s(%s)", fn_name, json.dumps(fn_args, separators=(",", ":")))

                if fn_name == "view_tree":
                    result = _handle_view_tree(doc_trees, fn_args.get("doc_id", ""))
                    if on_event:
                        on_event({"type": "tool_call", "fn": fn_name, "args": fn_args})

                elif fn_name == "expand_node":
                    result = _handle_expand_node(
                        doc_trees, fn_args.get("doc_id", ""), fn_args.get("node_id", "")
                    )
                    if on_event:
                        on_event({"type": "tool_call", "fn": fn_name, "args": fn_args})

                elif fn_name == "submit_selections":
                    raw_selections = fn_args.get("selections", [])
                    selections = [
                        Selection(
                            doc_id=s["doc_id"],
                            node_id=s["node_id"],
                            rationale=str(s.get("rationale", "")).strip(),
                        )
                        for s in raw_selections
                        if "doc_id" in s and "node_id" in s
                    ]
                    if scoped_doc_id:
                        kept = [s for s in selections if s.doc_id == scoped_doc_id]
                        if len(kept) != len(selections):
                            _log.warning(
                                "dropped %d selection(s) outside the scoped document %s",
                                len(selections) - len(kept), scoped_doc_id,
                            )
                        selections = kept
                    _log.info("submitted %d selection(s)", len(selections))
                    if on_event:
                        on_event({"type": "selections", "items": [
                            _enrich_selection(sel, doc_trees) for sel in selections
                        ]})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "ok", "count": len(selections)}),
                    })
                    return selections

                elif fn_name == "search_corpus":
                    if scoped_doc_id:
                        # Not in the tool list for this run; a model that calls it
                        # anyway must not be able to reach another document.
                        result = {"error": "search_corpus is not available: this query is scoped to a single document."}
                    elif teleport_count >= teleport_max:
                        result = {"error": f"Teleport limit reached ({teleport_max}). Use navigation tools to finalise."}
                    else:
                        teleport_count += 1
                        result = self._handle_search_corpus(
                            query=fn_args.get("query", query),
                            top_k=fn_args.get("top_k", 10),
                            original_query=query,
                        )
                        # If new docs came back, fetch their trees and add to doc_trees
                        for hit in result.get("results", []):
                            did = hit["doc_id"]
                            if did not in doc_trees and self._searcher:
                                flat = self._searcher.get_tree_nodes(did)
                                if flat:
                                    doc_trees[did] = self._searcher.reconstruct_tree(flat)
                                    doc_listing.append({
                                        "doc_id": did,
                                        "title": hit.get("doc_name", did),
                                        "node_count": _count_nodes(doc_trees[did]),
                                    })
                    if on_event:
                        on_event({"type": "tool_call", "fn": fn_name, "args": fn_args})

                elif fn_name == "search_pages":
                    if teleport_count >= teleport_max:
                        result = {"error": f"Teleport limit reached ({teleport_max}). Use navigation tools to finalise."}
                    else:
                        teleport_count += 1
                        result = self._handle_search_pages(
                            query=fn_args.get("query", query),
                            top_k=fn_args.get("top_k", 10),
                            # Single-doc runs pin the scope; the model's doc_id
                            # argument is not trusted.
                            doc_id=scoped_doc_id or fn_args.get("doc_id"),
                        )
                    if on_event:
                        on_event({"type": "tool_call", "fn": fn_name, "args": fn_args})

                elif fn_name == "walk_up_from_node":
                    result = self._handle_walk_up(
                        doc_trees=doc_trees,
                        # Pinned too: this handler loads an unknown doc's tree
                        # from OpenSearch on demand, which would otherwise put a
                        # second document back in play.
                        doc_id=scoped_doc_id or fn_args.get("doc_id", ""),
                        node_id=fn_args.get("node_id", ""),
                    )
                    if on_event:
                        on_event({"type": "tool_call", "fn": fn_name, "args": fn_args})

                else:
                    result = {"error": f"Unknown tool: {fn_name}"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        _log.warning("agentic loop hit max iterations without submitting")
        return []

    # ── teleport tool handlers ────────────────────────────────────────────────

    def _handle_search_corpus(self, query: str, top_k: int, original_query: str) -> dict:
        """Re-run corpus search with a new query. Logs the teleport event."""
        if not self._searcher:
            return {"error": "search_corpus not available (no searcher configured)"}

        _log.info("teleport: search_corpus query=%r", query)
        try:
            hits = self._searcher.hybrid_search(query, query_embedding=None, top_k=top_k)
        except Exception as e:
            return {"error": str(e)}

        self._log_teleport("search_corpus", original_query=original_query, teleport_query=query, results=hits)
        return {
            "results": [
                {"doc_id": h["doc_id"], "doc_name": h["doc_name"], "description": h["description"]}
                for h in hits
            ]
        }

    def _handle_search_pages(self, query: str, top_k: int, doc_id: str | None) -> dict:
        """BM25 search over page content. Logs the teleport event."""
        if not self._searcher:
            return {"error": "search_pages not available (no searcher configured)"}

        _log.info("teleport: search_pages query=%r doc_id=%r", query, doc_id)
        try:
            hits = self._searcher.search_page_content(query, top_k=top_k, doc_id=doc_id)
        except Exception as e:
            return {"error": str(e)}

        self._log_teleport("search_pages", original_query=query, teleport_query=query, results=hits, doc_id=doc_id)
        return {"results": hits}

    def _handle_walk_up(self, doc_trees: dict[str, list[dict]], doc_id: str, node_id: str) -> dict:
        """Return a node's summary, parent chain, and siblings."""
        # Fetch tree from OS if not already loaded (e.g. after a search_corpus teleport)
        tree = doc_trees.get(doc_id)
        if tree is None and self._searcher:
            flat = self._searcher.get_tree_nodes(doc_id)
            if flat:
                tree = self._searcher.reconstruct_tree(flat)
                doc_trees[doc_id] = tree

        if tree is None:
            return {"error": f"Document {doc_id} not found"}

        # Flatten tree into a lookup dict for parent traversal
        flat_lookup: dict[str, dict] = {}
        _flatten_tree(tree, flat_lookup, parent_id=None)

        if node_id not in flat_lookup:
            return {"error": f"Node {node_id} not found in document {doc_id}"}

        node_rec = flat_lookup[node_id]
        target = node_rec["node"]

        # Build parent chain walking up
        parent_chain: list[dict] = []
        current_parent_id = node_rec["parent_id"]
        while current_parent_id and current_parent_id in flat_lookup:
            parent_rec = flat_lookup[current_parent_id]
            parent_node = parent_rec["node"]
            parent_chain.append({
                "node_id": parent_node.get("node_id", ""),
                "title": parent_node.get("title", ""),
                "summary": (parent_node.get("summary") or "")[:400],
            })
            current_parent_id = parent_rec["parent_id"]
        parent_chain.reverse()  # root first

        # Siblings: nodes that share the same parent
        siblings: list[dict] = []
        if node_rec["parent_id"]:
            parent_children = flat_lookup[node_rec["parent_id"]]["node"].get("nodes") or []
        else:
            parent_children = tree  # top-level siblings
        for sib in parent_children:
            sib_id = sib.get("node_id", "")
            if sib_id != node_id:
                siblings.append({"node_id": sib_id, "title": sib.get("title", "")})

        return {
            "node": {
                "node_id": target.get("node_id", ""),
                "title": target.get("title", ""),
                "summary": (target.get("summary") or "")[:600],
                "start_page": target.get("start_page"),
                "end_page": target.get("end_page"),
            },
            "parent_chain": parent_chain,
            "siblings": siblings,
        }

    # ── one-shot mode (fallback) ─────────────────────────────────────────────

    def _select_nodes_one_shot(
        self,
        query: str,
        doc_trees: dict[str, list[dict]],
        on_event=None,
    ) -> list[Selection]:
        """Single-prompt node selection (original approach). Used as fallback."""
        max_summary = self._config.reasoning_summary_max_chars
        trees_json = json.dumps(
            [
                {
                    "doc_id": doc_id,
                    "tree": [_slim_node(n, max_summary) for n in tree],
                }
                for doc_id, tree in doc_trees.items()
            ],
        )

        prompt = f"""You are a precise research assistant. Study the document trees below and identify the sections most likely to contain the answer to the query.

Query: {query}

Document trees (each node has: title, node_id, summary, start_page, end_page, and nested nodes):
{trees_json}

For each document, select 1-3 node_ids whose content is most likely to directly answer the query.
Return ONLY a JSON array of objects with "doc_id", "node_id", and "rationale" fields, where
rationale is 1-2 sentences explaining why that section answers the query.
Example: [{{"doc_id": "abc123", "node_id": "001.002", "rationale": "This section covers the grappling rules the query asks about."}}]

Think carefully about which sections contain the specific information needed."""

        response = self._call_llm(
            self._config.reasoning_model, prompt, use_reasoning=True,
        )
        _log.debug("one-shot selections: %s", response)

        parsed = _parse_llm_json(response, "one_shot_selections", self._config.reasoning_model, self._log_dir)
        if isinstance(parsed, list):
            selections = [
                Selection(
                    doc_id=s["doc_id"],
                    node_id=s["node_id"],
                    rationale=str(s.get("rationale", "")).strip(),
                )
                for s in parsed
                if isinstance(s, dict) and "doc_id" in s and "node_id" in s
            ]
            if on_event and selections:
                on_event({"type": "selections", "items": [
                    _enrich_selection(sel, doc_trees) for sel in selections
                ]})
            return selections

        return []

    # ── LLM call helper ──────────────────────────────────────────────────────

    def _call_llm(self, model: str, prompt: str, *, use_reasoning: bool = False, **kwargs) -> str:
        if use_reasoning:
            api_base = self._config.reasoning_api_base
            api_key = self._config.reasoning_api_key
        else:
            api_base = self._config.litellm_api_base
            api_key = self._config.litellm_api_key
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            api_base=api_base,
            api_key=api_key,
            num_retries=self._config.num_retries,
            timeout=self._config.completion_timeout,
            **kwargs,
        )
        return response.choices[0].message.content.strip()

    def _log_teleport(
        self,
        tool: str,
        original_query: str,
        teleport_query: str,
        results: list[dict],
        doc_id: str | None = None,
    ) -> None:
        _append_jsonl(self._log_dir, "teleport.jsonl", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "original_query": original_query,
            "teleport_query": teleport_query,
            "doc_id": doc_id,
            "result_count": len(results),
            "top_results": results[:3],
        })


# ── Tool handlers ────────────────────────────────────────────────────────────


def _handle_view_tree(doc_trees: dict[str, list[dict]], doc_id: str) -> dict | list:
    """Return compact tree: top-level nodes with summaries, children with titles only."""
    tree = doc_trees.get(doc_id)
    if tree is None:
        return {"error": f"Document {doc_id} not found"}
    return [_overview_node(n) for n in tree]


def _handle_expand_node(doc_trees: dict[str, list[dict]], doc_id: str, node_id: str) -> dict:
    """Return a node with full summary + all children recursively with summaries."""
    tree = doc_trees.get(doc_id)
    if tree is None:
        return {"error": f"Document {doc_id} not found"}
    node = _find_node(tree, node_id)
    if node is None:
        return {"error": f"Node {node_id} not found in document {doc_id}"}
    return _detail_node(node)


# ── Tree helpers ─────────────────────────────────────────────────────────────


def _enrich_selection(sel: Selection, doc_trees: dict[str, list[dict]]) -> dict:
    """Selection + node metadata for streaming events."""
    tree = doc_trees.get(sel.doc_id, [])
    node = _find_node(tree, sel.node_id) if tree else None
    return {
        "doc_id": sel.doc_id,
        "node_id": sel.node_id,
        "rationale": sel.rationale,
        "title": node.get("title", "") if node else "",
        "start_page": node.get("start_page") if node else None,
        "end_page": node.get("end_page") if node else None,
    }


def _overview_node(node: dict) -> dict:
    """Compact view: this node's summary + children as titles only (no summaries)."""
    result: dict = {
        "node_id": node.get("node_id", ""),
        "title": node.get("title", ""),
        "summary": node.get("summary") or "",
        "start_page": node.get("start_page"),
        "end_page": node.get("end_page"),
    }
    children = node.get("nodes") or []
    if children:
        result["nodes"] = [
            {
                "node_id": c.get("node_id", ""),
                "title": c.get("title", ""),
                "start_page": c.get("start_page"),
                "end_page": c.get("end_page"),
                "has_children": bool(c.get("nodes")),
            }
            for c in children
        ]
    return result


def _detail_node(node: dict) -> dict:
    """Full view: this node + all children recursively with summaries."""
    result: dict = {
        "node_id": node.get("node_id", ""),
        "title": node.get("title", ""),
        "summary": node.get("summary") or "",
        "start_page": node.get("start_page"),
        "end_page": node.get("end_page"),
    }
    children = node.get("nodes") or []
    if children:
        result["nodes"] = [_detail_node(c) for c in children]
    return result


def _find_node(tree: list[dict], node_id: str) -> dict | None:
    """Recursive search for a node by ID in a nested tree."""
    for node in tree:
        if node.get("node_id") == node_id:
            return node
        children = node.get("nodes") or []
        if children:
            found = _find_node(children, node_id)
            if found:
                return found
    return None


def _count_nodes(tree: list[dict]) -> int:
    """Count total nodes in a nested tree."""
    count = 0
    for node in tree:
        count += 1
        children = node.get("nodes") or []
        if children:
            count += _count_nodes(children)
    return count


def _flatten_tree(
    nodes: list[dict],
    lookup: dict[str, dict],
    parent_id: str | None,
) -> None:
    """Recursively flatten a nested tree into a {node_id: {node, parent_id}} dict."""
    for node in nodes:
        node_id = node.get("node_id", "")
        lookup[node_id] = {"node": node, "parent_id": parent_id}
        children = node.get("nodes") or []
        if children:
            _flatten_tree(children, lookup, parent_id=node_id)


def _slim_node(node: dict, max_summary: int) -> dict:
    """Strip a tree node to only the fields needed for one-shot LLM reasoning.

    Drops stored-only fields (depth, sibling_order, token counts, etc.) and
    truncates summaries to reduce prompt size.
    """
    summary = node.get("summary") or ""
    if len(summary) > max_summary:
        summary = summary[:max_summary] + "…"

    slim: dict = {
        "node_id": node.get("node_id", ""),
        "title": node.get("title", ""),
        "summary": summary,
        "start_page": node.get("start_page"),
        "end_page": node.get("end_page"),
    }

    children = node.get("nodes") or []
    if children:
        slim["nodes"] = [_slim_node(c, max_summary) for c in children]

    return slim
