"""Couchbase cluster factory.

The analogue of `opensearch/client.py::create_client`: the single place that
turns a `Config` into a live connection object, so nothing downstream has to
know how credentials, TLS, or timeouts are assembled.

╔══════════════════════════════════════════════════════════════════════════╗
║ LESSON 1 STUB — Matt implements `create_cluster`. Do not fill this in.   ║
║ Acceptance tests: tests/integration/test_l01_connect.py                      ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from typing import Any

from ..config import Config


def create_cluster(config: Config) -> Any:
    """Connect to Couchbase and return a `Cluster` the caller can use immediately.

    Config surface available to you (see `librarian/config.py`):

        config.couchbase_connection_string   couchbase:// or couchbases://
        config.couchbase_username
        config.couchbase_password
        config.couchbase_connect_timeout     seconds (float)
        config.couchbase_kv_timeout          seconds (float)
        config.couchbase_query_timeout       seconds (float)
        config.couchbase_search_timeout      seconds (float)
        config.couchbase_timeout_profile     e.g. "wan_development", or None
        config.couchbase_cert_path           Path | None (self-signed TLS only)

    Both deployments must work from day one, selected *only* by the connection
    string — local Docker EE (`couchbase://localhost`) and Capella
    (`couchbases://cb.xxxxx.cloud.couchbase.com`). No "am I on Capella" branch
    anywhere else in the codebase.

    What the acceptance tests require:
      - authenticates with the configured username/password
      - the returned cluster is usable right away: the caller opens the bucket
        and pings, with no sleep anywhere
      - bad credentials propagate to the caller as the SDK's
        `AuthenticationException`. Nothing here swallows or re-wraps it.

    Replace the `Any` return annotation with the real `Cluster` type when you
    add the import.
    """
    raise NotImplementedError(
        "Lesson 1: implement create_cluster — see tests/integration/test_l01_connect.py"
    )
