"""Lesson 3 acceptance: single-document KV upsert.

Matt implements `librarian/cb/store.py::DocumentStore.upsert_document`; this
file decides whether it works.

    pytest -m integration --lesson 3 -q

As in Lessons 1 and 2 there is no `import couchbase` here. Reads go through the
handles a `Cluster` hands out, and SDK exception identities are asserted through
the MRO by class *name*, so every SDK import in this repo stays on Matt's side
of the hard rule (CURRICULUM.md).

The store is never used to verify the store: every assertion reads the document
back through the SDK's own `collection.get()`, so an `upsert_document` that
quietly writes nothing has nowhere to hide.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone

import pytest

from librarian.backends import StoreProtocol
from librarian.cb.client import create_cluster
from librarian.cb.manager import CollectionManager
from librarian.cb.names import DOCUMENTS
from librarian.cb.store import DocumentStore
from librarian.config import Config
from librarian.models import DocumentRecord

pytestmark = pytest.mark.lesson(3)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def provisioned_config(couchbase_settings) -> Config:
    """A Config pointed at a throwaway scope with the collections already made.

    Provisioned through Lesson 2's `CollectionManager` rather than by hand: it
    is the code that owns this job, it is green, and using it here means a
    regression in it shows up as a Lesson 3 failure too.
    """
    scope = f"test_l03_{uuid.uuid4().hex[:8]}"
    config = Config({"couchbase": {**couchbase_settings, "scope": scope}})
    cluster = create_cluster(config)
    CollectionManager(cluster, config).ensure_collections()
    yield config
    try:
        cluster.bucket(config.couchbase_bucket).collections().drop_scope(scope)
    except Exception:  # noqa: BLE001 — teardown must not mask a real failure
        pass


@pytest.fixture
def kv(provisioned_config):
    """The raw `documents` collection handle, for reading behind the store's back."""
    return (
        create_cluster(provisioned_config)
        .bucket(provisioned_config.couchbase_bucket)
        .scope(provisioned_config.couchbase_scope)
        .collection(DOCUMENTS)
    )


@pytest.fixture
def store(provisioned_config) -> DocumentStore:
    return DocumentStore(create_cluster(provisioned_config), provisioned_config)


@pytest.fixture
def record() -> DocumentRecord:
    """A fully populated record — every field set to something distinguishable.

    Defaults are the enemy of a roundtrip test: a field that is `0` both before
    and after tells you nothing about whether it was written.
    """
    return DocumentRecord(
        doc_id="a3f9c1e2-l03",
        doc_name="Gödel, Escher, Bach",
        source_type="pdf",
        source_path="hofstadter/geb.pdf",
        file_sha256="9f" * 32,
        file_size=48_211_904,
        domain="cognitive-science",
        collection="hofstadter",
        created_at=datetime(2024, 3, 1, 12, 30, 45, 123456, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc),
        page_count=777,
        language="en",
        tags=["recursion", "self-reference", "formal-systems"],
        description="A metaphorical fugue on minds and machines.",
        description_embedding=[round(0.001 * i, 6) for i in range(1024)],
        root_summary="Strange loops as the substrate of consciousness.",
        root_summary_embedding=[0.5] * 1024,
        top_level_titles="Introduction; Three-Part Invention; Chapter I",
        tree_depth=4,
        node_count=812,
        embedding_model="openai/BAAI/bge-m3",
        summary_model="openai/gpt-4o-mini",
    )


# ── helpers ──────────────────────────────────────────────────────────────────

#: Written by `upsert_document`; excluded from the stored body on purpose.
_DROPPED = "root_summary_embedding"

#: Not compared literally — a datetime has more than one honest JSON form.
_TEMPORAL = ("created_at", "updated_at")


def _as_datetime(value, field: str) -> datetime:
    """Interpret a stored timestamp, whatever JSON-native shape it took.

    ISO-8601 strings are what `model_dump(mode="json")` produces and what
    `to_os_doc()` produced before it, but a custom transcoder storing epoch
    seconds is a legitimate answer to this lesson's design question. Both are
    accepted; anything that is not JSON-native is not, since it means the
    document did not survive the trip as data.
    """
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"{field} stored as {value!r}, which is not a parseable datetime")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    pytest.fail(
        f"{field} came back as {type(value).__name__} ({value!r}). Couchbase stores "
        "JSON, so a datetime has to be encoded on the way in — see the "
        "TypeError trap in docs/briefs/lesson-03.md."
    )


# ── the roundtrip ────────────────────────────────────────────────────────────


