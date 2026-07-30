"""OpenSearch index mapping definitions for all three indexes."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_INDEX_PREFIX = "librarian"


@dataclass(frozen=True)
class IndexNames:
    """Index/pipeline names derived from a configurable prefix.

    Librarian is a generic engine; the prefix is how a deployment (e.g.
    marginalia) namespaces its indexes, letting several projects share one
    cluster.
    """

    prefix: str = DEFAULT_INDEX_PREFIX

    @property
    def documents(self) -> str:
        return f"{self.prefix}_documents"

    @property
    def tree_nodes(self) -> str:
        return f"{self.prefix}_tree_nodes"

    @property
    def page_content(self) -> str:
        return f"{self.prefix}_page_content"

    @property
    def pipeline(self) -> str:
        return f"{self.prefix}-hybrid-search"

    @property
    def all_indexes(self) -> list[str]:
        return [self.documents, self.tree_nodes, self.page_content]

    @classmethod
    def from_config(cls, config) -> "IndexNames":
        return cls(config.index_prefix)

_DOC_ANALYZER = {
    "analyzer": {
        "doc_analyzer": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "stop", "snowball"],
        }
    }
}


def documents_mapping(embedding_dimension: int = 1024) -> dict:
    """Mapping for the 'documents' index.

    Uses Lucene engine for HNSW (nmslib is deprecated as of OpenSearch 3.0).
    """
    return {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "knn": True,
                "knn.algo_param.ef_search": 100,
            },
            "analysis": _DOC_ANALYZER,
        },
        "mappings": {
            "properties": {
                "doc_id": {"type": "keyword"},
                "doc_name": {
                    "type": "text",
                    "analyzer": "doc_analyzer",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "source_type": {"type": "keyword"},
                "source_path": {"type": "keyword"},
                "file_sha256": {"type": "keyword"},
                "file_size": {"type": "long"},
                "domain": {"type": "keyword"},
                "collection": {
                    "type": "text",
                    "analyzer": "doc_analyzer",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "page_count": {"type": "integer"},
                "language": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "description": {"type": "text", "analyzer": "doc_analyzer"},
                "description_embedding": _knn_vector(embedding_dimension),
                "root_summary": {"type": "text", "analyzer": "doc_analyzer"},
                "root_summary_embedding": _knn_vector(embedding_dimension),
                "top_level_titles": {"type": "text", "analyzer": "doc_analyzer"},
                "tree_depth": {"type": "integer"},
                "node_count": {"type": "integer"},
                "embedding_model": {"type": "keyword"},
                "summary_model": {"type": "keyword"},
            }
        },
    }


def tree_nodes_mapping() -> dict:
    """Mapping for the 'tree_nodes' index.

    Routing is required — all nodes for a document land on the same shard.
    """
    return {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "analysis": _DOC_ANALYZER,
        },
        "mappings": {
            "_routing": {"required": True},
            "properties": {
                "doc_id": {"type": "keyword"},
                "node_id": {"type": "keyword"},
                "collection": {
                    "type": "text",
                    "analyzer": "doc_analyzer",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "parent_node_id": {"type": "keyword"},
                "depth": {"type": "integer"},
                "sibling_order": {"type": "integer"},
                "title": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "summary": {"type": "text"},
                "start_page": {"type": "integer"},
                "end_page": {"type": "integer"},
                # EPUB/markdown anchors (null for PDF)
                "chapter_href": {"type": "keyword"},
                "heading_anchor": {"type": "keyword"},
                "char_start": {"type": "long"},
                "char_end": {"type": "long"},
                "token_count": {"type": "integer"},
                "child_count": {"type": "integer"},
                "is_leaf": {"type": "boolean"},
                "summary_model": {"type": "keyword"},
            },
        },
    }


def page_content_mapping() -> dict:
    """Mapping for the 'page_content' index.

    Content field is not indexed (only fetched after node selection);
    content_search mirrors it for BM25. Routing required — all pages for a
    document land on the same shard.
    """
    return {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "analysis": _DOC_ANALYZER,
        },
        "mappings": {
            "_routing": {"required": True},
            "properties": {
                "doc_id": {"type": "keyword"},
                "node_id": {"type": "keyword"},
                "collection": {
                    "type": "text",
                    "analyzer": "doc_analyzer",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "page_number": {"type": "integer"},
                "content": {"type": "text", "index": False},
                "content_search": {"type": "text", "analyzer": "doc_analyzer"},
                # Sanitized chapter-fragment HTML for the EPUB reader (render only)
                "content_html": {"type": "text", "index": False},
                "chapter_href": {"type": "keyword"},
                "token_count": {"type": "integer"},
            },
        },
    }


def hybrid_search_pipeline() -> dict:
    """Search pipeline for normalising BM25 + k-NN scores before combining.

    Without normalisation, BM25 (scores 0–40+) dominates k-NN (scores 0–1).
    """
    return {
        "description": "Normalise and combine BM25 and k-NN scores for hybrid search",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {"weights": [0.4, 0.6]},
                    },
                }
            }
        ],
    }


def _knn_vector(dimension: int) -> dict:
    """k-NN vector field using Lucene HNSW (cosine similarity)."""
    return {
        "type": "knn_vector",
        "dimension": dimension,
        "method": {
            "name": "hnsw",
            "space_type": "cosinesimil",
            "engine": "lucene",
            "parameters": {
                "ef_construction": 128,
                "m": 16,
            },
        },
    }
