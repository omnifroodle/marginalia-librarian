"""Process-wide LLM call counter for presentation-layer heartbeats.

LiteLLM invokes the registered callbacks for every completion/embedding call,
sync or async, including the ones made deep inside the vendored PageIndex
fork. Adapters (CLI, API) that want liveness feedback call install() once and
poll calls(); library code never reads this.
"""

from __future__ import annotations

import threading

import litellm

_lock = threading.Lock()
_count = 0


def _on_success(kwargs, completion_response, start_time, end_time) -> None:
    global _count
    with _lock:
        _count += 1


def _on_failure(kwargs, completion_response, start_time, end_time) -> None:
    global _count
    with _lock:
        _count += 1


def install() -> None:
    """Register the counter with LiteLLM (idempotent)."""
    if _on_success not in litellm.success_callback:
        litellm.success_callback.append(_on_success)
    if _on_failure not in litellm.failure_callback:
        litellm.failure_callback.append(_on_failure)


def calls() -> int:
    """Total LLM calls (successes + failures) since process start."""
    return _count
