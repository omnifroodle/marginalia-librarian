"""OpenSearch search operations: hybrid candidate discovery and tree/content retrieval."""

from __future__ import annotations

from opensearchpy import OpenSearch

from ..opensearch.mappings import IndexNames


class DocumentSearcher:
    def __init__(self, client: OpenSearch, indexes: IndexNames) -> None:
        # indexes is required: a default IndexNames() would silently target the
        # unprefixed "librarian_*" indexes instead of the configured prefix.
        self._client = client
        self._ix = indexes

    def hybrid_search(
        self,
        query: str,
        query_embedding: list[float] | None,
        top_k: int = 20,
        filters: list[dict] | None = None,
    ) -> list[dict]:
        """Phase 1: Discover candidate documents using hybrid BM25 + k-NN search.

        Uses the OpenSearch 'hybrid' query type with a normalisation pipeline
        so that BM25 and k-NN scores are on the same scale before combining.
        Falls back to pure BM25 if no embedding is provided.
        """
        if query_embedding:
            body = self._hybrid_query(query, query_embedding, top_k, filters)
            params = {"search_pipeline": self._ix.pipeline}
        else:
            body = self._bm25_query(query, top_k, filters)
            params = {}

        resp = self._client.search(index=self._ix.documents, body=body, params=params)
        return [
            {
                "doc_id": h["_source"]["doc_id"],
                "doc_name": h["_source"]["doc_name"],
                "description": h["_source"].get("description", ""),
                "score": h["_score"],
            }
            for h in resp["hits"]["hits"]
        ]

    def get_tree_nodes(self, doc_id: str) -> list[dict]:
        """Fetch all tree nodes for a document, sorted depth-first."""
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

    def search_page_content(self, query: str, top_k: int = 10, doc_id: str | None = None) -> list[dict]:
        """BM25 search over page content text (requires content_search field to be indexed).

        Returns page-level hits: doc_id, node_id, page_number, snippet.
        doc_id is optional — when set, scopes the search to one document.
        """
        must: list[dict] = [{"match": {"content_search": query}}]
        body: dict = {
            "size": top_k,
            "_source": ["doc_id", "node_id", "page_number", "chapter_href", "content"],
            "highlight": {
                "fields": {"content_search": {"number_of_fragments": 1, "fragment_size": 300}},
                "pre_tags": [""],
                "post_tags": [""],
            },
        }
        if doc_id:
            body["query"] = {
                "bool": {"must": must, "filter": [{"term": {"doc_id": doc_id}}]}
            }
            resp = self._client.search(index=self._ix.page_content, routing=doc_id, body=body)
        else:
            body["query"] = {"bool": {"must": must}}
            resp = self._client.search(index=self._ix.page_content, body=body)

        hits = []
        for h in resp["hits"]["hits"]:
            src = h["_source"]
            hl = h.get("highlight", {}).get("content_search", [])
            snippet = hl[0] if hl else (src.get("content", "")[:300] if src.get("content") else "")
            hits.append({
                "doc_id": src.get("doc_id", ""),
                "node_id": src.get("node_id", ""),
                "page_number": src.get("page_number"),
                "chapter_href": src.get("chapter_href"),
                "snippet": snippet,
                "score": h["_score"],
            })
        return hits

    def get_page_content(self, doc_id: str, node_ids: list[str]) -> list[dict]:
        """Fetch raw content for selected nodes."""
        resp = self._client.search(
            index=self._ix.page_content,
            routing=doc_id,
            body={
                "size": 200,
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

    def reconstruct_tree(self, flat_nodes: list[dict]) -> list[dict]:
        """Rebuild a nested tree structure from flat node records."""
        by_id = {n["node_id"]: dict(n, nodes=[]) for n in flat_nodes}
        roots: list[dict] = []

        for node in flat_nodes:
            parent_id = node.get("parent_node_id")
            if parent_id and parent_id in by_id:
                by_id[parent_id]["nodes"].append(by_id[node["node_id"]])
            else:
                roots.append(by_id[node["node_id"]])

        return roots

    # ── query builders ────────────────────────────────────────────────────────

    def _hybrid_query(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        filters: list[dict] | None,
    ) -> dict:
        sub_queries = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["description^3", "root_summary^2", "top_level_titles", "collection"],
                }
            },
            {
                "knn": {
                    "description_embedding": {
                        "vector": embedding,
                        "k": top_k,
                    }
                }
            },
        ]

        if filters:
            # NOTE: OpenSearch hybrid queries don't support a shared filter across
            # both sub-queries. The bool/filter only applies to the BM25 leg here;
            # the kNN leg is unfiltered and may return candidates outside the filter
            # scope. To properly scope kNN, filters would need to be passed into the
            # knn clause separately via its own "filter" parameter — requiring the
            # caller to build the kNN sub-query differently per filter type.
            return {
                "size": top_k,
                "query": {
                    "hybrid": {
                        "queries": [
                            {
                                "bool": {
                                    "must": [sub_queries[0]],
                                    "filter": filters,
                                }
                            },
                            sub_queries[1],
                        ]
                    }
                },
            }

        return {
            "size": top_k,
            "query": {"hybrid": {"queries": sub_queries}},
        }

    def _bm25_query(
        self,
        query: str,
        top_k: int,
        filters: list[dict] | None,
    ) -> dict:
        must = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["description^3", "root_summary^2", "top_level_titles", "collection"],
                }
            }
        ]
        body: dict = {"size": top_k}
        if filters:
            body["query"] = {"bool": {"must": must, "filter": filters}}
        else:
            body["query"] = {"bool": {"must": must}}
        return body
