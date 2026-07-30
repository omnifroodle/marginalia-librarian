"""Single orchestrator for the query funnel.

Replaces the triplicated engine/web/mcp pipelines in page_index_opensearch:
one library entry point that returns structured data and streams typed events
through an optional callback. Adapters (CLI, FastAPI SSE) stay thin.

Phases:
  1. hybrid BM25+kNN candidate discovery      (documents index)
  2. LLM shortlist                            (fast model over descriptions)
  3. agentic tree reasoning                   (rationale-preserving selections)
  4. per-citation note generation             (blurb + liner notes, streamed)

Event types emitted: phase_started, candidates, docs_shortlisted, tool_call,
selections, citation_ready. Terminal done/error events are the adapter's job.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from opensearchpy import OpenSearch

from ..backends import SearcherProtocol
from ..config import Config
from ..notes.note_generator import CitationNotes, NoteGenerator
from .search import DocumentSearcher
from .tree_reasoner import Selection, TreeReasoner

_log = logging.getLogger("librarian.query")

EventFn = Callable[[dict], None]


@dataclass
class SearchResult:
    question: str
    candidates: list[dict] = field(default_factory=list)
    shortlisted_doc_ids: list[str] = field(default_factory=list)
    citations: list[CitationNotes] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "candidates": self.candidates,
            "shortlisted_doc_ids": self.shortlisted_doc_ids,
            "citations": [c.to_dict() for c in self.citations],
        }


class QueryOrchestrator:
    def __init__(self, config: Config, client: OpenSearch | None = None) -> None:
        self._config = config
        if client is None:
            from ..opensearch.client import create_client
            client = create_client(config)
        self._client = client
        from ..opensearch.mappings import IndexNames
        self._indexes = IndexNames.from_config(config)
        self._searcher: SearcherProtocol = DocumentSearcher(client, self._indexes)
        # Searcher injection keeps the teleport tools enabled — in the old repo
        # only the CLI wired this, silently degrading web/MCP.
        self._reasoner = TreeReasoner(config, searcher=self._searcher)
        self._notes = NoteGenerator(config)

    def run(
        self,
        question: str,
        *,
        top_k: int | None = None,
        doc_id: str | None = None,
        on_event: EventFn | None = None,
    ) -> SearchResult:
        """Run the funnel. doc_id scopes the search to a single document
        (the reader's select-text-and-ask path)."""
        emit = on_event or (lambda _e: None)
        result = SearchResult(question=question)

        if doc_id:
            doc_trees, doc_names = self._load_single_doc(doc_id, emit)
        else:
            doc_trees, doc_names = self._discover_docs(question, top_k, emit, result)

        if not doc_trees:
            return result

        emit({"type": "phase_started", "phase": 3, "name": "tree_reasoning",
              "msg": f"Reasoning over {len(doc_trees)} document(s)…"})
        selections = self._reasoner.select_nodes(
            question, doc_trees, on_event=emit, single_doc=bool(doc_id)
        )
        if not selections:
            return result

        emit({"type": "phase_started", "phase": 4, "name": "notes",
              "msg": f"Writing notes for {len(selections)} citation(s)…"})
        result.citations = self._generate_notes(
            question, selections, doc_trees, doc_names, emit
        )
        return result

    # ── phase helpers ─────────────────────────────────────────────────────────

    def _discover_docs(
        self,
        question: str,
        top_k: int | None,
        emit: EventFn,
        result: SearchResult,
    ) -> tuple[dict[str, list[dict]], dict[str, str]]:
        """Phases 1-2: hybrid search then LLM shortlist; load shortlisted trees."""
        top_k = top_k or self._config.default_top_k

        emit({"type": "phase_started", "phase": 1, "name": "discovery",
              "msg": "Running hybrid BM25 + semantic search…"})
        query_embedding = None
        if self._config.embeddings_enabled:
            try:
                from ..ingestion.embedding import generate_embedding
                query_embedding = generate_embedding(question, self._config)
            except Exception as e:
                _log.warning("query embedding failed (%s), using BM25 only", e)

        candidates = self._searcher.hybrid_search(question, query_embedding, top_k=top_k)
        result.candidates = candidates
        emit({"type": "candidates", "items": candidates})
        if not candidates:
            return {}, {}

        emit({"type": "phase_started", "phase": 2, "name": "shortlist",
              "msg": f"Shortlisting from {len(candidates)} candidates…"})
        shortlisted = self._reasoner.shortlist_documents(question, candidates)
        result.shortlisted_doc_ids = shortlisted

        name_by_id = {c["doc_id"]: c.get("doc_name", c["doc_id"]) for c in candidates}
        emit({
            "type": "docs_shortlisted",
            "doc_ids": shortlisted,
            "doc_names": [name_by_id.get(d, d) for d in shortlisted],
        })

        doc_trees: dict[str, list[dict]] = {}
        doc_names: dict[str, str] = {}
        for did in shortlisted:
            flat = self._searcher.get_tree_nodes(did)
            if flat:
                doc_trees[did] = self._searcher.reconstruct_tree(flat)
                doc_names[did] = name_by_id.get(did, did)
        return doc_trees, doc_names

    def _load_single_doc(
        self, doc_id: str, emit: EventFn
    ) -> tuple[dict[str, list[dict]], dict[str, str]]:
        emit({"type": "phase_started", "phase": 3, "name": "tree_load",
              "msg": f"Loading tree for document {doc_id}…"})
        flat = self._searcher.get_tree_nodes(doc_id)
        if not flat:
            return {}, {}
        doc_name = doc_id
        from ..opensearch.store import DocumentStore
        doc = DocumentStore(self._client, self._indexes).get_document(doc_id)
        if doc:
            doc_name = doc.get("doc_name", doc_id)
        return {doc_id: self._searcher.reconstruct_tree(flat)}, {doc_id: doc_name}

    def _generate_notes(
        self,
        question: str,
        selections: list[Selection],
        doc_trees: dict[str, list[dict]],
        doc_names: dict[str, str],
        emit: EventFn,
    ) -> list[CitationNotes]:
        """Phase 4: one note-generation call per citation, fanned out on threads.

        citation_ready events fire in completion order; the returned list keeps
        the reasoner's rank order.
        """
        from .tree_reasoner import _find_node

        # Fetch page records per document once, then slice per node.
        pages_by_doc: dict[str, list[dict]] = {}
        for sel in selections:
            if sel.doc_id not in pages_by_doc:
                node_ids = [s.node_id for s in selections if s.doc_id == sel.doc_id]
                pages_by_doc[sel.doc_id] = self._searcher.get_page_content(sel.doc_id, node_ids)

        def make(rank: int, sel: Selection) -> tuple[int, CitationNotes]:
            tree = doc_trees.get(sel.doc_id, [])
            node = _find_node(tree, sel.node_id) or {}
            page_records = [
                r for r in pages_by_doc.get(sel.doc_id, [])
                if r.get("node_id") == sel.node_id
            ]
            notes = self._notes.generate(
                question,
                doc_id=sel.doc_id,
                node_id=sel.node_id,
                doc_name=doc_names.get(sel.doc_id, sel.doc_id),
                title=node.get("title", ""),
                rationale=sel.rationale,
                page_records=page_records,
                start_page=node.get("start_page"),
                end_page=node.get("end_page"),
                chapter_href=node.get("chapter_href"),
            )
            return rank, notes

        results: dict[int, CitationNotes] = {}
        max_workers = min(len(selections), self._config.max_concurrent_llm_calls) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(make, rank, sel) for rank, sel in enumerate(selections)]
            for fut in as_completed(futures):
                rank, notes = fut.result()
                results[rank] = notes
                emit({"type": "citation_ready", "rank": rank, "citation": notes.to_dict()})

        return [results[r] for r in sorted(results)]
