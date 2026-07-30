"""OpenSearch client factory."""

from __future__ import annotations

from opensearchpy import OpenSearch

from ..config import Config


def create_client(config: Config) -> OpenSearch:
    """Create an OpenSearch client from config."""
    kwargs: dict = {
        "hosts": [{"host": config.opensearch_host, "port": config.opensearch_port}],
        "use_ssl": config.opensearch_tls,
        "verify_certs": config.opensearch_verify_certs,
        "ssl_show_warn": False,
        # Prevent indefinite blocking on slow/unresponsive OpenSearch operations.
        # connection_class default transport will use this for both connect and read.
        "timeout": 60,
    }
    if config.opensearch_auth:
        kwargs["http_auth"] = config.opensearch_auth

    return OpenSearch(**kwargs)
