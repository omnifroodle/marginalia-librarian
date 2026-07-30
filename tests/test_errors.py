"""classify_error: every funnel failure maps to a typed SSE error code."""

import litellm
import pytest
from opensearchpy.exceptions import ConnectionError as OSConnectionError
from opensearchpy.exceptions import ConnectionTimeout

from librarian.api.errors import _MESSAGES, classify_error
from librarian.pageindex import LLMCallError, LLMRateLimitExhausted


def test_rate_limit_exhausted():
    code, _ = classify_error(LLMRateLimitExhausted("429 past the budget"))
    assert code == "llm_rate_limit"


def test_raw_litellm_rate_limit():
    exc = litellm.RateLimitError("slow down", "openai", "test-model")
    code, _ = classify_error(exc)
    assert code == "llm_rate_limit"


def test_402_by_status_code_attribute():
    exc = Exception("provider says no")
    exc.status_code = 402
    code, message = classify_error(exc)
    assert code == "llm_payment"
    assert message == _MESSAGES["llm_payment"]


def test_402_by_message_string():
    # The NanoGPT shape seen live: an APIError whose message carries the code.
    exc = Exception("APIError: 402 - available 0.0256 USD, required 0.0445")
    assert classify_error(exc)[0] == "llm_payment"


def test_payment_required_by_phrase():
    assert classify_error(Exception("Payment Required"))[0] == "llm_payment"


def test_auth_errors():
    exc = litellm.AuthenticationError("bad key", "openai", "test-model")
    assert classify_error(exc)[0] == "llm_auth"


def test_keyless_deployment_is_auth_not_unavailable():
    # What a keyless stack actually raises (seen live): litellm wraps the
    # missing key as InternalServerError before any request is sent.
    exc = litellm.InternalServerError(
        "InternalServerError: Nano-gptException - Missing credentials. "
        "Please pass an `api_key`…",
        "openai",
        "test-model",
    )
    assert classify_error(exc)[0] == "llm_auth"


def test_transient_exhaustion_is_unavailable():
    assert classify_error(LLMCallError("gave up after retries"))[0] == "llm_unavailable"
    exc = litellm.ServiceUnavailableError("503", "openai", "test-model")
    assert classify_error(exc)[0] == "llm_unavailable"


def test_rate_limit_wins_over_generic_llm_error():
    # LLMRateLimitExhausted subclasses LLMCallError; the specific code wins.
    assert classify_error(LLMRateLimitExhausted("429"))[0] == "llm_rate_limit"


@pytest.mark.parametrize(
    "exc",
    [
        OSConnectionError("N/A", "connection refused", None),
        ConnectionTimeout("timeout", "opensearch timed out", None),
    ],
)
def test_opensearch_down(exc):
    assert classify_error(exc)[0] == "search_backend_unavailable"


def test_everything_else_is_internal():
    code, message = classify_error(RuntimeError("surprise"))
    assert code == "internal"
    assert message == _MESSAGES["internal"]


def test_every_code_has_presentable_copy():
    for code, message in _MESSAGES.items():
        assert message.endswith(".")
        assert "Traceback" not in message
