# Lesson 3 — Single-document KV upsert

*Delivered 2026-07-31. Archived as delivered; see [README.md](README.md) for the
section-by-section rationale.*

---

## The mental model

In OpenSearch every write went through an index. `client.index(...)` handed a
document to an analysis pipeline, which tokenized it, updated inverted indexes,
and eventually — near-real-time, refresh interval permitting — made it
retrievable. There was one road in, and it was the search road. You felt this
in Lesson 2: `COUNT(*)` couldn't tell you the truth because the *query service*
hadn't caught up with the *data service*.

Couchbase inverts that. The key-value layer is the primary access path, and
everything else — query, search, vector, analytics — is a consumer that
subscribes to it downstream:

```
   your upsert ──► KV (memory-first, hash-partitioned into 1024 vBuckets)
                    │
                    ├──► disk
                    ├──► replicas
                    ├──► GSI / query service      (Lesson 5, 6)
                    ├──► FTS                      (Lesson 7–9)
                    └──► Eventing, Analytics, XDCR…
```

Three consequences worth internalizing now, because they shape every later
lesson:

**A KV get is not a search.** Give it the key, get the document — a hash
lookup, no index consulted, no consistency question to ask. Sub-millisecond and
flat, whether the collection holds ten documents or ten million. In OpenSearch,
`get` by id was a special case of the search machinery; here it's the base case
and search is the special one.

**A KV write is immediately readable by key**, and *only* by key. There is no
refresh interval to wait out — read-your-own-write is guaranteed on the KV
path. The lag you fought in Lesson 2 lives entirely in the downstream
consumers. This is why `test_collections_are_usable_when_ensure_returns` could
upsert-then-get with no sleep while `test_status_counts_documents` had to poll.

**The key is not in the document.** OpenSearch's `_id` and the `doc_id` field
were separate things and you dutifully wrote both. Same here — the key lives in
metadata (`META().id` in SQL++, `GetResult.key` in the SDK) and is not
automatically a field in the body. Whether you *also* keep `doc_id` in the body
is a choice; Lesson 5 and 6 will want to filter on it in SQL++, so keeping it
is the cheap answer.

