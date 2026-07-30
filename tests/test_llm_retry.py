"""Retry behaviour of the pageindex litellm gateway wrappers (429 handling)."""

from __future__ import annotations

import asyncio
import email.utils
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import litellm
import pytest

from librarian.pageindex import utils
from librarian.pageindex.utils import (
    LLMCallError,
    LLMRateLimitExhausted,
    RetryPolicy,
    llm_acompletion,
    llm_completion,
)

POLICY = RetryPolicy(
    num_retries=2,
    backoff_max=5.0,
    rate_limit_retries=3,
    rate_limit_max_wait=60.0,
    timeout=5,
)


@pytest.fixture(autouse=True)
def _reset_gateway():
    utils._retry_policy = POLICY
    utils._cooldown_until = 0.0
    yield
    utils._retry_policy = RetryPolicy()
    utils._cooldown_until = 0.0


@pytest.fixture
def sleeps(monkeypatch):
    """Capture sleep durations without waiting, advancing a fake monotonic
    clock so the cooldown gate's sleep-and-recheck loop terminates."""
    recorded: list[float] = []
    clock = {"now": 1000.0}

    def fake_monotonic():
        return clock["now"]

    def fake_sleep(seconds):
        recorded.append(seconds)
        clock["now"] += seconds

    async def fake_asleep(seconds):
        recorded.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(utils.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(utils.time, "sleep", fake_sleep)
    monkeypatch.setattr(utils.asyncio, "sleep", fake_asleep)
    return recorded


def _rate_limit_error(retry_after: str | None = None) -> litellm.RateLimitError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return litellm.RateLimitError(
        message="429 rate limited",
        llm_provider="openai",
        model="openai/test",
        response=httpx.Response(
            429, headers=headers, request=httpx.Request("POST", "http://test")
        ),
    )


def _response(content: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")]
    )


def _completion_raising(errors: list[Exception]):
    """litellm.completion stand-in that raises each error once, then succeeds."""
    queue = list(errors)

    def fake(**kwargs):
        if queue:
            raise queue.pop(0)
        return _response()

    return fake


def test_retry_after_header_honored(monkeypatch, sleeps):
    monkeypatch.setattr(
        utils.litellm, "completion", _completion_raising([_rate_limit_error("7")])
    )
    assert llm_completion("openai/test", "hi") == "ok"
    assert len(sleeps) == 1
    # 7s from the header, up to 1s delay jitter, up to 2s wake-up jitter
    assert 7.0 <= sleeps[0] <= 10.5


def test_retry_after_capped_at_max_wait(monkeypatch, sleeps):
    monkeypatch.setattr(
        utils.litellm, "completion", _completion_raising([_rate_limit_error("9999")])
    )
    assert llm_completion("openai/test", "hi") == "ok"
    # capped delay plus up to 2s wake-up jitter
    assert POLICY.rate_limit_max_wait <= sleeps[0] <= POLICY.rate_limit_max_wait + 2.1


def test_retry_after_http_date(monkeypatch, sleeps):
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    header = email.utils.format_datetime(when)
    monkeypatch.setattr(
        utils.litellm, "completion", _completion_raising([_rate_limit_error(header)])
    )
    assert llm_completion("openai/test", "hi") == "ok"
    assert 20.0 <= sleeps[0] <= 34.0


def test_garbage_retry_after_falls_back_to_backoff(monkeypatch, sleeps):
    monkeypatch.setattr(
        utils.litellm, "completion", _completion_raising([_rate_limit_error("soon-ish")])
    )
    assert llm_completion("openai/test", "hi") == "ok"
    # attempt 1 backoff: 4 * 2**1 + uniform(0, 2), plus up to 2s wake-up jitter
    assert 8.0 <= sleeps[0] <= 12.0


def test_backoff_grows_and_is_capped(monkeypatch, sleeps, caplog):
    errors = [_rate_limit_error() for _ in range(POLICY.rate_limit_retries)]
    monkeypatch.setattr(utils.litellm, "completion", _completion_raising(errors))
    with caplog.at_level("WARNING", logger="librarian.pageindex"):
        assert llm_completion("openai/test", "hi") == "ok"
    assert len(sleeps) == POLICY.rate_limit_retries
    assert sleeps == sorted(sleeps)  # backoff ranges don't overlap even with jitter
    assert all(s <= POLICY.rate_limit_max_wait + 2.1 for s in sleeps)
    assert "source=backoff" in caplog.text


def test_rate_limit_exhaustion_raises(monkeypatch, sleeps):
    def always_429(**kwargs):
        raise _rate_limit_error()

    monkeypatch.setattr(utils.litellm, "completion", always_429)
    with pytest.raises(LLMRateLimitExhausted):
        llm_completion("openai/test", "hi")
    assert len(sleeps) == POLICY.rate_limit_retries


def test_async_rate_limit_retry_and_exhaustion(monkeypatch, sleeps):
    succeed_after = [_rate_limit_error("7")]

    async def fake_acompletion(**kwargs):
        if succeed_after:
            raise succeed_after.pop(0)
        return _response()

    monkeypatch.setattr(utils.litellm, "acompletion", fake_acompletion)
    assert asyncio.run(llm_acompletion("openai/test", "hi")) == "ok"
    assert 7.0 <= sleeps[0] <= 10.5

    sleeps.clear()
    utils._cooldown_until = 0.0

    async def always_429(**kwargs):
        raise _rate_limit_error()

    monkeypatch.setattr(utils.litellm, "acompletion", always_429)
    with pytest.raises(LLMRateLimitExhausted):
        asyncio.run(llm_acompletion("openai/test", "hi"))
    assert len(sleeps) == POLICY.rate_limit_retries


def test_transient_exhaustion_raises_llm_call_error(monkeypatch, sleeps):
    def always_timeout(**kwargs):
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(utils.litellm, "completion", always_timeout)
    with pytest.raises(LLMCallError) as excinfo:
        llm_completion("openai/test", "hi")
    assert not isinstance(excinfo.value, LLMRateLimitExhausted)
    assert len(sleeps) == POLICY.num_retries
    assert all(s <= POLICY.backoff_max for s in sleeps)


def test_non_transient_propagates_immediately(monkeypatch, sleeps):
    def bad_request(**kwargs):
        raise litellm.BadRequestError(
            message="context too long", model="openai/test", llm_provider="openai"
        )

    monkeypatch.setattr(utils.litellm, "completion", bad_request)
    with pytest.raises(litellm.BadRequestError):
        llm_completion("openai/test", "hi")
    assert sleeps == []


def test_shared_cooldown_gates_subsequent_calls(monkeypatch, sleeps):
    # A cooldown set by some other call gates a fresh call: it sleeps the
    # cooldown out (plus wake-up jitter) before issuing its first request.
    monkeypatch.setattr(utils.litellm, "completion", lambda **kwargs: _response())
    utils._extend_cooldown(5.0)
    assert llm_completion("openai/test", "hi") == "ok"
    assert len(sleeps) == 1
    assert 5.0 <= sleeps[0] <= 7.1