def test_upsert_document_roundtrip(store, kv, record):
    """Every field of the record survives, under the key `doc_id`.

    The read is a bare `collection.get(record.doc_id)` — no prefix, no key
    derivation, no query. If the key needed anything done to it, this fails.
    """
    store.upsert_document(record)

    stored = kv.get(record.doc_id).content_as[dict]
    expected = record.model_dump()

    missing = set(expected) - set(stored) - {_DROPPED}
    assert not missing, (
        f"fields absent from the stored document: {sorted(missing)}. A body "
        "built field by field goes stale the moment DocumentRecord grows — "
        "the rubric asks for .model_dump()."
    )

    for field, want in expected.items():
        if field == _DROPPED:
            continue
        if field in _TEMPORAL:
            assert _as_datetime(stored[field], field) == want, f"{field} changed value"
        else:
            assert stored[field] == want, (
                f"{field}: stored {stored[field]!r}, record had {want!r}"
            )


def test_the_write_is_readable_with_no_polling(store, kv, record):
    """No refresh interval, no scan consistency, no sleep.

    Worth pinning as its own test because Lesson 2 taught the opposite reflex:
    `test_status_counts_documents` had to poll for twenty seconds. That lag is
    a property of the *query service*, not of Couchbase. A KV read of a key you
    just wrote is served from the same node that accepted the write, and is
    immediate — this is the guarantee the whole ingestion pipeline rests on.
    """
    store.upsert_document(record)
    assert kv.get(record.doc_id).content_as[dict]["doc_name"] == record.doc_name


def test_root_summary_embedding_is_not_stored(store, kv, record):
    """The one field the migration drops.

    Written at ingest, read by nothing, and ~49% of the serialized bytes of a
    populated record (27,112 -> 13,772, measured). It is also the trap in
    reusing `DocumentRecord.to_os_doc()`: that serializer works perfectly
    against Couchbase and keeps this field, so a roundtrip test alone would
    call it a pass.
    """
    store.upsert_document(record)

    stored = kv.get(record.doc_id).content_as[dict]
    assert _DROPPED not in stored, (
        f"{_DROPPED} is still in the stored document ({len(stored[_DROPPED])} "
        "floats). See CURRICULUM.md 'Drop in migration'."
    )
    assert stored["root_summary"] == record.root_summary, (
        "root_summary is the human-readable text and stays — only the "
        "embedding of it goes."
    )


def test_embedding_round_trips_as_a_plain_float_array(store, kv, record):
    """A JSON array of numbers, not a string, not base64, not truncated.

    Lesson 9's vector index reads this field directly. Anything that isn't a
    bare list of floats is a problem deferred to the lesson least able to
    absorb it.
    """
    store.upsert_document(record)

    embedding = kv.get(record.doc_id).content_as[dict]["description_embedding"]
    assert isinstance(embedding, list), (
        f"description_embedding came back as {type(embedding).__name__}"
    )
    assert len(embedding) == 1024, f"expected 1024 dimensions, got {len(embedding)}"
    assert all(isinstance(v, float) for v in embedding), (
        "every element should be a JSON number"
    )
    assert embedding == record.description_embedding, (
        "values changed in transit — floats round-trip exactly through the "
        "default transcoder, so a mismatch means something re-encoded them"
    )


# ── overwrite semantics ──────────────────────────────────────────────────────


def test_upsert_replaces_the_whole_document(store, kv, record):
    """The second write wins, and nothing from the first survives it.

    Re-ingest is the normal case: a source file changes, the tree is rebuilt,
    and the same `doc_id` is written again with fewer tags, a shorter summary,
    or no embedding at all. A merge — subdocument mutation, or a read-modify-
    write — leaves the old values in place and produces a document that matches
    no version of the source file. `upsert` replaces; that is why the Protocol
    names it that.
    """
    store.upsert_document(record)

    revised = record.model_copy(
        update={
            "doc_name": "Gödel, Escher, Bach (2nd ed.)",
            "page_count": 800,
            "tags": ["recursion"],
            "description_embedding": None,
            "domain": None,
        }
    )
    store.upsert_document(revised)

    stored = kv.get(record.doc_id).content_as[dict]
    assert stored["doc_name"] == "Gödel, Escher, Bach (2nd ed.)"
    assert stored["page_count"] == 800
    assert stored["tags"] == ["recursion"], (
        f"tags are {stored['tags']!r} — the old list survived the second write"
    )
    assert stored["description_embedding"] is None, (
        "the revised record has no embedding, so the stored document must not "
        "have one either; a stale 1024-float vector attached to new text is "
        "worse than no vector at all"
    )
    assert stored["domain"] is None


def test_upsert_evicts_fields_that_are_no_longer_in_the_record(store, kv, record):
    """A document written by older code must not keep its extra fields.

    This is the case that separates a real `upsert` from a read-modify-write
    or a subdocument mutation, and the previous test does *not* catch it:
    `model_dump()` emits every field of `DocumentRecord` including the `None`
    ones, so merging one dump over another happens to produce the right answer.
    It stops producing the right answer the moment the stored document contains
    a key the model no longer has.

    Which is not hypothetical — it is precisely this migration. Every document
    in the corpus today was written with `root_summary_embedding` in it, and
    re-ingesting one has to remove it, not preserve it under a new body.
    """
    kv.upsert(
        record.doc_id,
        {
            "doc_id": record.doc_id,
            "doc_name": "written by the OpenSearch-era pipeline",
            "root_summary_embedding": [0.25] * 1024,
            "content_search": "a field that no longer exists in the model",
        },
    )

    store.upsert_document(record)

    stored = kv.get(record.doc_id).content_as[dict]
    leftovers = set(stored) - set(record.model_dump())
    assert not leftovers, (
        f"fields from the previous document survived the write: "
        f"{sorted(leftovers)}. `upsert` replaces the whole value — a "
        "read-modify-write or a subdocument mutation would leave these behind."
    )
    assert _DROPPED not in stored


