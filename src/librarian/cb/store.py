"""KV write path — the Couchbase port of `opensearch/store.py::DocumentStore`.

Grows across several lessons into the whole of `backends.StoreProtocol`:

    Lesson 3   upsert_document                     (this file, now)
    Lesson 4   upsert_tree_nodes, upsert_page_content
    Lesson 5   delete_document_artifacts
    Lesson 6   document_exists, get_document, get_page_content, list_documents

Only the current lesson's method is stubbed here on purpose — see the scope
fence in docs/briefs/lesson-03.md.

Acceptance tests: tests/integration/test_l03_store.py
"""

from __future__ import annotations

from ..config import Config
from ..models import DocumentRecord


class DocumentStore:
    """Reads and writes the librarian's documents in one bucket + scope.

    The OpenSearch analogue took `(client, indexes)`; this takes
    `(cluster, config)` to match `cb.manager.CollectionManager`, since both
    need the same bucket/scope pair out of config.
    """

    def __init__(self, cluster, config: Config) -> None:
        """
        Args:
            cluster: a live `Cluster` from `cb.client.create_cluster`.
            config:  supplies `couchbase_bucket` and `couchbase_scope`.

        The collection handles belong here, not in the methods. `upsert_document`
        is called once per document today and ~10k times per ingest from Lesson
        4 onward; re-deriving `bucket → scope → collection` inside the write is
        the N+1 of this codebase. `cb.names` has the collection names.
        """
        raise NotImplementedError("Lesson 3")

    def upsert_document(self, record: DocumentRecord) -> None:
        """Write one document record, overwriting any existing one.

        Conforms to `backends.StoreProtocol.upsert_document` — the ingestion
        pipeline calls exactly this, so the signature and the `None` return are
        fixed (see the brief).

        Contract:
          - the document key is `record.doc_id`, unmodified and unprefixed
          - the body is the record serialized from `.model_dump()`, not a dict
            assembled field by field
          - `root_summary_embedding` is **not** stored: it is written at ingest
            and read by nothing, and it is roughly half the bytes of a fully
            populated record
          - `description_embedding` is stored as a plain JSON array of floats
          - calling this twice with the same `doc_id` replaces the document
            outright; no field from the first write survives into the second

        Idempotent by construction, because re-ingest is normal: a document
        whose source file changed is re-parsed and written again under the same
        `doc_id`.

        Pick a durability level explicitly and say why in a comment — including
        what would make you change it. docs/briefs/lesson-03.md has the measured
        numbers and an explanation of why this cluster cannot validate the
        choice.
        """
        raise NotImplementedError("Lesson 3")


__all__ = ["DocumentStore"]
