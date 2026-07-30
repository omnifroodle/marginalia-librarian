"""Lesson 1 acceptance: cluster connection + config plumbing.

Matt implements `librarian/cb/client.py::create_cluster`; this file decides
whether it works.

    pytest -m integration tests/integration/test_connect.py -q

Note what is *not* in this file: any `import couchbase`. Service and exception
identities are asserted by name (enum `.value`, class `__mro__`) so the
scaffold stays on the agent's side of the hard rule in CURRICULUM.md — every
SDK import in this repo is Matt's. It also means these assertions don't break
when Matt picks a different SDK version.
"""

from __future__ import annotations

import pytest

from librarian.cb.client import create_cluster
from librarian.config import Config

pytestmark = pytest.mark.lesson(1)

# The SDK reports services as a ServiceType enum whose values are the wire
# names: Data = "kv", Query = "n1ql", Search = "fts".
CORE_SERVICES = {"kv", "n1ql", "fts"}


def _enum_value(v) -> str:
    """Enum member -> its value, tolerating plain strings."""
    return str(getattr(v, "value", v)).lower()


def _exception_names(exc: BaseException) -> set[str]:
    """Every class name in the exception's MRO — an import-free isinstance."""
    return {cls.__name__ for cls in type(exc).__mro__}


def test_create_cluster_opens_the_configured_bucket(cb_config):
    """The returned cluster is usable immediately — no sleep, no retry loop."""
    cluster = create_cluster(cb_config)
    bucket = cluster.bucket(cb_config.couchbase_bucket)
    assert bucket.name == cb_config.couchbase_bucket


def test_ping_reports_core_services_healthy(cb_config):
    cluster = create_cluster(cb_config)
    # Opening the bucket is what establishes the KV connections that a
    # cluster-level ping can report; without it you tend to see only the
    # HTTP services. (Worth confirming for yourself — try it both ways.)
    cluster.bucket(cb_config.couchbase_bucket)

    result = cluster.ping()
    reported = {_enum_value(svc): endpoints for svc, endpoints in result.endpoints.items()}

    missing = CORE_SERVICES - reported.keys()
    assert not missing, (
        f"services missing from ping: {sorted(missing)} — got {sorted(reported)}. "
        "If a service is absent entirely, the cluster node was initialized "
        "without it (Lesson 0 wants Data/Query/Index/Search)."
    )

    for name in sorted(CORE_SERVICES):
        endpoints = reported[name]
        assert endpoints, f"{name}: no endpoints reported"
        for endpoint in endpoints:
            assert _enum_value(endpoint.state) == "ok", (
                f"{name} endpoint not ok: {endpoint.state} ({endpoint.remote})"
            )


def test_bad_credentials_are_not_swallowed(couchbase_settings):
    """A wrong password must reach the caller as AuthenticationException.

    This is the whole point of the "no bare except" rubric line: the failure
    the operator needs to see is "your password is wrong", not an empty result
    or a generic connection error.
    """
    bad = Config(
        {"couchbase": {**couchbase_settings, "password": "definitely-not-the-password"}}
    )

    with pytest.raises(Exception) as exc_info:  # noqa: B017 — narrowed by the assert below
        cluster = create_cluster(bad)
        cluster.bucket(bad.couchbase_bucket).ping()

    names = _exception_names(exc_info.value)
    assert "AuthenticationException" in names, (
        f"expected the SDK's AuthenticationException, got "
        f"{type(exc_info.value).__name__}: {exc_info.value}. "
        "If the SDK surfaces a timeout instead, that is a real finding about "
        "bootstrap failure modes — raise it rather than loosening this test."
    )
