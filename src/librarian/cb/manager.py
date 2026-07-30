"""Bucket/scope/collection lifecycle — the Couchbase port of IndexManager.

The analogue of `opensearch/index_manager.py`: create the storage layout, tear
it down, and report on it. `librarian init-indexes` and `librarian status` are
the two CLI commands that consume this (rewired in Lesson 11 — leave cli.py
alone for now).

Acceptance tests: tests/integration/test_l02_manager.py
"""

from __future__ import annotations

import logging

from ..config import Config
from .names import ALL_COLLECTIONS

_log = logging.getLogger("librarian.cb")


class CollectionManager:
    """Owns the librarian's collections inside one bucket + scope.

    Naming note: the SDK has its own `CollectionManager`
    (`couchbase.management.collections`), which is what `bucket.collections()`
    returns. This class is the librarian-shaped wrapper around it — same name
    on purpose, since it plays the same role `IndexManager` plays for
    OpenSearch, but be precise in your own code about which one a variable
    holds.

    Scope of responsibility — the bucket is NOT yours to create. `__init__`
    and the methods below assume `config.couchbase_bucket` already exists and
    should fail loudly if it doesn't. See the BRIEF for why that line sits
    where it does.
    """

    def __init__(self, cluster, config: Config) -> None:
        """
        Args:
            cluster: a live `Cluster` from `cb.client.create_cluster`.
            config:  supplies `couchbase_bucket` and `couchbase_scope`.

        Hold whatever handles you need here rather than re-deriving them in
        every method — the Lesson 3 rubric line "bucket/scope/collection
        references held, not re-looked-up per call" starts being true here.
        """
        raise NotImplementedError

    def ensure_collections(self) -> list[tuple[str, str]]:
        """Create the scope (if needed) and all of `ALL_COLLECTIONS`.

        Idempotent: calling it on an already-provisioned cluster is a no-op
        that reports what it found. This is the operation `librarian
        init-indexes` runs, and it runs on every deploy.

        Returns:
            One `(collection_name, action)` pair per collection, where action
            is `"created"` or `"skipped"` — same shape as
            `IndexManager.create_all`, so the CLI's print loop is unchanged::

                [("documents", "created"), ("tree_nodes", "skipped"), ...]

        Note the scope does not appear in the returned list (log it instead);
        the CLI reports on collections.
        """
        raise NotImplementedError

    def reset(self) -> list[tuple[str, str]]:
        """Drop every librarian collection and recreate it empty.

        Destructive — this is `librarian init-indexes --reset`, the "I want a
        clean corpus" button.

        Returns:
            `(name, "dropped")` pairs followed by the result of
            `ensure_collections()`, mirroring `IndexManager.reset_all`.

        There is a trap here that the tests will find; the BRIEF names it.
        """
        raise NotImplementedError

    def status(self) -> dict[str, dict]:
        """Report on each collection, keyed by name.

        Returns exactly this shape — `librarian status` formats it into a
        table, and the tests assert on it::

            {
                "documents":    {"exists": True,  "doc_count": 1204},
                "tree_nodes":   {"exists": True,  "doc_count": 88231},
                "page_content": {"exists": False, "doc_count": None},
            }

        `doc_count` is `None` when, and only when, the collection is missing —
        a missing collection is not an empty one, and reporting 0 for it hides
        a failed deploy.

        The count is *approximate* and lags recent writes by a few hundred
        milliseconds. That is not a defect in your implementation and it is not
        fixable from here: with no index on the collection, the query service
        answers `COUNT(*)` from collection metadata that trails the KV writes.
        Measured on this cluster, 300 documents written and immediately counted
        report somewhere around 210-290, and `scan_consistency=request_plus`
        does not help (sequential scans don't participate in that protocol).
        Lesson 5 adds a primary/GSI index, at which point the same query
        becomes exact and this docstring gets to change.

        Say so in the docstring you write. An operator reading `librarian
        status` after an ingest needs to know whether a low number means data
        loss or just lag, and this function is the only place that can tell
        them.
        """
        raise NotImplementedError


__all__ = ["CollectionManager", "ALL_COLLECTIONS"]
