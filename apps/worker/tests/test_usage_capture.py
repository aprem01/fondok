"""Offline unit tests for :class:`app.usage.UsageCapture`.

Regression guard for the prompt-cache *reporting* bug: langchain_anthropic
>= 1.4 splits cache-creation tokens by TTL. On every real cache-write the
Anthropic response carries a ``cache_creation`` breakdown object, which makes
the wrapper ZERO the generic ``input_token_details["cache_creation"]`` key and
surface the real count under ``ephemeral_5m_input_tokens`` instead. The old
UsageCapture read only ``cache_creation`` and therefore recorded 0 creation
tokens on every cached call — making prompt caching look broken in the cost
ledger even though Anthropic was genuinely writing (and later charging 10% on)
the cache.

These tests never hit the network. They build the usage payload with the
*installed* langchain_anthropic ``_create_usage_metadata`` so the test stays
faithful to whatever version is deployed.
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.usage import UsageCapture


def _usage_metadata_from_anthropic(**usage_kwargs: object) -> dict:
    """Render an Anthropic ``Usage`` through the installed langchain wrapper.

    Mirrors exactly what ``ChatAnthropic`` attaches to the returned
    ``AIMessage.usage_metadata`` in production, so the test exercises the same
    key layout UsageCapture sees at runtime.
    """
    from anthropic.types import Usage
    from langchain_anthropic.chat_models import _create_usage_metadata

    return dict(_create_usage_metadata(Usage(**usage_kwargs)))


def _run_capture(usage_metadata: dict, *, model: str = "claude-sonnet-4-6") -> UsageCapture:
    msg = AIMessage(content="ok", usage_metadata=usage_metadata)  # type: ignore[arg-type]
    msg.response_metadata = {"model": model}
    result = LLMResult(generations=[[ChatGeneration(message=msg)]])
    cap = UsageCapture()
    asyncio.run(cap.on_llm_end(result))
    return cap


def test_cache_creation_from_ttl_breakdown_is_recorded() -> None:
    """A cache-WRITE call must record its creation tokens even though the
    wrapper zeroes the generic ``cache_creation`` key and moves the count to
    ``ephemeral_5m_input_tokens``."""
    usage = _usage_metadata_from_anthropic(
        input_tokens=120,
        output_tokens=300,
        cache_creation_input_tokens=5000,
        cache_read_input_tokens=0,
        cache_creation={
            "ephemeral_5m_input_tokens": 5000,
            "ephemeral_1h_input_tokens": 0,
        },
    )
    # Sanity: this is the shape that used to defeat the old code.
    details = usage["input_token_details"]
    assert details.get("cache_creation", 0) == 0
    assert details.get("ephemeral_5m_input_tokens") == 5000

    cap = _run_capture(usage)
    assert cap.cache_creation_input_tokens == 5000, (
        "cache-creation tokens must be summed across the TTL-specific keys; "
        f"got {cap.cache_creation_input_tokens} from details={details}"
    )
    assert cap.cache_read_input_tokens == 0
    assert cap.input_tokens == usage["input_tokens"]


def test_cache_read_is_recorded() -> None:
    """A cache-HIT call surfaces cache_read normally (never zeroed)."""
    usage = _usage_metadata_from_anthropic(
        input_tokens=120,
        output_tokens=300,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=5000,
        cache_creation={
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 0,
        },
    )
    cap = _run_capture(usage)
    assert cap.cache_read_input_tokens == 5000
    assert cap.cache_creation_input_tokens == 0


def test_one_hour_ttl_breakdown_is_recorded() -> None:
    """1-hour ephemeral cache writes land under a different key — cover it."""
    usage = _usage_metadata_from_anthropic(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=3000,
        cache_read_input_tokens=0,
        cache_creation={
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 3000,
        },
    )
    cap = _run_capture(usage)
    assert cap.cache_creation_input_tokens == 3000


def test_no_cache_leaves_counters_zero() -> None:
    """An uncached call records zero cache tokens and non-zero plain input."""
    usage = _usage_metadata_from_anthropic(
        input_tokens=800,
        output_tokens=200,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    cap = _run_capture(usage)
    assert cap.cache_creation_input_tokens == 0
    assert cap.cache_read_input_tokens == 0
    assert cap.input_tokens == 800
    assert cap.output_tokens == 200


def test_generic_cache_creation_key_still_honored() -> None:
    """Defensive: if a response ever carries the generic ``cache_creation``
    key WITHOUT the TTL breakdown (older API shapes), it must still count."""
    # Build the metadata manually to simulate the no-breakdown path.
    usage = {
        "input_tokens": 5120,
        "output_tokens": 300,
        "total_tokens": 5420,
        "input_token_details": {"cache_read": 0, "cache_creation": 5000},
    }
    cap = _run_capture(usage)
    assert cap.cache_creation_input_tokens == 5000
