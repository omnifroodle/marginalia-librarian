"""Shared fixtures for live-Couchbase integration tests.

Everything in this directory is automatically marked `integration` and is
excluded from a default `pytest` run (see pyproject.toml). Run with:

    pytest -m integration

Requires a local cluster: `docker compose up -d`, then one-time init via the
web UI at http://localhost:8091 (see docker-compose.yml header).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

_HERE = Path(__file__).parent

COUCHBASE_MGMT_HOST = "localhost"
COUCHBASE_MGMT_PORT = 8091


def _cluster_reachable(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(
            (COUCHBASE_MGMT_HOST, COUCHBASE_MGMT_PORT), timeout=timeout
        ):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    # This hook sees every collected item, not just this directory's — scope
    # the markers to tests that actually live under tests/integration/.
    local = [i for i in items if _HERE in Path(str(i.path)).parents]
    for item in local:
        item.add_marker(pytest.mark.integration)
    if local and not _cluster_reachable():
        skip = pytest.mark.skip(
            reason=(
                "No Couchbase cluster reachable on "
                f"{COUCHBASE_MGMT_HOST}:{COUCHBASE_MGMT_PORT} — "
                "run `docker compose up -d` and initialize it via the web UI"
            )
        )
        for item in local:
            item.add_marker(skip)
