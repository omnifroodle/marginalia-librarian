"""Lesson 2 acceptance: bucket/scope/collection management.

Matt implements `librarian/cb/manager.py::CollectionManager`; this file decides
whether it works.

    pytest -m integration tests/integration/test_l02_manager.py -q

As in Lesson 1, there is no `import couchbase` here — SDK exception identities
are asserted through the MRO by class *name*, so every SDK import in this repo
stays on Matt's side of the hard rule (CURRICULUM.md).

Isolation: each test gets its own throwaway scope, created and dropped by the
`scoped_config` fixture, so a failed run can't leave the shared `_default`
scope littered and two runs can't fight. The one test that deliberately targets
`_default` says so in its name.
"""

from __future__ import annotations

import time
import uuid

import pytest

from librarian.cb.client import create_cluster
from librarian.cb.manager import CollectionManager
from librarian.cb.names import ALL_COLLECTIONS, DOCUMENTS, PAGE_CONTENT, TREE_NODES
from librarian.config import Config

pytestmark = pytest.mark.lesson(2)


# ── helpers ──────────────────────────────────────────────────────────────────


def _exception_names(exc: BaseException) -> set[str]:
    """Every class name in the exception's MRO — an import-free isinstance."""
    return {cls.__name__ for cls in type(exc).__mro__}


def _collections_in(cluster, config: Config) -> set[str]:
    """Collection names present in the configured scope, read straight from the
    cluster rather than from anything the manager returned.

    A manager that reports "created" without creating has to fail somewhere;
    this is that somewhere.
    """
    manifest = cluster.bucket(config.couchbase_bucket).collections().get_all_scopes()
    for scope in manifest:
        if scope.name == config.couchbase_scope:
            return {c.name for c in scope.collections}
    return set()


def _scope_exists(cluster, config: Config) -> bool:
    manifest = cluster.bucket(config.couchbase_bucket).collections().get_all_scopes()
    return any(s.name == config.couchbase_scope for s in manifest)


def _drop_scope_quietly(couchbase_settings, scope: str) -> None:
    """Teardown helper. Broad except is deliberate and confined to teardown:
    the scope may never have been created (the test under repair raised first),
    and a cleanup failure must not mask the real assertion error.
    """
    try:
        cluster = create_cluster(Config({"couchbase": {**couchbase_settings, "scope": scope}}))
        cluster.bucket(couchbase_settings["bucket"]).collections().drop_scope(scope)
    except Exception:  # noqa: BLE001 — see docstring
        pass


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def scoped_config(couchbase_settings) -> Config:
    """A Config pointed at a scope that does not exist yet, dropped afterwards.

    Starting from "the scope is missing" is the honest starting state: on a
    fresh deploy `ensure_collections()` has to create the scope too, and a
    fixture that pre-created it would hide that requirement.
    """
    scope = f"test_l02_{uuid.uuid4().hex[:8]}"
    yield Config({"couchbase": {**couchbase_settings, "scope": scope}})
    _drop_scope_quietly(couchbase_settings, scope)


@pytest.fixture
def default_scope_config(couchbase_settings) -> Config:
    """A Config pointed at `_default`, cleaned up collection by collection.

    `_default` cannot be dropped (that is the trap this exercises), so teardown
    has to remove the collections individually.
    """
    config = Config({"couchbase": {**couchbase_settings, "scope": "_default"}})
    yield config
    try:
        manager = create_cluster(config).bucket(config.couchbase_bucket).collections()
        for name in ALL_COLLECTIONS:
            try:
                manager.drop_collection("_default", name)
            except Exception:  # noqa: BLE001 — teardown; may not exist
                pass
    except Exception:  # noqa: BLE001 — teardown must not mask a real failure
        pass


# ── ensure_collections ───────────────────────────────────────────────────────


def test_ensure_collections_creates_the_scope_and_all_collections(scoped_config):
    cluster = create_cluster(scoped_config)
    manager = CollectionManager(cluster, scoped_config)

    results = manager.ensure_collections()

    assert _scope_exists(cluster, scoped_config), (
        f"scope {scoped_config.couchbase_scope!r} was not created. On a fresh "
        "deploy the scope won't exist either — ensure_collections() owns it."
    )
    assert _collections_in(cluster, scoped_config) >= set(ALL_COLLECTIONS)

    assert sorted(results) == sorted((name, "created") for name in ALL_COLLECTIONS), (
        f"expected every collection reported as 'created', got {results}"
    )


