# Lesson 2 — Bucket / scope / collection management

*Delivered 2026-07-30. Scaffold commit `f760b90`. Archived as delivered; see
[README.md](README.md) for the section-by-section rationale.*

---

## The mental model

OpenSearch gave you one flat namespace of indexes, which is why `IndexNames`
carries a prefix — `librarian_documents` exists to not collide with
`marginalia_documents`. Couchbase gives you the namespace structurally:

```
bucket (librarian)          ← a physical thing: RAM quota, replicas, disk
└── scope (_default)        ← a logical grouping; the multi-tenancy boundary
    ├── documents           ← what an OpenSearch index was
    ├── tree_nodes
    └── page_content
```

So the names are plain constants now —
[cb/names.py](../../src/librarian/cb/names.py) — and the configurable knobs
moved up to `couchbase.bucket` / `couchbase.scope`.

The line that matters most: **the bucket is not yours to create.** A bucket is
a resource-allocation decision (RAM quota, replica count, eviction policy) that
belongs to whoever runs the cluster. On Capella it's literally a different
plane — the control plane, via UI/API/Terraform — and your app credentials
typically can't create one at all. An app that creates its own bucket on
startup works beautifully in dev and is a governance problem in production.
`ensure_collections()` creates scopes and collections; it fails loudly on a
missing bucket.

## What you write

[src/librarian/cb/manager.py](../../src/librarian/cb/manager.py) — three
methods, full contracts in the docstrings:

| | |
|---|---|
| `ensure_collections()` | create scope + all three collections, idempotently → `[(name, "created"\|"skipped")]` |
| `reset()` | drop and recreate → `[(name, "dropped")] + ensure_collections()` |
| `status()` | `{name: {"exists": bool, "doc_count": int \| None}}` |

`bucket.collections()` returns the SDK's own `CollectionManager` — that's your
handle.

## The real decision: idempotency

This is the one to think about rather than pattern-match. Two ways to make
`ensure_collections()` safe to re-run:

**Check-then-create** — `get_all_scopes()`, diff against `ALL_COLLECTIONS`,
create what's missing. Reads naturally and gives you the `created`/`skipped`
distinction for free. It's also a TOCTOU race: two deploys hitting one cluster
can both observe "missing" and both create.

**Exception-driven** — just call `create_collection` and catch
`CollectionAlreadyExistsException` (verified name). Race-free, because the
cluster arbitrates. But you're using exceptions for expected control flow, and
you need a separate read to report `skipped` honestly.

Either is defensible; the rubric asks you to have *reasoned* it, not to pick
the one I'd pick. Worth noting your answer may differ between
`ensure_collections` (concurrent deploys plausible) and `reset` (an
operator-invoked destructive command).

## Traps I've already measured

Recorded in [NOTES.md](../../NOTES.md) so you don't burn a round on them:

- **`get_all_scopes()` returns `_system` too** (with `_query`, `_mobile`).
  Filter to what you own — `status()` asserts exactly three keys.
- **`_default` scope cannot be dropped.** `HTTP 400 "Deleting _default scope is
  not allowed"`, surfaced as `UnsupportedOperation` — a name with no "scope" in
  it, so it won't match the handler you'd guess. This is why "reset = drop the
  scope, recreate it" is a one-liner that passes every test but
  `test_reset_works_in_the_default_scope`.
- **`cluster.bucket("nope")` raises immediately** — `BucketNotFoundException`,
  ~10ms. Not lazy.
- **`create_collection(scope_name, collection_name, settings=None)`.** The
  `CollectionSpec` overload is deprecated since 4.1.9.

## `status()` and the count you can't have

`doc_count` will be **approximate and you cannot fix that in this lesson.**
With no index on the collection, `COUNT(*)` is served by a `CountScan` operator
reading collection metadata that trails the KV writes. 300 documents written
and immediately counted:

| | |
|---|---|
| `COUNT(*)` default | 242 / 300 |
| `COUNT(*)` `request_plus` | 214 / 300 |
| `COUNT(*) WHERE META().id IS NOT MISSING` `request_plus` | 287 / 300 |
| `COUNT(*)` **with a primary index** | **300 / 300** |

`request_plus` is the natural guess and it doesn't work — sequential scans
don't participate in its mutation-token protocol. Lesson 5's GSI index is what
makes it exact. The test polls for convergence instead of asserting instantly,
and is written to survive Lesson 5 without edits.

The rubric line here is about honesty, not accuracy: say in your docstring that
the number lags. An operator running `librarian status` after an ingest needs
to know whether a low count means data loss or lag.

## Acceptance

```bash
pytest -m integration --lesson 2 -q     # 13 tests: lesson 1's 5 + these 8
```

[tests/integration/test_l02_manager.py](../../tests/integration/test_l02_manager.py)
— currently 8 red, and I've verified all 8 pass against a throwaway
implementation, so anything that stays red is your code, not my scaffold. Each
test gets a disposable scope and cleans up after itself.

## Rubric

- Idempotency approach reasoned — you can say why yours, and what it costs
- No blanket `except` (enforced on `cb/`); catch the specific exception you can
  act on
- `reset()` works in `_default` scope
- The bucket is never created; a missing one surfaces `BucketNotFoundException`
- `status()` documents that its count lags, and distinguishes missing (`None`)
  from empty (`0`)
- Handles held on `self`, not re-derived per call — Lesson 3 builds on that

## Guiding questions

1. `ensure_collections()` returns `created` vs `skipped`. Under the
   exception-driven approach, where does that information come from — and is it
   still true by the time you return it?
2. If two `librarian init-indexes` run concurrently against one cluster, what's
   the worst outcome your implementation permits?
3. `reset()` is destructive and `librarian init-indexes --reset` is one flag
   away from `init-indexes`. Is there anything the *manager* should do about
   that, or is it purely the CLI's problem?

Leave [cli.py](../../src/librarian/cli.py) alone — rewiring the commands is
Lesson 11. `status()` just needs to return a shape the existing print loop
could consume.
