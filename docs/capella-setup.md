# Capella setup checklist (Lesson 0, Matt — all in the Capella UI)

Provision now; the first code that touches Capella is Lesson 13, and the
Capella AI lessons (14–17) need AI Services. Working through this UI flow
yourself is deliberate — it's the exact path customers walk.

## Cluster

- [ ] Create a free-tier/trial cluster (pick a region near you).
- [ ] Note the connection string: `couchbases://cb.<id>.cloud.couchbase.com`.
      The `couchbases://` scheme means TLS; Capella's certs chain to public
      CAs, so no cert pinning is needed — but find where the root cert is
      documented anyway (customers ask).
- [ ] Confirm which services the cluster runs (Data, Query, Index, Search —
      Search is required for FTS/vector lessons).
- [ ] Check whether your tier/region offers **AI Services** (Model Service,
      Vectorization, Agent Catalog). Phase 2 depends on this — if it's not
      available, flag it in CURRICULUM.md now rather than at Lesson 14.

## Access

- [ ] Allowed IPs: add your current IP (and note it changes — this is a
      classic customer support call).
- [ ] Create database-access credentials **scoped to the `librarian` bucket
      with read/write roles — not Full Admin**. Role scoping is DCE material:
      know what each role grants.
- [ ] Store credentials in `config.capella.yaml` (gitignored) or env vars.
      Never in git.

## Data containers

- [ ] Create bucket `librarian` (memory quota: modest; the demo corpus is
      small).
- [ ] Create the scope you'll use (or note that `_default` is the plan) —
      match what `config.capella.yaml` will say. Collections are created by
      your Lesson 2 code, not the UI.

## Sanity check (after Lesson 1 is done)

- [ ] `LIBRARIAN_TEST_ENV=capella pytest -m integration
      tests/integration/test_connect.py` connects, and ping shows kv/query/
      search healthy over `couchbases://`.
