"""Map funnel exceptions to the typed SSE error contract (docs/api.md).

The /search worker catches everything; this module decides what the client
is told. `code` is the machine-readable field the reader switches on,
`message` is presentable copy. The raw exception string travels separately
as `detail` — never show it to end users.
"""

from __future__ import annotations

from typing import Literal, Tuple

ErrorCode = Literal[
    "llm_payment",
    "llm_auth",
    "llm_rate_limit",
    "llm_unavailable",
    "search_backend_unavailable",
    "internal",
]

_MESSAGES: dict[str, str] = {
    "llm_payment": "The language-model account is out of credit.",
    "llm_auth": "The language-model API key was rejected or is not configured.",
    "llm_rate_limit": "The language-model provider is rate limiting requests.",
    "llm_unavailable": "The language-model provider is unavailable right now.",
    "search_backend_unavailable": "The search index is unreachable.",
    "internal": "Something went wrong inside the librarian.",
}


def classify_error(exc: Exception) -> Tuple[ErrorCode, str]:
    """Return (code, human-friendly message) for a funnel failure. Never raises."""
    try:
        code = _classify(exc)
    except Exception:  # classification must never mask the original failure
        code = "internal"
    return code, _MESSAGES[code]


def _classify(exc: Exception) -> ErrorCode:
    import litellm
    from opensearchpy import exceptions as os_exc

    from ..pageindex import LLMCallError, LLMRateLimitExhausted

    if isinstance(exc, (LLMRateLimitExhausted, litellm.RateLimitError)):
        return "llm_rate_limit"
    # litellm has no dedicated 402 class; NanoGPT's out-of-credit error arrives
    # as an APIError-family exception carrying status_code (or wrapped in an
    # LLMCallError message), so match on the code, not the type.
    status = getattr(exc, "status_code", None)
    if status == 402 or "402" in str(exc) or "Payment Required" in str(exc):
        return "llm_payment"
    # A keyless deployment doesn't get an AuthenticationError: litellm raises
    # InternalServerError("… Missing credentials …") before sending anything,
    # so match the message too (seen live from the compose stack).
    if isinstance(exc, (litellm.AuthenticationError, litellm.PermissionDeniedError)) or (
        "Missing credentials" in str(exc)
    ):
        return "llm_auth"
    if isinstance(
        exc,
        (
            LLMCallError,
            litellm.ServiceUnavailableError,
            litellm.APIConnectionError,
            litellm.InternalServerError,
            litellm.Timeout,
        ),
    ):
        return "llm_unavailable"
    if isinstance(exc, (os_exc.ConnectionError, os_exc.ConnectionTimeout)):
        return "search_backend_unavailable"
    return "internal"
