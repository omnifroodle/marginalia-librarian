"""Backend seam: the storage/search surface the rest of librarian depends on.

Transition-only module for the OpenSearch -> Couchbase migration. These
Protocols pin the exact method surface that callers (ingestion pipeline,
query orchestrator, tree reasoner teleport tools, API adapter) rely on, so
the Couchbase implementations in cb/ can be built against a stable contract
while the fake-based tests stay green. Deleted in Lesson 11 once cb/ is the
only backend (see CURRICULUM.md).
"""

from __future__ import annotations

from typing import Protocol

from .models import DocumentRecord, PageContentRecord, TreeNodeRecord


class StoreProtocol(Protocol):
    """Write path + direct document reads (currently opensearch/store.py)."""

    def upsert_document(self, record: DocumentRecord) -> None: ...

    def document_exists(self, doc_id: str) -> bool: ...

    def get_document(self, doc_id: str) -> dict | None: ...

    def list_documents(self, size: int = 100) -> list[dict]: ...

    def upsert_tree_nodes(self, doc_id: str, nodes: list[TreeNodeRecord]) -> None: ...

    def upsert_page_content(self, doc_id: str, pages: list[PageContentRecord]) -> None: ...

    def get_page_content(self, doc_id: str, node_ids: list[str]) -> list[dict]: ...

    def get_chapter_content(self, doc_id: str, chapter_href: str) -> list[dict]: ...


class SearcherProtocol(Protocol):
    """Query path used by the four-phase funnel (currently query/search.py)."""

    def hybrid_search(
        self,
        query: str,
        query_embedding: list[float] | None,
        top_k: int = 20,
        filters: list[dict] | None = None,
    ) -> list[dict]: ...

    def get_tree_nodes(self, doc_id: str) -> list[dict]: ...

    def search_page_content(
        self, query: str, top_k: int = 10, doc_id: str | None = None
    ) -> list[dict]: ...

    def get_page_content(self, doc_id: str, node_ids: list[str]) -> list[dict]: ...

    def reconstruct_tree(self, flat_nodes: list[dict]) -> list[dict]: ...
