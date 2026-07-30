"""CRUD operations for all three OpenSearch indexes."""

from __future__ import annotations

from opensearchpy import OpenSearch, helpers

from ..models import DocumentRecord, PageContentRecord, TreeNodeRecord
from .mappings import IndexNames


class DocumentStore:
    def __init__(self, client: OpenSearch, indexes: IndexNames) -> None:
        # indexes is required: a default IndexNames() would silently target the
        # unprefixed "librarian_*" indexes instead of the configured prefix.
        self._client = client
        self._ix = indexes

    # ── documents index ───────────────────────────────────────────────────────

    def upsert_document(self, record: DocumentRecord) -> None:
        self._client.index(
            index=self._ix.documents,
            id=record.doc_id,
            body=record.to_os_doc(),
        )

    def document_exists(self, doc_id: str) -> bool:
        """Return True if a document with this doc_id already exists."""
        return self._client.exists(index=self._ix.documents, id=doc_id)

    def document_exists_by_name(self, doc_name: str) -> str | None:
        """Return doc_id if a document with this name already exists, else None."""
        resp = self._client.search(
            index=self._ix.documents,
            body={
                "size": 1,
                "query": {"term": {"doc_name.keyword": doc_name}},
                "_source": ["doc_id"],
            },
        )
        hits = resp["hits"]["hits"]
        if hits:
            return hits[0]["_source"]["doc_id"]
        return None

    def get_document(self, doc_id: str) -> dict | None:
        try:
            resp = self._client.get(index=self._ix.documents, id=doc_id)
            return resp["_source"]
        except Exception:
            return None

    def list_documents(self, size: int = 100) -> list[dict]:
        resp = self._client.search(
            index=self._ix.documents,
            body={
                "size": size,
                "query": {"match_all": {}},
                "_source": [
                    "doc_id", "doc_name", "description", "page_count",
                    "node_count", "source_type", "collection",
                ],
            },
        )
        return [h["_source"] for h in resp["hits"]["hits"]]

    # ── tree_nodes index ──────────────────────────────────────────────────────

    def upsert_tree_nodes(self, doc_id: str, nodes: list[TreeNodeRecord]) -> None:
        # Delete existing nodes for this document first (safe re-ingestion)
        self._delete_by_doc_id(self._ix.tree_nodes, doc_id)

        actions = [
            {
                "_index": self._ix.tree_nodes,
                "_id": f"{doc_id}::{node.node_id}",
                "_routing": doc_id,
                **node.to_os_doc(),
            }
            for node in nodes
        ]
        if actions:
            helpers.bulk(self._client, actions, max_chunk_bytes=5 * 1024 * 1024)

    def get_tree_nodes(self, doc_id: str) -> list[dict]:
        resp = self._client.search(
            index=self._ix.tree_nodes,
            routing=doc_id,
            body={
                "size": 1000,
                "query": {"term": {"doc_id": doc_id}},
                "sort": [{"depth": "asc"}, {"sibling_order": "asc"}],
            },
        )
        return [h["_source"] for h in resp["hits"]["hits"]]

    # ── page_content index ────────────────────────────────────────────────────

    def upsert_page_content(self, doc_id: str, pages: list[PageContentRecord]) -> None:
        # Delete existing content for this document first
        self._delete_by_doc_id(self._ix.page_content, doc_id)

        actions = [
            {
                "_index": self._ix.page_content,
                "_id": f"{doc_id}::{page.node_id}::{page.page_number}",
                "_routing": doc_id,
                **page.to_os_doc(),
            }
            for page in pages
        ]
        if actions:
            helpers.bulk(self._client, actions, max_chunk_bytes=5 * 1024 * 1024)

    def get_page_content(self, doc_id: str, node_ids: list[str]) -> list[dict]:
        resp = self._client.search(
            index=self._ix.page_content,
            routing=doc_id,
            body={
                "size": 500,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"doc_id": doc_id}},
                            {"terms": {"node_id": node_ids}},
                        ]
                    }
                },
                "sort": [{"page_number": "asc"}],
            },
        )
        return [h["_source"] for h in resp["hits"]["hits"]]

    def get_chapter_content(self, doc_id: str, chapter_href: str) -> list[dict]:
        """All content records for one EPUB/markdown chapter, in page order."""
        resp = self._client.search(
            index=self._ix.page_content,
            routing=doc_id,
            body={
                "size": 500,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"doc_id": doc_id}},
                            {"term": {"chapter_href": chapter_href}},
                        ]
                    }
                },
                "sort": [{"page_number": "asc"}],
            },
        )
        return [h["_source"] for h in resp["hits"]["hits"]]

    # ── delete helpers ────────────────────────────────────────────────────────

    def delete_document(self, doc_id: str) -> None:
        """Remove a document from all three indexes."""
        try:
            self._client.delete(index=self._ix.documents, id=doc_id)
        except Exception:
            pass
        self._delete_by_doc_id(self._ix.tree_nodes, doc_id)
        self._delete_by_doc_id(self._ix.page_content, doc_id)

    def _delete_by_doc_id(self, index: str, doc_id: str) -> None:
        from opensearchpy.exceptions import NotFoundError
        try:
            self._client.delete_by_query(
                index=index,
                body={"query": {"term": {"doc_id": doc_id}}},
                params={"routing": doc_id, "conflicts": "proceed"},
            )
        except NotFoundError:
            # Index doesn't exist yet — nothing to delete, that's fine.
            pass
