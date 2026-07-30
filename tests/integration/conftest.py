"""Shared fixtures for live-Couchbase integration tests.

Everything in this directory is automatically marked `integration` and is
excluded from a default `pytest` run (see pyproject.toml). Run with:

    pytest -m integration

Requires a local cluster: `docker compose up -d`, then one-time init via the
web UI at http://localhost:8091 (see docker-compose.yml header).
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from librarian.config import Config

_HERE = Path(__file__).parent

# The cluster may be remote (e.g. the `office` docker host) — override with
# LIBRARIAN_TEST_COUCHBASE_HOST. Env *reads* are fine; writes are what
# test_hygiene.py bans.
COUCHBASE_MGMT_HOST = os.environ.get("LIBRARIAN_TEST_COUCHBASE_HOST", "localhost")
COUCHBASE_MGMT_PORT = int(os.environ.get("LIBRARIAN_TEST_COUCHBASE_PORT", "8091"))

# Credentials are whatever Matt chose during the manual cluster init (Lesson 0)
# and are never committed. See NOTES.md § "Credentials for integration tests".
COUCHBASE_USERNAME = os.environ.get("LIBRARIAN_TEST_COUCHBASE_USERNAME", "Administrator")
COUCHBASE_PASSWORD = os.environ.get("LIBRARIAN_TEST_COUCHBASE_PASSWORD", "")
COUCHBASE_BUCKET = os.environ.get("LIBRARIAN_TEST_COUCHBASE_BUCKET", "librarian")
# Derived from the management host by default so one env var moves both the
# reachability probe and the SDK connection. Lesson 13 (Capella) overrides this
# directly with a couchbases:// string.
COUCHBASE_CONNECTION_STRING = os.environ.get(
    "LIBRARIAN_TEST_COUCHBASE_CONNECTION_STRING", f"couchbase://{COUCHBASE_MGMT_HOST}"
)


def _cluster_reachable(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(
            (COUCHBASE_MGMT_HOST, COUCHBASE_MGMT_PORT), timeout=timeout
        ):
            return True
    except OSError:
        return False


@pytest.fixture
def cluster_address() -> tuple[str, int]:
    """(host, port) of the cluster management API the harness is pointed at."""
    return COUCHBASE_MGMT_HOST, COUCHBASE_MGMT_PORT


@pytest.fixture
def couchbase_settings() -> dict:
    """The raw `couchbase:` config block for the harness cluster.

    Returned as a plain dict (not a Config) so tests can vary one field —
    a deliberately wrong password, a different bucket — and rebuild.
    """
    if not COUCHBASE_PASSWORD:
        pytest.skip(
            "LIBRARIAN_TEST_COUCHBASE_PASSWORD is not set — export the admin "
            "password chosen during cluster init (see NOTES.md). Run "
            "./training-tools/env-check.sh to check the whole environment."
        )
    return {
        "connection_string": COUCHBASE_CONNECTION_STRING,
        "username": COUCHBASE_USERNAME,
        "password": COUCHBASE_PASSWORD,
        "bucket": COUCHBASE_BUCKET,
        "scope": "_default",
    }


@pytest.fixture
def cb_config(couchbase_settings) -> Config:
    """A Config pointed at the harness cluster — no config.yaml required."""
    return Config({"couchbase": couchbase_settings})


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
                "run `docker compose up -d` (or set LIBRARIAN_TEST_COUCHBASE_HOST "
                "for a remote cluster) and initialize it via the web UI"
            )
        )
        for item in local:
            item.add_marker(skip)
