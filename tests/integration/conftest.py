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
import yaml

from librarian.config import Config, _interpolate_env_vars

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parent.parent

# The cluster may be remote (e.g. the `office` docker host) — override with
# LIBRARIAN_TEST_COUCHBASE_HOST. Env *reads* are fine; writes are what
# test_hygiene.py bans.
COUCHBASE_MGMT_HOST = os.environ.get("LIBRARIAN_TEST_COUCHBASE_HOST", "localhost")
COUCHBASE_MGMT_PORT = int(os.environ.get("LIBRARIAN_TEST_COUCHBASE_PORT", "8091"))


def _env(name: str) -> str | None:
    """An env var's value, treating empty as unset."""
    return os.environ.get(name) or None


def _settings_from_config_file() -> dict:
    """The `couchbase:` block from a local config.yaml, if there is one.

    config.yaml is gitignored and is the documented place credentials live, so
    honouring it means a working dev setup needs nothing exported — and any
    session (including an agent's) can run the integration suite.

    Only the `couchbase:` block is interpolated. `load_config()` would resolve
    every `${VAR}` in the file, so a config.yaml copied from the example skips
    this whole suite over an unset NANOGPT_API_KEY — an LLM key has nothing to
    do with whether we can reach a cluster.
    """
    path = _REPO_ROOT / "config.yaml"
    if not path.exists():
        return {}
    try:
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        pytest.skip(f"config.yaml is present but unreadable: {exc}")
    try:
        block = _interpolate_env_vars(raw.get("couchbase") or {})
    except ValueError as exc:
        # An unset ${VAR} inside the couchbase block itself — that one really
        # does block us, so skip with the reason rather than erroring.
        pytest.skip(f"config.yaml couchbase block: {exc}")

    cfg = Config({"couchbase": block})
    cfg.apply_env_overrides()  # COUCHBASE_* still win over the file
    return {
        "connection_string": cfg.couchbase_connection_string,
        "username": cfg.couchbase_username,
        "password": cfg.couchbase_password,
        "bucket": cfg.couchbase_bucket,
        "scope": cfg.couchbase_scope,
    }


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

    Resolution order, most specific first:
      1. LIBRARIAN_TEST_COUCHBASE_* env vars — per-run overrides
      2. the repo's gitignored config.yaml
      3. built-in defaults (localhost, Administrator, bucket `librarian`)

    A password has to come from (1) or (2); without one the suite skips.

    Returned as a plain dict (not a Config) so tests can vary a single field —
    a deliberately wrong password, a different bucket — and rebuild.
    """
    settings = _settings_from_config_file()
    overrides = {
        "connection_string": _env("LIBRARIAN_TEST_COUCHBASE_CONNECTION_STRING"),
        "username": _env("LIBRARIAN_TEST_COUCHBASE_USERNAME"),
        "password": _env("LIBRARIAN_TEST_COUCHBASE_PASSWORD"),
        "bucket": _env("LIBRARIAN_TEST_COUCHBASE_BUCKET"),
    }
    settings.update({k: v for k, v in overrides.items() if v is not None})

    # Derived from the management host so one env var moves both the
    # reachability probe and the SDK connection. Lesson 13 (Capella) sets a
    # couchbases:// string here instead.
    settings.setdefault("connection_string", f"couchbase://{COUCHBASE_MGMT_HOST}")
    settings.setdefault("username", "Administrator")
    settings.setdefault("bucket", "librarian")
    settings.setdefault("scope", "_default")

    if not settings.get("password"):
        pytest.skip(
            "No Couchbase password available — either export "
            "LIBRARIAN_TEST_COUCHBASE_PASSWORD or put the admin password in "
            "config.yaml under couchbase.password (gitignored; see NOTES.md). "
            "./training-tools/env-check.sh checks the whole environment."
        )
    return settings


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
