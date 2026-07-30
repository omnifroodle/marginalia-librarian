"""Collection names for the librarian's storage layout.

The OpenSearch analogue is `opensearch/mappings.py::IndexNames`, and the
difference between the two is the whole point of Lesson 2.

OpenSearch indexes share one flat, cluster-wide namespace, so `IndexNames`
carries a configurable *prefix* — `librarian_documents`, `marginalia_documents`
— because that prefix is the only thing keeping two projects on one cluster
from colliding.

Couchbase gives you that namespacing structurally: bucket → scope →
collection. Two projects get two buckets, or two scopes in one bucket, and the
collection names underneath are free to be plain. So there is no prefix here,
and these are constants rather than a configurable dataclass. The
namespacing knobs are `couchbase.bucket` and `couchbase.scope` in config.

Mapping from the OpenSearch world, for the port:

    librarian_documents      ->  <bucket>.<scope>.documents
    librarian_tree_nodes     ->  <bucket>.<scope>.tree_nodes
    librarian_page_content   ->  <bucket>.<scope>.page_content
    librarian-hybrid-search  ->  (nothing — client-side fusion, Lesson 10)

The search pipeline has no analogue at all: Couchbase has no server-side
hybrid-fusion primitive, so that normalisation moves into Python. Do not go
looking for where to create it.
"""

from __future__ import annotations

DOCUMENTS = "documents"
TREE_NODES = "tree_nodes"
PAGE_CONTENT = "page_content"

#: Every collection the librarian owns, in creation order. Iterate this rather
#: than re-listing the three names at each call site.
ALL_COLLECTIONS: tuple[str, ...] = (DOCUMENTS, TREE_NODES, PAGE_CONTENT)
