"""Configuration loading with YAML + environment variable interpolation.

Loaded once at process edge (CLI or API startup) and passed down explicitly.
Nothing here mutates os.environ.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


def _interpolate_env_vars(value: Any) -> Any:
    """Recursively replace ${VAR} with environment variable values."""
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            var_name = match.group(1)
            result = os.environ.get(var_name)
            if result is None:
                raise ValueError(
                    f"Environment variable '{var_name}' referenced in config but not set"
                )
            return result
        return re.sub(r"\$\{([^}]+)\}", replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_vars(v) for v in value]
    return value


class Config:
    """Loaded and validated configuration."""

    def __init__(self, data: dict) -> None:
        self._data = data

    # ── models ────────────────────────────────────────────────────────────────

    @property
    def tree_generation_model(self) -> str:
        return self._data["models"]["tree_generation"]

    @property
    def reasoning_model(self) -> str:
        return self._data["models"]["reasoning"]

    @property
    def notes_model(self) -> str:
        """Model for per-citation blurbs and liner notes; defaults to reasoning."""
        return self._data["models"].get("notes") or self.reasoning_model

    @property
    def embedding_model(self) -> str | None:
        val = self._data["models"].get("embedding", "")
        return val if val else None

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.embedding_model)

    @property
    def embedding_dimension(self) -> int:
        return int(self._data.get("litellm", {}).get("embedding_dimension", 1024))

    # ── litellm ───────────────────────────────────────────────────────────────

    @property
    def litellm_api_base(self) -> str | None:
        return self._data.get("litellm", {}).get("api_base")

    @property
    def litellm_api_key(self) -> str | None:
        return self._data.get("litellm", {}).get("api_key")

    @property
    def embedding_api_base(self) -> str | None:
        """Separate API base for embeddings — falls back to litellm_api_base if not set."""
        return self._data.get("litellm", {}).get("embedding_api_base") or self.litellm_api_base

    @property
    def embedding_api_key(self) -> str | None:
        """Separate API key for embeddings — falls back to litellm_api_key if not set."""
        return self._data.get("litellm", {}).get("embedding_api_key") or self.litellm_api_key

    @property
    def reasoning_api_base(self) -> str | None:
        """Separate API base for reasoning.

        Falls back to litellm_api_base if the key is absent from config.
        Set to null (~) in config to explicitly use the provider's native endpoint
        (e.g. Anthropic) without falling back to the global api_base.
        """
        litellm_cfg = self._data.get("litellm", {})
        if "reasoning_api_base" in litellm_cfg:
            return litellm_cfg["reasoning_api_base"] or None
        return self.litellm_api_base

    @property
    def reasoning_api_key(self) -> str | None:
        """Separate API key for reasoning.

        Falls back to litellm_api_key if the key is absent from config.
        Set to null (~) in config to let LiteLLM pick up the provider's
        own environment variable (e.g. ANTHROPIC_API_KEY) instead.
        """
        litellm_cfg = self._data.get("litellm", {})
        if "reasoning_api_key" in litellm_cfg:
            return litellm_cfg["reasoning_api_key"] or None
        return self.litellm_api_key

    # ── opensearch ────────────────────────────────────────────────────────────

    @property
    def opensearch_host(self) -> str:
        return self._data.get("opensearch", {}).get("host", "localhost")

    @property
    def opensearch_port(self) -> int:
        return int(self._data.get("opensearch", {}).get("port", 9200))

    @property
    def opensearch_tls(self) -> bool:
        return bool(self._data.get("opensearch", {}).get("tls", False))

    @property
    def opensearch_verify_certs(self) -> bool:
        return bool(self._data.get("opensearch", {}).get("verify_certs", False))

    @property
    def index_prefix(self) -> str:
        """Namespace for this deployment's indexes/pipeline on the cluster."""
        return self._data.get("opensearch", {}).get("index_prefix", "librarian")

    @property
    def opensearch_auth(self) -> tuple[str, str] | None:
        auth = self._data.get("opensearch", {}).get("auth")
        if auth and auth.get("username") and auth.get("password"):
            return (auth["username"], auth["password"])
        return None

    # ── library (source files) ────────────────────────────────────────────────

    @property
    def vault_root(self) -> Path:
        """Root directory the corpus lives under. source_path fields are stored
        relative to this so the index survives the vault moving between machines."""
        return Path(self._data.get("library", {}).get("vault_root", "."))

    # ── ingestion ─────────────────────────────────────────────────────────────

    @property
    def reject_log_path(self) -> Path:
        p = self._data.get("ingestion", {}).get("reject_log_path", "reject_log.json")
        return Path(p)

    # ── search ────────────────────────────────────────────────────────────────

    @property
    def default_top_k(self) -> int:
        return int(self._data.get("search", {}).get("default_top_k", 20))

    @property
    def shortlist_size(self) -> int:
        return int(self._data.get("search", {}).get("shortlist_size", 5))

    @property
    def reasoning_summary_max_chars(self) -> int:
        """Max characters per node summary sent to the Phase 3 reasoning LLM.

        Prevents context-window overflow on models with small windows (e.g. 32K).
        At ~4 chars/token, 400 chars ≈ 100 tokens per node.
        """
        return int(self._data.get("search", {}).get("reasoning_summary_max_chars", 400))

    @property
    def reasoning_mode(self) -> str:
        """How Phase 3 selects nodes: 'agentic' (tool-calling loop) or 'one_shot'."""
        return self._data.get("search", {}).get("reasoning_mode", "agentic")

    @property
    def teleport_max(self) -> int:
        """Max teleport tool calls (search_corpus / search_pages) allowed per query."""
        return int(self._data.get("search", {}).get("teleport_max", 3))

    # ── notes ─────────────────────────────────────────────────────────────────

    @property
    def max_liner_notes_per_citation(self) -> int:
        return int(self._data.get("notes", {}).get("max_liner_notes_per_citation", 4))

    # ── resilience ───────────────────────────────────────────────────────────

    @property
    def completion_timeout(self) -> int:
        return int(self._data.get("resilience", {}).get("completion_timeout", 300))

    @property
    def embedding_timeout(self) -> int:
        return int(self._data.get("resilience", {}).get("embedding_timeout", 120))

    @property
    def num_retries(self) -> int:
        return int(self._data.get("resilience", {}).get("num_retries", 3))

    @property
    def rate_limit_retries(self) -> int:
        return int(self._data.get("resilience", {}).get("rate_limit_retries", 10))

    @property
    def rate_limit_max_wait(self) -> int:
        return int(self._data.get("resilience", {}).get("rate_limit_max_wait", 120))

    @property
    def max_concurrent_llm_calls(self) -> int:
        return int(self._data.get("resilience", {}).get("max_concurrent_llm_calls", 10))

    # ── api ───────────────────────────────────────────────────────────────────

    @property
    def api_host(self) -> str:
        return self._data.get("api", {}).get("host", "127.0.0.1")

    @property
    def api_port(self) -> int:
        return int(self._data.get("api", {}).get("port", 8000))

    @property
    def api_cors_origins(self) -> list[str]:
        """Browser origins allowed to call the API (the reader fetches /assets
        cross-origin for pdf.js). Default allows any origin — the API serves
        nothing sensitive and is expected to run on localhost."""
        return list(self._data.get("api", {}).get("cors_origins", ["*"]))

    # ── logging ───────────────────────────────────────────────────────────────

    @property
    def log_dir(self) -> Path | None:
        """Directory for diagnostic logs; None disables file logging entirely."""
        p = self._data.get("logging", {}).get("dir")
        return Path(p) if p else None

    def apply_env_overrides(self) -> None:
        """Apply well-known environment variable overrides (called at load time)."""
        if api_base := os.environ.get("LIBRARIAN_API_BASE"):
            self._data.setdefault("litellm", {})["api_base"] = api_base
        if api_key := os.environ.get("LIBRARIAN_API_KEY"):
            self._data.setdefault("litellm", {})["api_key"] = api_key
        if os_host := os.environ.get("OPENSEARCH_HOST"):
            self._data.setdefault("opensearch", {})["host"] = os_host
        if vault := os.environ.get("LIBRARIAN_VAULT_ROOT"):
            self._data.setdefault("library", {})["vault_root"] = vault


def load_config(path: str | Path | None = None) -> Config:
    """Load config from YAML file, interpolate env vars, return Config object."""
    if path is None:
        path = Path("config.yaml")
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Copy config.example.yaml to config.yaml and fill in your values."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raw = {}
    interpolated = _interpolate_env_vars(raw)
    config = Config(interpolated)
    config.apply_env_overrides()
    return config