Reading:
[KV operations](https://docs.couchbase.com/python-sdk/current/howtos/kv-operations.html)
·
[Documents & keys](https://docs.couchbase.com/server/current/learn/data/document-data-model.html)
·
[Durability](https://docs.couchbase.com/server/current/learn/data/durability.html)

## What you write

[src/librarian/cb/store.py](../../src/librarian/cb/store.py) — a `DocumentStore`
that will grow into the whole `StoreProtocol`. This lesson is one method of it.

| | |
|---|---|
| `DocumentStore(cluster, config)` | hold the handles; the collection reference is derived **once** |
| `upsert_document(record: DocumentRecord) -> None` | key = `record.doc_id`; body = the record minus `root_summary_embedding` |

The Protocol it has to satisfy is
[backends.py](../../src/librarian/backends.py)`::StoreProtocol` — same surface
`opensearch/store.py::DocumentStore` implements today, so the ingestion
pipeline can be pointed at either one in Lesson 11. Note it returns `None`:
callers get no `cas`, no `MutationToken`. If you want those for later, that is
a Protocol change and therefore a Lesson 11 conversation, not a quiet
divergence now.

`root_summary_embedding` is dropped because nothing ever queried it — it was
written at ingest and read by no one. It is 1024 floats, and on a fully
populated record it is **49% of the serialized bytes** (27,112 → 13,772 bytes,
measured). Dropping it is the single largest storage win in the migration.

## The real decision: where does serialization live?

`models.py` already answers this question once, for OpenSearch:

```python
def to_os_doc(self) -> dict:
    """Serialize for OpenSearch, converting datetimes to ISO strings."""
    d = self.model_dump()
    d["created_at"] = self.created_at.isoformat()
    d["updated_at"] = self.updated_at.isoformat()
    return d
```

So the precedent is a **backend-shaped serializer method on the model**. You
have three options and they are genuinely different bets:

**A — `to_cb_doc()` next to `to_os_doc()`.** Symmetric, discoverable, and
`upsert_document` becomes one line. The cost: `models.py` is your domain layer,
and it now knows about two storage backends. Every future backend adds a
method. During a migration you carry both, and the pair invites the bug where
someone edits one and not the other.

**B — serialize in the store.** `record.model_dump(mode="json",
exclude={"root_summary_embedding"})` at the call site. The domain model stays
ignorant of storage; the knowledge that Couchbase wants JSON-native types and
doesn't want that one field lives in the module that owns the Couchbase
connection. The cost: it's a slightly opaque one-liner in a method whose
signature says nothing about it, and Lesson 4 will want the same treatment for
two more record types — so you'll be deciding whether to factor it out anyway.

**C — a transcoder.** The SDK lets you register one and hand `upsert` the
pydantic object directly. Genuinely the most Couchbase-native answer, and
genuinely the most machinery: a class to write, a global-ish registration to
place, and an indirection between "I passed a record" and "these bytes landed."
See
[custom transcoders](https://docs.couchbase.com/python-sdk/current/howtos/transcoders-nonjson.html).

`to_os_doc()` is deleted in Lesson 11 along with the rest of `opensearch/`.
Whichever you pick, the question the rubric actually asks is: **which of these
leaves the smallest mess on the day OpenSearch goes away?**

One thing that is *not* a choice: whatever you do, the dict comes from
`model_dump()`. Hand-building `{"doc_id": record.doc_id, "doc_name": ...}`
means every field added to `DocumentRecord` from now on is silently dropped at
write time, and nothing fails.

## Traps I've already measured

All verified on this cluster today; also in [NOTES.md](../../NOTES.md).

- **`model_dump()` alone will not upsert.** `DocumentRecord.created_at` is a
  `datetime`, and the default transcoder is `json.dumps`, which raises
  `TypeError: Object of type datetime is not JSON serializable` — a plain
  Python `TypeError`, not a `CouchbaseException`. It will not be in the SDK
  error docs and it will not look like a Couchbase problem.
  `model_dump(mode="json")` is the one-word fix.
- **`to_os_doc()` will upsert cleanly** — and is wrong here, because it keeps
  `root_summary_embedding`. This trap passes a naive roundtrip test; there's a
  test in the scaffold specifically for it.
- **The key limit is not 250 bytes in a named collection.** Measured: **246
  bytes** in `<bucket>.<named scope>.<named collection>`, 250 in
  `_default._default`. Collections prepend an ID to the key on the wire and
  that ID grows as the cluster's collection count grows, so 246 is a ceiling
  you measured, not a constant you can rely on. Irrelevant for a `doc_id`;
  Lesson 4's `f"{doc_id}::{node_id}::{page_number}"` is where it bites.
- **Values cap at 20 MB.** 21 MB returns `ec=104`. A tree node's text won't hit
  it, but it's the wall behind Lesson 4.
- **Which operation, which exception**: `insert` on an existing key →
  `DocumentExistsException`; `replace` or `get` on a missing key →
  `DocumentNotFoundException`. `upsert` raises neither, which is the entire
  point of it — re-ingest of an existing document must not fail.
- **`MutationResult`** carries `cas`, `mutation_token`, `key`, `success`.
  `GetResult` carries `cas`, `content_as[...]`, `key`, `expiry_time`.
- **Floats round-trip exactly.** A 1024-dim embedding comes back `==` to what
  went in; no precision loss, no need for a custom encoder.
- **`None` round-trips as JSON `null`**, not as a missing field. So an
  unembedded document has `description_embedding: null` in the body, which
  Lesson 9's vector search will have to reckon with.

## What you can't have: an honest durability test

The rubric asks you to defend a durability level in a comment. You cannot
validate that defence on this cluster, and it's important you know that going
in rather than concluding from a green test that you chose well.

Here is what a single node with zero replicas reports (100 upserts of a
1024-dim record, median ms/op):

| | |
|---|---|
| no durability option | **0.41 ms** |
| `DurabilityLevel.NONE` | 0.41 ms |
| `DurabilityLevel.MAJORITY` | 0.47 ms |
| `MAJORITY_AND_PERSIST_TO_ACTIVE` | 1.19 ms |
| `PERSIST_TO_MAJORITY` | 1.21 ms |

Every level succeeds and `MAJORITY` looks nearly free. Both of those are
artifacts. "Majority" of one node is one node, so the guarantee is satisfied
by the write you were doing anyway — no replication round-trip exists to pay
for. On a three-node Capella cluster with one replica, `MAJORITY` means the
write is acknowledged only once a *second* node holds it in memory, and the
number moves. The 2.5× jump for the persist-to-disk levels is the only figure
in that table that will survive the trip to Capella, and even it will grow.

The inverse trap is also live: `MAJORITY` on a cluster whose replicas are
configured but unavailable raises `DurabilityImpossibleException` and your
ingest stops. A level that costs nothing here can be the thing that takes
production down.

So reason from what the data *is*, not from the benchmark. These are
regenerable artifacts of somebody's PDF — an ingest can be re-run. Weigh that
against ~10k KV writes per document tree in Lesson 4, and say in the comment
what you'd revisit if that changed.

## Acceptance

```bash
pytest -m integration --lesson 3 -q
```

[tests/integration/test_l03_store.py](../../tests/integration/test_l03_store.py)
— 9 tests, currently red. All 9 have been run green against a throwaway
implementation before handoff, so **anything still red is your code, not my
scaffold**. Each test gets its own scope, provisioned through your Lesson 2
`CollectionManager` and dropped afterwards.

They were also run against five deliberately *wrong* implementations, and each
one failed exactly the test that names its defect — reusing `to_os_doc()`,
hand-building the body, plain `model_dump()`, read-modify-write instead of
replace, and re-deriving the collection handle per call. The merge one is worth
knowing about: it passed all eight tests I originally wrote, which is how
`test_upsert_evicts_fields_that_are_no_longer_in_the_record` came to exist.

There is no `import couchbase` in the test file. Reads go through
`cluster.bucket(...).scope(...).collection(...).get(...)` — the tests never call
your store to verify your store.

## Rubric

- `model_dump()` at the boundary — no hand-built dict, no field enumerated by
  hand
- Serialization location chosen deliberately, with the Lesson 11 cleanup in mind
- Durability level explicit and defended in a comment, including what would
  change your mind
- `root_summary_embedding` gone from the stored body
- Bucket / scope / collection handles derived once in `__init__`, not per call
- Signature conforms to `StoreProtocol.upsert_document`
- No blanket `except` (enforced on `cb/` by ruff + `test_hygiene.py`)

## Guiding questions

1. `upsert_document` returns `None`, so a caller can't tell a write that
   reached one node from one that reached three. Where in this system would
   that distinction actually be actionable — and if the answer is "nowhere,"
   what does that tell you about the durability level to pick?
2. Re-ingesting a document runs `upsert_document` with a record built fresh
   from the file. `created_at` has `default_factory=datetime.now`. What happens
   to the original creation time, and is that a bug? (It is the same bug in
   `opensearch/store.py` today — the question is whether it's yours to fix
   here.)
3. Lesson 4 adds `upsert_tree_nodes` and `upsert_page_content` to this same
   class, ~10k documents per ingest. Does anything about how you're writing
   `upsert_document` today make that harder — and is there anything you should
   *not* factor out yet, on the grounds that you can't see the shape of it?

## Scope fence

- **Don't implement the rest of `StoreProtocol`.** `document_exists`,
  `get_document` and `list_documents` are reads that need SQL++ or a GSI index
  — Lessons 5 and 6. Bulk writes are Lesson 4. The stub file has only the
  method this lesson owns; leave it that way.
- **Don't touch `ingestion/pipeline.py` or `cli.py`.** Nothing constructs your
  `DocumentStore` yet, and wiring it in is Lesson 11.
- **Don't add an index.** The urge to make `SELECT * FROM documents` work is
  Lesson 5's, and giving in to it here means learning GSI while debugging
  transcoding.