def test_upsert_accepts_a_minimal_record(store, kv):
    """Only the two required fields; everything else default or None.

    This is what a document looks like when embeddings are disabled — an
    entirely supported configuration (`models.embedding` empty turns the vector
    path off). `None` has to reach the document as JSON `null` rather than
    being dropped or crashing the encoder.
    """
    minimal = DocumentRecord(doc_id="minimal-l03", doc_name="Untitled")

    store.upsert_document(minimal)

    stored = kv.get("minimal-l03").content_as[dict]
    assert stored["doc_name"] == "Untitled"
    assert "description_embedding" in stored, (
        "an unset embedding should round-trip as null, not vanish — Lesson 9 "
        "has to distinguish 'not embedded yet' from 'field never written'"
    )
    assert stored["description_embedding"] is None
    assert stored["page_count"] == 0, "model defaults are part of the record"


# ── the rubric lines with teeth ──────────────────────────────────────────────


def test_conforms_to_the_store_protocol(store, record):
    """`upsert_document` must be substitutable for the OpenSearch one.

    Lesson 11 swaps the backend at the construction site, so the ingestion
    pipeline has to be unable to tell the difference. A different name, an
    extra required argument, or a return value the caller is expected to check
    are all silent divergences today and a rewrite later.

    Checked by signature rather than `isinstance`: `StoreProtocol` is not
    `@runtime_checkable`, and even if it were, a runtime protocol check only
    asks whether the *names* exist — which they deliberately don't yet, since
    the other seven methods belong to Lessons 4-6.
    """
    ours = inspect.signature(DocumentStore.upsert_document)
    theirs = inspect.signature(StoreProtocol.upsert_document)

    assert list(ours.parameters) == list(theirs.parameters), (
        f"parameters are {list(ours.parameters)}, Protocol says "
        f"{list(theirs.parameters)}"
    )
    assert store.upsert_document(record) is None, (
        "the Protocol returns None. Returning a cas or a MutationToken is a "
        "reasonable thing to want, but it is a change to backends.py that the "
        "OpenSearch implementation would also have to make — a Lesson 11 "
        "conversation, not a quiet divergence."
    )


def test_collection_handles_are_derived_once(provisioned_config, record):
    """`bucket()` / `scope()` / `collection()` belong in `__init__`.

    Lesson 4 calls into this class ~10k times per ingested document. Re-walking
    `bucket -> scope -> collection` inside the write is the N+1 of this
    codebase, and it is invisible in a test that writes one document — so it
    gets pinned here, while the class is small enough to fix cheaply.

    The cluster is wrapped in a counting proxy; the counters are read only
    *after* construction, so any amount of setup work in `__init__` is fine.
    """

    class _Counter:
        def __init__(self):
            self.reset()

        def reset(self):
            self.bucket = self.scope = self.collection = 0

        def totals(self):
            return (self.bucket, self.scope, self.collection)

        def __str__(self):
            return (
                f"bucket()x{self.bucket}, scope()x{self.scope}, "
                f"collection()x{self.collection}"
            )

    counts = _Counter()

    class _Proxy:
        """Forwards everything, tallying the three lookups we care about."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def bucket(self, *a, **kw):
            counts.bucket += 1
            return _Proxy(self._wrapped.bucket(*a, **kw))

        def scope(self, *a, **kw):
            counts.scope += 1
            return _Proxy(self._wrapped.scope(*a, **kw))

        def collection(self, *a, **kw):
            counts.collection += 1
            return self._wrapped.collection(*a, **kw)

        def default_scope(self, *a, **kw):
            counts.scope += 1
            return _Proxy(self._wrapped.default_scope(*a, **kw))

        def default_collection(self, *a, **kw):
            counts.collection += 1
            return self._wrapped.default_collection(*a, **kw)

    subject = DocumentStore(_Proxy(create_cluster(provisioned_config)), provisioned_config)

    counts.reset()  # ignore construction; only per-write lookups are the point
    for i in range(5):
        subject.upsert_document(record.model_copy(update={"doc_id": f"handle-{i}"}))

    assert counts.totals() == (0, 0, 0), (
        f"5 upserts re-derived handles: {counts}. These are cheap object "
        "constructions, not round trips — what is being graded is that the "
        "handle is state of the store, because Lesson 4 multiplies this by 10k."
    )
