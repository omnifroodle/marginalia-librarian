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