def test_ensure_collections_is_idempotent(scoped_config):
    """The second call must be a clean no-op, not an exception and not a churn.

    `librarian init-indexes` runs on every deploy, most of them against a
    cluster that is already provisioned.
    """
    cluster = create_cluster(scoped_config)
    manager = CollectionManager(cluster, scoped_config)

    manager.ensure_collections()
    second = manager.ensure_collections()

    assert sorted(second) == sorted((name, "skipped") for name in ALL_COLLECTIONS), (
        f"second ensure_collections() reported {second} — expected all 'skipped'"
    )
    assert _collections_in(cluster, scoped_config) >= set(ALL_COLLECTIONS)


def test_ensure_collections_reports_state_per_collection(scoped_config):
    """A partly-provisioned scope must be reported collection by collection.

    The other two ensure tests only cover the endpoints — nothing exists, or
    everything does — and an all-or-nothing implementation ("did I have to
    create the scope? then everything is new") passes both while being wrong.

    Partial state is not a corner case: it is what every existing deployment
    looks like the day a lesson adds a fourth name to `ALL_COLLECTIONS`. Three
    present, one missing, and `init-indexes` has to say so accurately.
    """
    cluster = create_cluster(scoped_config)

    # Provision one collection behind the manager's back, via the SDK's own
    # CollectionManager, so it is genuinely pre-existing.
    sdk = cluster.bucket(scoped_config.couchbase_bucket).collections()
    sdk.create_scope(scoped_config.couchbase_scope)
    sdk.create_collection(scoped_config.couchbase_scope, TREE_NODES)

    results = dict(CollectionManager(cluster, scoped_config).ensure_collections())

    assert results[TREE_NODES] == "skipped", (
        f"{TREE_NODES} existed before the call; got {results[TREE_NODES]!r}"
    )
    assert results[DOCUMENTS] == "created"
    assert results[PAGE_CONTENT] == "created"


def test_collections_are_usable_when_ensure_returns(scoped_config):
    """No sleep between provisioning and using the result.

    Same contract as Lesson 1's "the returned cluster is usable right away".
    On this single-node cluster the manifest propagates in about a millisecond,
    so a naive implementation passes — the test is here to pin the contract
    before Capella and a multi-node cluster make the window real, not because
    it is hard to satisfy locally.
    """
    cluster = create_cluster(scoped_config)
    CollectionManager(cluster, scoped_config).ensure_collections()

    scope = cluster.bucket(scoped_config.couchbase_bucket).scope(
        scoped_config.couchbase_scope
    )
    for name in ALL_COLLECTIONS:
        collection = scope.collection(name)
        collection.upsert("smoke::1", {"ok": True})
        assert collection.get("smoke::1").content_as[dict] == {"ok": True}


# ── reset ────────────────────────────────────────────────────────────────────


def test_reset_drops_and_recreates_empty_collections(scoped_config):
    cluster = create_cluster(scoped_config)
    manager = CollectionManager(cluster, scoped_config)
    manager.ensure_collections()

    scope = cluster.bucket(scoped_config.couchbase_bucket).scope(
        scoped_config.couchbase_scope
    )
    scope.collection(DOCUMENTS).upsert("survivor::1", {"data": "should not survive"})

    results = manager.reset()

    assert _collections_in(cluster, scoped_config) >= set(ALL_COLLECTIONS), (
        "reset() dropped the collections but did not recreate them"
    )
    assert [action for _, action in results].count("dropped") == len(ALL_COLLECTIONS), (
        f"expected a 'dropped' entry per collection, got {results}"
    )

    with pytest.raises(Exception) as exc_info:  # noqa: B017 — narrowed below
        scope.collection(DOCUMENTS).get("survivor::1")
    assert "DocumentNotFoundException" in _exception_names(exc_info.value), (
        f"expected the document to be gone, got "
        f"{type(exc_info.value).__name__}: {exc_info.value}"
    )


