"""Smoke test for the integration harness itself.

Skipped (with instructions) when no cluster is reachable; passes once one is
listening — local `docker compose up -d`, or a remote host selected via
LIBRARIAN_TEST_COUCHBASE_HOST. Lesson scaffolds add real tests alongside
this file.
"""

import socket


def test_cluster_reachable(cluster_address):
    host, port = cluster_address
    with socket.create_connection((host, port), timeout=2.0):
        pass
