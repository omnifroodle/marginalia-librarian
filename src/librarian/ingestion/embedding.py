"""Embedding generation via LiteLLM."""

from __future__ import annotations

import litellm

from ..config import Config


def generate_embedding(text: str, config: Config) -> list[float]:
    """Generate a single embedding vector for the given text."""
    # Truncate to avoid hitting context limits (rough 4-char-per-token estimate)
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars]

    response = litellm.embedding(
        model=config.embedding_model,
        input=[text],
        api_base=config.embedding_api_base,
        api_key=config.embedding_api_key,
        encoding_format="float",
        timeout=config.embedding_timeout,
        max_retries=config.num_retries,
    )
    return response.data[0]["embedding"]


def generate_embeddings_batch(texts: list[str], config: Config) -> list[list[float]]:
    """Generate embeddings for a list of texts in one API call."""
    max_chars = 8000
    truncated = [t[:max_chars] if len(t) > max_chars else t for t in texts]

    response = litellm.embedding(
        model=config.embedding_model,
        input=truncated,
        api_base=config.embedding_api_base,
        api_key=config.embedding_api_key,
        encoding_format="float",
        timeout=config.embedding_timeout,
        max_retries=config.num_retries,
    )
    return [item["embedding"] for item in response.data]
