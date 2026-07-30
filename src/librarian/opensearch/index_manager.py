"""Create, delete, reset, and inspect OpenSearch indexes."""

from __future__ import annotations

import logging

from opensearchpy import OpenSearch

from ..config import Config
from .mappings import (
    IndexNames,
    documents_mapping,
    hybrid_search_pipeline,
    page_content_mapping,
    tree_nodes_mapping,
)

_log = logging.getLogger("librarian.opensearch")


class IndexManager:
    def __init__(self, client: OpenSearch, config: Config) -> None:
        self._client = client
        self._config = config
        self._ix = IndexNames.from_config(config)

    def create_all(self) -> list[tuple[str, str]]:
        """Create all three indexes and the hybrid search pipeline.

        Returns (name, action) pairs where action is 'created' or 'skipped'.
        """
        results = [self._ensure_pipeline()]
        dim = self._config.embedding_dimension
        mappings = {
            self._ix.documents: documents_mapping(dim),
            self._ix.tree_nodes: tree_nodes_mapping(),
            self._ix.page_content: page_content_mapping(),
        }
        for name, mapping in mappings.items():
            if self._client.indices.exists(index=name):
                _log.info("skip %s (already exists)", name)
                results.append((name, "skipped"))
            else:
                self._client.indices.create(index=name, body=mapping)
                _log.info("created %s", name)
                results.append((name, "created"))
        return results

    def delete_all(self) -> list[tuple[str, str]]:
        """Delete all three indexes."""
        results = []
        for name in self._ix.all_indexes:
            if self._client.indices.exists(index=name):
                self._client.indices.delete(index=name)
                _log.info("deleted %s", name)
                results.append((name, "deleted"))
            else:
                results.append((name, "skipped"))
        return results

    def reset_all(self) -> list[tuple[str, str]]:
        """Delete and recreate all three indexes."""
        return self.delete_all() + self.create_all()

    def status(self) -> dict[str, dict]:
        """Return doc counts, sizes, and health for each index."""
        result: dict[str, dict] = {}
        for name in self._ix.all_indexes:
            if not self._client.indices.exists(index=name):
                result[name] = {"doc_count": None, "size": "—", "health": "missing"}
                continue
            stats = self._client.indices.stats(index=name)
            idx_stats = stats["indices"].get(name, {})
            total = idx_stats.get("total", {})
            docs = total.get("docs", {})
            store = total.get("store", {})
            health_resp = self._client.cluster.health(index=name)
            result[name] = {
                "doc_count": docs.get("count", 0),
                "size": _human_bytes(store.get("size_in_bytes", 0)),
                "health": health_resp.get("status", "unknown"),
            }
        return result

    def _ensure_pipeline(self) -> tuple[str, str]:
        """Create the hybrid search normalisation pipeline if it doesn't exist."""
        pipeline = self._ix.pipeline
        try:
            self._client.transport.perform_request(
                "GET", f"/_search/pipeline/{pipeline}"
            )
            return (pipeline, "skipped")
        except Exception:
            self._client.transport.perform_request(
                "PUT",
                f"/_search/pipeline/{pipeline}",
                body=hybrid_search_pipeline(),
            )
            _log.info("created pipeline %s", pipeline)
            return (pipeline, "created")


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"
