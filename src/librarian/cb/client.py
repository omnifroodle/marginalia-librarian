"""Couchbase cluster factory.

The analogue of `opensearch/client.py::create_client`: the single place that
turns a `Config` into a live connection object, so nothing downstream has to
know how credentials, TLS, or timeouts are assembled.

Acceptance tests: tests/integration/test_l01_connect.py
"""

from __future__ import annotations

from ..config import Config
from datetime import timedelta

from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, ClusterTimeoutOptions, WaitUntilReadyOptions
from couchbase.auth import PasswordAuthenticator
from couchbase.diagnostics import ServiceType

def create_cluster(config: Config) -> Cluster:
    """Connect to Couchbase and return a `Cluster` the caller can use immediately.

    Config surface (see `librarian/config.py`); timeouts are seconds as floats
    and are converted to `timedelta` here, at the boundary:

        config.couchbase_connection_string   couchbase:// or couchbases://
        config.couchbase_username
        config.couchbase_password
        config.couchbase_bootstrap_timeout   seconds (float)
        config.couchbase_connect_timeout     seconds (float)
        config.couchbase_kv_timeout          seconds (float)
        config.couchbase_query_timeout       seconds (float)
        config.couchbase_search_timeout      seconds (float)
        config.couchbase_timeout_profile     e.g. "wan_development", or None
        config.couchbase_cert_path           Path | None (self-signed TLS only)
        config.couchbase_bucket              librarian
        config.couchbase_scope               _default

    Both deployments work from the same code path, selected *only* by the
    connection string — local Docker EE (`couchbase://localhost`) and Capella
    (`couchbases://cb.xxxxx.cloud.couchbase.com`). No "am I on Capella" branch
    anywhere in the codebase.

    Contract:
      - authenticates with the configured username/password
      - the returned cluster is usable right away: the caller opens the bucket
        and pings, with no sleep anywhere
      - bad credentials propagate to the caller as the SDK's
        `AuthenticationException`. Nothing here swallows or re-wraps it.
    """

    endpoint = config.couchbase_connection_string
    username = config.couchbase_username
    password = config.couchbase_password

    # Create the authenticator with the provided username and password
    authenticator = PasswordAuthenticator(username, password)

    # Apply a timeout profile if specified in the config. 
    # If no profile is specified, apply individual timeouts.
    # NOTE: trying to apply both would result in an override.
    if config.couchbase_timeout_profile:
      options = ClusterOptions(authenticator)
      options.apply_profile(config.couchbase_timeout_profile)

    else: # If no timeout profile is specified, apply individual timeouts
      timeout_options = ClusterTimeoutOptions(
        connect_timeout=timedelta(seconds=config.couchbase_connect_timeout),
        kv_timeout=timedelta(seconds=config.couchbase_kv_timeout),
        query_timeout=timedelta(seconds=config.couchbase_query_timeout),
        search_timeout=timedelta(seconds=config.couchbase_search_timeout),
        bootstrap_timeout=timedelta(seconds=config.couchbase_bootstrap_timeout)
      )
      options = ClusterOptions(authenticator, timeout_options=timeout_options)

    # Create the cluster object with the connection string and authenticator
    cluster = Cluster(endpoint, options)
    cluster.wait_until_ready(
      timedelta(seconds=config.couchbase_bootstrap_timeout),
      WaitUntilReadyOptions(
         service_types=[ServiceType.KeyValue, ServiceType.Query, ServiceType.Search])
    )  # Wait until the cluster is ready

    return cluster