def test_reset_works_in_the_default_scope(default_scope_config):
    """`_default` is the configured scope out of the box — reset must work there.

    The obvious implementation of reset() ("drop the scope, recreate it") is a
    one-liner that passes every other test in this file and fails only here,
    because the cluster refuses to delete `_default`:

        HTTP 400 {"errors":{"_":"Deleting _default scope is not allowed"}}

    which the SDK surfaces as `UnsupportedOperation`, not as anything with
    "scope" in the name.
    """
    cluster = create_cluster(default_scope_config)
    manager = CollectionManager(cluster, default_scope_config)
    manager.ensure_collections()

    manager.reset()

    assert _collections_in(cluster, default_scope_config) >= set(ALL_COLLECTIONS)


# ── status ───────────────────────────────────────────────────────────────────


def test_status_reports_missing_collections_before_they_exist(scoped_config):
    """status() has to work on an unprovisioned cluster — that is when an
    operator is most likely to run it."""
    cluster = create_cluster(scoped_config)
    manager = CollectionManager(cluster, scoped_config)

    status = manager.status()

    assert set(status) == set(ALL_COLLECTIONS), (
        f"status() should report exactly the librarian's collections, got "
        f"{sorted(status)}. The bucket also contains a `_system` scope with "
        "`_query` and `_mobile` collections that are not ours to report on."
    )
    for name, info in status.items():
        assert info["exists"] is False, f"{name}: {info}"
        assert info["doc_count"] is None, (
            f"{name}: doc_count should be None for a missing collection, not "
            f"{info['doc_count']!r} — 0 would read as 'provisioned and empty'."
        )


def test_status_counts_documents(scoped_config):
    """The count must be real, and must converge on the truth.

    Deliberately *not* asserted immediately. Without an index on the
    collection there is no way to get an exact count: the query service serves
    `COUNT(*)` from collection metadata that trails the KV writes, and
    `scan_consistency=request_plus` does not fix it — measured on this
    cluster, 300 documents written and counted in the same breath report
    anywhere from ~210 to ~290. Lesson 5's primary index is what makes it
    exact; this test is written to survive that change without edits.

    So: poll. A count that converges is being read from the cluster. A
    hardcoded or fabricated one isn't — which is also why `tree_nodes` is
    checked for a genuine 0 in the same call.
    """
    cluster = create_cluster(scoped_config)
    manager = CollectionManager(cluster, scoped_config)
    manager.ensure_collections()

    collection = (
        cluster.bucket(scoped_config.couchbase_bucket)
        .scope(scoped_config.couchbase_scope)
        .collection(DOCUMENTS)
    )
    for i in range(300):
        collection.upsert(f"doc::{i}", {"i": i})

    deadline = time.monotonic() + 20.0
    while True:
        status = manager.status()
        if status[DOCUMENTS]["doc_count"] == 300 or time.monotonic() > deadline:
            break
        time.sleep(0.25)

    assert status[DOCUMENTS]["exists"] is True
    assert status[DOCUMENTS]["doc_count"] == 300, (
        f"status() settled on {status[DOCUMENTS]['doc_count']} of 300 documents "
        "after 20s. Lag explains a count that is low for a moment; it does not "
        "explain one that never catches up."
    )
    assert status["tree_nodes"]["doc_count"] == 0, (
        "an existing but empty collection counts 0, not None"
    )


# ── the bucket is not ours to create ─────────────────────────────────────────


def test_a_missing_bucket_fails_loudly(couchbase_settings):
    """Creating the bucket is the cluster operator's job (and on Capella, the
    control plane's). Pointed at a bucket that doesn't exist, the manager must
    surface `BucketNotFoundException` rather than creating one or reporting an
    empty-but-fine status.
    """
    config = Config(
        {
            "couchbase": {
                **couchbase_settings,
                "bucket": f"no-such-bucket-{uuid.uuid4().hex[:8]}",
            }
        }
    )
    cluster = create_cluster(config)

    with pytest.raises(Exception) as exc_info:  # noqa: B017 — narrowed below
        manager = CollectionManager(cluster, config)
        manager.ensure_collections()

    assert "BucketNotFoundException" in _exception_names(exc_info.value), (
        f"expected BucketNotFoundException, got "
        f"{type(exc_info.value).__name__}: {exc_info.value}"
    )
