"""Vendored PageIndex fork (github.com/VectifyAI/PageIndex), trimmed to the
surface the librarian uses: PDF/markdown → hierarchical tree with summaries.

Local changes vs upstream: litellm gateway with retry/backoff and a
concurrency semaphore; Retry-After-aware 429 handling with a configurable
RetryPolicy and typed failures (LLMCallError / LLMRateLimitExhausted) instead
of silent empty-string returns; explicit configure_llm() instead of env vars;
dead client/retrieve API removed; progress printed via logging; PyPDF2 replaced
by its maintained rename, pypdf.

⚠️ The pypdf swap (2026-07-30) is mechanically identical — `PdfReader`,
`.metadata`, `.pages`, `.extract_text()` are the same API — and
`tests/test_pdf_extraction.py` now smoke-tests that surface. But that test
reads a *synthesized* PDF; the corpus is full of scanned, encrypted, malformed
and CJK files that no test here represents. **The first full ingest after this
change is the real verification** — treat surprises in page text or metadata
extraction as suspects here before looking elsewhere. `get_page_tokens` still
accepts the old `pdf_parser="PyPDF2"` selector string.
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
