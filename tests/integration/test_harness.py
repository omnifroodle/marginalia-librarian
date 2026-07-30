"""Smoke test for the integration harness itself.

Skipped (with instructions) when no local cluster is up; passes once
`docker compose up -d` has a cluster listening. Lesson scaffolds add real
tests alongside this file.
"""

import socket


def test_cluster_reachable():
    # Same host/port the conftest skip-gate probes.
    with socket.create_connection(("localhost", 8091), timeout=2.0):
        pass
