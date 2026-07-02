"""Unit tests for ``app.llm.invoke_with_escalation``.

Cost-opt pass T (2026-07) downgraded the Normalizer and QA Resolver
from Sonnet 4.6 to Haiku 4.5. The safety net is
``invoke_with_escalation``: on N consecutive JSON-parse /
ValidationError misses the helper re-issues the same messages on
Sonnet before surfacing the failure. These tests pin that behavior
deterministically — no Anthropic traffic.

What we cover:
  * Happy path — Haiku returns a schema-shaped object on first call,
    ``model_used`` reflects the Haiku model id (no escalation).
  * Escalation triggers — Haiku emits ValidationErrors up to threshold,
    then the helper falls back to Sonnet and returns.
  * Threshold respected — with threshold=1 we escalate on the very
    first miss; with threshold=3 we tolerate two misses then escalate
    on the third.
  * Escalation model equals primary — the helper raises the last error
    instead of infinitely looping (defensive guard).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-llm-escalation.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")
# Pin per-role model env vars so a dev .env file that pre-dates pass T
# (which set the Python defaults to Haiku 4.5) can't override the
# assertions below. The tests assert on the RESOLVED model id, not the
# code-level default — so we have to set what the resolver sees.
os.environ["ANTHROPIC_NORMALIZER_MODEL"] = "claude-haiku-4-5-20251001"
os.environ["ANTHROPIC_QA_RESOLVER_MODEL"] = "claude-haiku-4-5-20251001"
os.environ["ANTHROPIC_ESCALATION_MODEL"] = "claude-sonnet-4-6"


class _Envelope(BaseModel):
    verdict: str
    n: int


class _StubLLM:
    """LangChain-runnable stub whose ``ainvoke`` walks a scripted sequence.

    Each entry is either:
      * an ``_Envelope`` instance → returned as-is (happy path).
      * an ``Exception`` instance → raised (models a parse miss).
      * ``None`` → returned as-is (models the empty-envelope path the
        helper treats as a miss).
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any], config: Any | None = None) -> Any:
        self.calls.append(messages)
        assert self._script, "stub LLM exhausted its script"
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.mark.asyncio
async def test_happy_path_returns_first_result_no_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call succeeds → helper returns the primary model id."""
    from app.llm import invoke_with_escalation

    ok = _Envelope(verdict="resolved", n=1)
    llm = _StubLLM([ok])
    result, model_used = await invoke_with_escalation(
        role="normalizer",
        schema=_Envelope,
        messages=["hi"],
        max_tokens=1024,
        timeout=10,
        temperature=0.0,
        llm=llm,
    )
    assert result is ok
    # Primary role → whatever ANTHROPIC_NORMALIZER_MODEL is set to
    # (Haiku 4.5 as of pass T).
    assert model_used.startswith("claude-haiku-4-5") or "haiku" in model_used


@pytest.mark.asyncio
async def test_escalates_to_sonnet_after_threshold_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ValidationErrors on Haiku → helper escalates to Sonnet.

    We patch the ChatAnthropic import so the "escalation" branch doesn't
    try to hit the network; the escalation stub returns a healthy envelope.
    """
    from app.llm import invoke_with_escalation

    haiku_stub = _StubLLM(
        [
            ValidationError.from_exception_data("miss1", []),
            ValidationError.from_exception_data("miss2", []),
        ]
    )
    escalated_ok = _Envelope(verdict="resolved", n=42)
    escalated_stub = _StubLLM([escalated_ok])

    class _FakeChatAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def with_structured_output(self, _schema: Any, method: str = "function_calling") -> Any:  # noqa: ARG002
            return escalated_stub

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeChatAnthropic)

    result, model_used = await invoke_with_escalation(
        role="normalizer",
        schema=_Envelope,
        messages=["hi"],
        max_tokens=1024,
        timeout=10,
        temperature=0.0,
        llm=haiku_stub,
        escalation_threshold=2,
    )
    assert result is escalated_ok
    # After escalation, ``model_used`` must reflect the Sonnet escalation
    # model — the cost dashboard needs to see it.
    assert "sonnet" in model_used, f"expected sonnet in {model_used}"
    # Haiku stub was called exactly threshold=2 times before escalating.
    assert len(haiku_stub.calls) == 2
    # Escalated stub was called exactly once.
    assert len(escalated_stub.calls) == 1


@pytest.mark.asyncio
async def test_threshold_one_escalates_on_first_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """threshold=1 → escalate on the very first miss (no retry on Haiku)."""
    from app.llm import invoke_with_escalation

    haiku_stub = _StubLLM([ValueError("bad envelope")])
    escalated_ok = _Envelope(verdict="ok", n=1)
    escalated_stub = _StubLLM([escalated_ok])

    class _FakeChatAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def with_structured_output(self, _schema: Any, method: str = "function_calling") -> Any:  # noqa: ARG002
            return escalated_stub

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeChatAnthropic)

    result, model_used = await invoke_with_escalation(
        role="normalizer",
        schema=_Envelope,
        messages=["hi"],
        max_tokens=1024,
        timeout=10,
        temperature=0.0,
        llm=haiku_stub,
        escalation_threshold=1,
    )
    assert result is escalated_ok
    assert "sonnet" in model_used
    assert len(haiku_stub.calls) == 1
    assert len(escalated_stub.calls) == 1


@pytest.mark.asyncio
async def test_empty_envelope_counts_as_miss_and_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``ainvoke`` return of ``None`` is a parse miss — counts toward
    the escalation threshold."""
    from app.llm import invoke_with_escalation

    haiku_stub = _StubLLM([None, None])  # two "empty envelopes"
    escalated_ok = _Envelope(verdict="ok", n=2)
    escalated_stub = _StubLLM([escalated_ok])

    class _FakeChatAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def with_structured_output(self, _schema: Any, method: str = "function_calling") -> Any:  # noqa: ARG002
            return escalated_stub

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeChatAnthropic)

    result, model_used = await invoke_with_escalation(
        role="normalizer",
        schema=_Envelope,
        messages=["hi"],
        max_tokens=1024,
        timeout=10,
        temperature=0.0,
        llm=haiku_stub,
        escalation_threshold=2,
    )
    assert result is escalated_ok
    assert "sonnet" in model_used


@pytest.mark.asyncio
async def test_no_escalation_when_role_already_on_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the role's model equals the escalation model, we can't
    escalate — the helper must raise the last error, not loop forever.
    """
    from app.llm import invoke_with_escalation

    # Force the escalation model to equal the extractor role's model
    # (both Sonnet by default). Then an ``extractor`` invoke that
    # misses has nowhere to escalate to.
    llm = _StubLLM([ValueError("bad envelope"), ValueError("still bad")])
    with pytest.raises(ValueError, match="still bad"):
        await invoke_with_escalation(
            role="extractor",
            schema=_Envelope,
            messages=["hi"],
            max_tokens=1024,
            timeout=10,
            temperature=0.0,
            llm=llm,
            escalation_threshold=2,
        )
    # Both attempts consumed on the primary; no escalation happened.
    assert len(llm.calls) == 2
