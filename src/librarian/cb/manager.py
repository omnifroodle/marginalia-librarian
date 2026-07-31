"""Bucket/scope/collection lifecycle — the Couchbase port of IndexManager.

The analogue of `opensearch/index_manager.py`: create the storage layout, tear
it down, and report on it. `librarian init-indexes` and `librarian status` are
the two CLI commands that consume this .
"""

from __future__ import annotations

import logging

from couchbase.exceptions import (
    CollectionAlreadyExistsException,
    CollectionNotFoundException,
    ScopeAlreadyExistsException,
    ScopeNotFoundException,
)

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
    """

    def __init__(self, cluster, config: Config) -> None:
        """
        Args:
            cluster: a live `Cluster` from `cb.client.create_cluster`.
            config:  supplies `couchbase_bucket` and `couchbase_scope`.

        """
        self.bucket_name = config.couchbase_bucket
        self.scope_name = config.couchbase_scope
        self.cluster = cluster
        self.collections = cluster.bucket(self.bucket_name).collections()

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

        # check for our scope, create if not
        # the most common scope is `_default`, which should always exist,
        # if not something is very wrong with the cluster. Trying to create it
        # will fail, we intentionally don't check for this case and let the exception bubble up.
        if self._collection_names() is None:
            # check for our scope, create if not
            # should only fail if somehow called twice in parallel
            try:
                self.collections.create_scope(self.scope_name)
                _log.info("Created scope: %s", self.scope_name)
            except ScopeAlreadyExistsException:
                _log.debug("Scope %s already exists", self.scope_name)

        else:
            _log.debug("Scope %s already exists", self.scope_name)

        # verify each collection exists, create if not
        results = []
        for collection_name in ALL_COLLECTIONS:
            try:
                self.collections.create_collection(
                    self.scope_name,
                    collection_name
                )
                results.append((collection_name, "created"))
            except CollectionAlreadyExistsException:
                _log.debug("Collection %s already exists", collection_name)
                results.append((collection_name, "skipped"))
        return results

    def reset(self) -> list[tuple[str, str]]:
        """Drop every librarian collection and recreate it empty.

        Destructive — this is `librarian init-indexes --reset`, the "I want a
        clean corpus" button.

        Returns:
            `(name, "dropped")` pairs followed by the result of
            `ensure_collections()`, mirroring `IndexManager.reset_all`.
        """

        results = []
        for collection in ALL_COLLECTIONS:
            try:
                self.collections.drop_collection(
                    self.scope_name,
                    collection
                )
                _log.info("Dropped collection: %s", collection)
                results.append((collection, "dropped"))
            except (CollectionNotFoundException, ScopeNotFoundException) as e:
                _log.debug("Collection %s does not exist or error: %s", collection, e)
                results.append((collection, "skipped"))

        return results + self.ensure_collections()

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

        Also note that `doc_count` is approximate and may lag behind recent writes.
        """
        results = {}
        live_collections = self._collection_names() or set()
        for collection in ALL_COLLECTIONS:
            if collection not in live_collections:
                results[collection] = {"exists": False, "doc_count": None}
            else:
                # bucket_name comes from config, so it is assumed sanitized here
                result = self.cluster.query(
                    f"SELECT RAW COUNT(*) AS doc_count FROM `{self.bucket_name}`.`{self.scope_name}`.`{collection}`"
                )
                result = next(iter(result))
                results[collection] = {"exists": True, "doc_count": result}

        return results

    def _collection_names(self) -> set[str] | None:
        """Return the set of collection names that exist in the scope."""
        for scope in self.collections.get_all_scopes():
            if scope.name == self.scope_name:
                return {c.name for c in scope.collections}
        return None
    
__all__ = ["CollectionManager", "ALL_COLLECTIONS"]
