# training-tools

Mechanical, repeatable environment work — kept out of the lessons so a session
spends its tokens on Couchbase, not on rediscovering this machine.

| Script | Mutates? | What it does |
|---|---|---|
| `dev-setup.sh` | yes | Idempotent venv bootstrap: creates `.venv` if missing, installs `-e ".[api,dev]"`, smoke-imports `librarian`. |
| `env-check.sh` | no | One-shot readiness sweep: venv, SDK, container, cluster init state, credentials, services, buckets. |

Run `env-check.sh` at the start of a session; run `dev-setup.sh` when it tells
you to. Everything else (docker commands, gotchas, credential env vars) is in
[NOTES.md](../NOTES.md).

Both scripts are repo-relative — run them from anywhere.

## Adding to this folder

Add a script when something is (a) run more than once, (b) fiddly enough that
getting it wrong costs a debugging round, and (c) not itself a lesson. Keep
Couchbase *SDK* work out of here — that's Matt's code, and it lives in
`src/librarian/cb/` (see the hard rule in `CURRICULUM.md`). Shell/REST/docker
plumbing is fair game.
