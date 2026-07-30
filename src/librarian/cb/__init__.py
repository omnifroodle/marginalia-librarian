"""Couchbase backend.

Named `cb` rather than `couchbase` so it never shadows the SDK's own top-level
package. Replaces `librarian/opensearch/` over Lessons 1-11; the surface it
must satisfy is pinned by `librarian/backends.py` until that seam is deleted.

Module map (built lesson by lesson — see CURRICULUM.md):
    client.py   L1   create_cluster
    manager.py  L2   collections; L5 adds GSI indexes
    store.py    L3   KV writes; L4 bulk; L5 delete/re-ingest
    search.py   L6   SQL++ reads; L7-L10 FTS, vector, hybrid fusion
    indexes/    L7+  FTS index definition JSON
"""
