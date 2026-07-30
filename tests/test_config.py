"""Tests for config loading and env var interpolation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from librarian.config import load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def test_load_minimal_config(tmp_path):
    p = _write_config(tmp_path, """
        models:
          tree_generation: "openai/haiku"
          reasoning: "openai/sonnet"
          embedding: "openai/embed"
        litellm:
          api_base: "http://localhost:8000"
          api_key: "test-key"
    """)
    cfg = load_config(p)
    assert cfg.tree_generation_model == "openai/haiku"
    assert cfg.reasoning_model == "openai/sonnet"
    assert cfg.litellm_api_base == "http://localhost:8000"


def test_env_var_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret-123")
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
          embedding: "m"
        litellm:
          api_key: "${TEST_API_KEY}"
    """)
    cfg = load_config(p)
    assert cfg.litellm_api_key == "secret-123"


def test_missing_env_var_raises(tmp_path):
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
          embedding: "m"
        litellm:
          api_key: "${DEFINITELY_NOT_SET_XYZ}"
    """)
    with pytest.raises(ValueError, match="DEFINITELY_NOT_SET_XYZ"):
        load_config(p)


def test_missing_config_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_defaults(tmp_path):
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
          embedding: "m"
    """)
    cfg = load_config(p)
    assert cfg.opensearch_host == "localhost"
    assert cfg.opensearch_port == 9200
    assert cfg.opensearch_tls is False
    assert cfg.default_top_k == 20
    assert cfg.shortlist_size == 5
    assert cfg.embedding_dimension == 1024
    assert cfg.max_liner_notes_per_citation == 4
    assert cfg.log_dir is None


def test_notes_model_falls_back_to_reasoning(tmp_path):
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "openai/sonnet"
    """)
    cfg = load_config(p)
    assert cfg.notes_model == "openai/sonnet"


def test_index_prefix_default_and_override(tmp_path):
    from librarian.opensearch.mappings import IndexNames

    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
    """)
    cfg = load_config(p)
    assert cfg.index_prefix == "librarian"
    assert IndexNames.from_config(cfg).documents == "librarian_documents"

    p2 = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
        opensearch:
          index_prefix: "marginalia"
    """)
    ix = IndexNames.from_config(load_config(p2))
    assert ix.all_indexes == [
        "marginalia_documents", "marginalia_tree_nodes", "marginalia_page_content"
    ]
    assert ix.pipeline == "marginalia-hybrid-search"


# ── couchbase config plumbing (Lesson 1) ──────────────────────────────────────
# These stay in this file rather than a test_l01_*.py: they cover the shared
# Config class, and splitting one class's tests across files by lesson would
# scatter them permanently. Integration tests, which are genuinely per-lesson
# and short-lived, get lesson-numbered files instead.


@pytest.mark.lesson(1)
def test_couchbase_defaults(tmp_path):
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
    """)
    cfg = load_config(p)
    assert cfg.couchbase_connection_string == "couchbase://localhost"
    assert cfg.couchbase_username == "Administrator"
    assert cfg.couchbase_password == ""
    assert cfg.couchbase_bucket == "librarian"
    assert cfg.couchbase_scope == "_default"
    # SDK defaults, so an absent timeouts block changes nothing.
    assert cfg.couchbase_connect_timeout == 10.0
    assert cfg.couchbase_kv_timeout == 2.5
    assert cfg.couchbase_query_timeout == 75.0
    assert cfg.couchbase_search_timeout == 75.0
    assert cfg.couchbase_timeout_profile is None
    assert cfg.couchbase_cert_path is None


@pytest.mark.lesson(1)
def test_couchbase_password_interpolated_from_env(tmp_path, monkeypatch):
    # Credentials arrive by reference, never as a literal in a committed file.
    monkeypatch.setenv("TEST_CB_PASSWORD", "hunter2")
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
        couchbase:
          connection_string: "couchbase://cbnode"
          username: "librarian_app"
          password: "${TEST_CB_PASSWORD}"
          bucket: "demo"
          scope: "main"
    """)
    cfg = load_config(p)
    assert cfg.couchbase_password == "hunter2"
    assert cfg.couchbase_connection_string == "couchbase://cbnode"
    assert cfg.couchbase_username == "librarian_app"
    assert cfg.couchbase_bucket == "demo"
    assert cfg.couchbase_scope == "main"


@pytest.mark.lesson(1)
def test_couchbase_capella_style_config(tmp_path):
    # The Capella shape (Lesson 13): TLS scheme + WAN timeout profile. Only the
    # connection string distinguishes the deployment.
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
        couchbase:
          connection_string: "couchbases://cb.abc123.cloud.couchbase.com"
          timeout_profile: "wan_development"
          cert_path: "/etc/ssl/couchbase-ca.pem"
          timeouts:
            connect: 20
            kv: 5
            query: 120
            search: 120
    """)
    cfg = load_config(p)
    assert cfg.couchbase_connection_string.startswith("couchbases://")
    assert cfg.couchbase_timeout_profile == "wan_development"
    assert cfg.couchbase_cert_path == Path("/etc/ssl/couchbase-ca.pem")
    assert cfg.couchbase_connect_timeout == 20.0
    assert cfg.couchbase_kv_timeout == 5.0
    assert cfg.couchbase_query_timeout == 120.0
    assert cfg.couchbase_search_timeout == 120.0


@pytest.mark.lesson(1)
def test_couchbase_env_overrides(tmp_path, monkeypatch):
    # Lets the integration suite and containers point at a cluster without
    # editing (or even having) a config.yaml.
    monkeypatch.setenv("COUCHBASE_CONNECTION_STRING", "couchbase://otherhost")
    monkeypatch.setenv("COUCHBASE_USERNAME", "envuser")
    monkeypatch.setenv("COUCHBASE_PASSWORD", "envpass")
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
        couchbase:
          connection_string: "couchbase://from-file"
          username: "fileuser"
          password: "filepass"
    """)
    cfg = load_config(p)
    assert cfg.couchbase_connection_string == "couchbase://otherhost"
    assert cfg.couchbase_username == "envuser"
    assert cfg.couchbase_password == "envpass"


def test_vault_root(tmp_path):
    p = _write_config(tmp_path, """
        models:
          tree_generation: "m"
          reasoning: "m"
        library:
          vault_root: "/Volumes/vault/demo"
    """)
    cfg = load_config(p)
    assert cfg.vault_root == Path("/Volumes/vault/demo")
