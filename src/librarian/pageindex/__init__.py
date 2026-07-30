"""Vendored PageIndex fork (github.com/VectifyAI/PageIndex), trimmed to the
surface the librarian uses: PDF/markdown → hierarchical tree with summaries.

Local changes vs upstream: litellm gateway with retry/backoff and a
concurrency semaphore; Retry-After-aware 429 handling with a configurable
RetryPolicy and typed failures (LLMCallError / LLMRateLimitExhausted) instead
of silent empty-string returns; explicit configure_llm() instead of env vars;
dead client/retrieve API removed; progress printed via logging.
"""

from .page_index import page_index
from .page_index_md import md_to_tree
from .utils import (
    LLMCallError,
    LLMRateLimitExhausted,
    RetryPolicy,
    configure_llm,
    set_max_concurrent_llm_calls,
)

__all__ = [
    "page_index",
    "md_to_tree",
    "configure_llm",
    "set_max_concurrent_llm_calls",
    "RetryPolicy",
    "LLMCallError",
    "LLMRateLimitExhausted",
]
