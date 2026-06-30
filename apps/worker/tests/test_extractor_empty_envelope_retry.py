"""Sam QA 2026-06-30 — empty_envelope first-pass flakiness regression.

When 8 docs were uploaded simultaneously to a fresh deal, 3 of 8 docs
landed FAILED on first pass with ``error_kind='empty_envelope'`` ("the
extractor ran on real text but emitted no grounded fields") — and ALL
3 recovered cleanly on a manual reprocess. 37.5% first-pass failure
under burst load.

Root cause: two stacked failure modes —

1. Anthropic ``overloaded_error`` (HTTP 529) bursts under load that
   blew past the SDK's default ``max_retries=2``. Fix at the LLM
   factory layer in :mod:`app.llm` — extractor now uses
   ``max_retries=6`` (override via ``EXTRACTOR_LLM_MAX_RETRIES``).

2. The structured-output parser intermittently returns an empty
   envelope on a successful API call when the model emits prose
   instead of the JSON tool args, even AFTER the salvage path runs.
   Fix at the agent layer — ONE in-band retry per chunk with a short
   jittered backoff. Hard-capped at 1 retry so a doc that genuinely
   has nothing to extract still lands FAILED (no infinite loop).

These tests mock ``_invoke_llm`` so the suite stays offline — no API
key required, runs on every push. They assert:

* First-call empty envelope + second-call populated → success=True
* First-call exception   + second-call populated → success=True
* Both calls empty → success=False (retry budget capped at 1)
* First call succeeds → no retry attempted (no wasted spend)
* Concurrent burst of 8 chunks where 3 flake on first pass → 0
  silently-failed docs (the original bug)
* ``EXTRACTOR_LLM_MAX_RETRIES`` and ``LLM_MAX_RETRIES`` env vars
  propagate to the ChatAnthropic client
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Test isolation — pattern mirrors test_extractor_throttle.py. Set the
# SQLite DSN BEFORE the worker imports its settings module so a
# developer's shell-level DATABASE_URL doesn't bleed in.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-empty-envelope-retry.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")
# Snap the in-band retry budget to its production default so an
# operator who set EXTRACTOR_EMPTY_ENVELOPE_RETRIES=0 in their shell
# doesn't silently flip every assertion.
os.environ["EXTRACTOR_EMPTY_ENVELOPE_RETRIES"] = "1"


@pytest.fixture(autouse=True)
def _reset_extractor_retry_budget() -> Any:
    """Reload the module so the env var above takes effect for every
    test, even when the module was imported earlier in the session."""
    from app.agents import extractor

    extractor._EXTRACTOR_EMPTY_ENVELOPE_RETRY_BUDGET = (
        extractor._read_empty_envelope_retry_budget()
    )
    yield


def _make_doc() -> Any:
    """Tiny ExtractorDocument — content is irrelevant, the LLM call
    is mocked. We just need the result-builder bookkeeping to be
    happy with a valid doc_type + filename."""
    from fondok_schemas import DocType

    from app.agents.extractor import ExtractorDocument

    return ExtractorDocument(
        document_id="doc-empty-envelope-test",
        filename="test_doc.pdf",
        doc_type=DocType.OM,
        content="Some parsed text content for the extractor to look at.",
        source_pages=[1],
    )


class _EmptyEnvelope:
    """Test double for the empty-envelope failure mode.

    ``_ExtractorEnvelope`` enforces ``min_length=1`` on ``fields``, so a
    real production empty envelope manifests as a ``ValidationError``
    raised inside ``_invoke_llm`` — which is exactly the exception path
    we want to exercise. Tests that need to simulate "API succeeded but
    came back empty" can either:

      * Raise ``ValidationError`` directly (mirrors the in-prod path).
      * Raise ``ValueError("Extractor LLM returned empty envelope ...")``
        — what ``_invoke_llm`` itself emits when salvage fails.

    Both routes hit ``_attempt_extract``'s ``except`` block which returns
    ``retry_eligible=True``. We standardize on the ValueError form in
    these tests because it's the wrapper ``_invoke_llm`` emits.
    """

    pass


def _envelope(*, fields: int) -> Any:
    """Build a ``_ExtractorEnvelope`` carrying ``fields`` ground-true rows.

    ``fields=0`` would fail validation (``min_length=1``) so the helper
    rejects it — callers that need to simulate an empty envelope should
    raise an exception from the mock instead.
    """
    from app.agents.extractor import _ExtractionRow, _ExtractorEnvelope

    if fields < 1:
        raise ValueError(
            "use raise_empty_envelope_error() to simulate the empty path; "
            "_ExtractorEnvelope.fields has min_length=1"
        )

    rows = [
        _ExtractionRow(
            field_name=f"broker_proforma.field_{i}",
            value=float(i * 1000),
            unit="USD",
            source_page=1,
            confidence=0.9,
            raw_text=f"Row {i} raw text",
        )
        for i in range(fields)
    ]
    return _ExtractorEnvelope(
        fields=rows,
        overall_confidence=0.9,
        low_confidence_fields=[],
        requires_human_review=False,
        notes=None,
    )


def _empty_envelope_error() -> Exception:
    """The exact exception ``_invoke_llm`` raises when the parsed
    envelope is missing/empty AND the raw-AIMessage salvage path
    returns None — i.e. the in-production ``empty_envelope`` shape."""
    return ValueError(
        "Extractor LLM returned empty envelope and no salvageable raw JSON"
    )


# ─────────────────────── core retry contract ───────────────────────


async def test_first_empty_envelope_then_success_recovers() -> None:
    """The PRIMARY bug: first pass returns the empty envelope, retry
    populates the result. The pipeline must take the retry outcome.

    In production the empty-envelope path manifests as a ValueError
    raised by ``_invoke_llm`` (because ``_ExtractorEnvelope.fields``
    has ``min_length=1`` and the salvage path returns None when the
    raw AIMessage carries no recoverable JSON). The mock raises the
    exact exception string for fidelity."""
    from app.agents import extractor

    call_count = {"n": 0}

    async def fake_invoke(llm: Any, messages: Any, usage: Any = None) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _empty_envelope_error()
        return _envelope(fields=5)  # retry recovers

    with patch.object(extractor, "_invoke_llm", side_effect=fake_invoke):
        with patch.object(extractor, "_build_llm", return_value=object()):
            # Force the jittered backoff to 0 so the test doesn't sleep 1-3s.
            with patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MIN_S", 0.0
            ), patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MAX_S", 0.0
            ):
                result, model_call = await extractor._extract_one(
                    _make_doc(),
                    deal_id="deal-1",
                    system_blocks=["instructions"],
                )

    assert call_count["n"] == 2, "expected exactly one in-band retry"
    assert result.success, f"retry should have recovered: {result.error!r}"
    assert len(result.fields) == 5, (
        f"second attempt had 5 fields; got {len(result.fields)}"
    )
    assert model_call is not None


async def test_first_exception_then_success_recovers() -> None:
    """An exception path (timeout, overloaded past SDK retries, etc.)
    is also retry-eligible — retry must run and succeed."""
    from app.agents import extractor

    call_count = {"n": 0}

    async def fake_invoke(llm: Any, messages: Any, usage: Any = None) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated Anthropic overloaded_error after SDK gives up")
        return _envelope(fields=3)

    with patch.object(extractor, "_invoke_llm", side_effect=fake_invoke):
        with patch.object(extractor, "_build_llm", return_value=object()):
            with patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MIN_S", 0.0
            ), patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MAX_S", 0.0
            ):
                result, model_call = await extractor._extract_one(
                    _make_doc(),
                    deal_id="deal-2",
                    system_blocks=["instructions"],
                )

    assert call_count["n"] == 2
    assert result.success, f"exception path should have recovered: {result.error!r}"
    assert len(result.fields) == 3
    # Note: model_call is None on the exception path's first attempt, and
    # the retry built a fresh UsageCapture so the retry's model_call is
    # what we expect to see surface to the caller.
    assert model_call is not None


async def test_both_empty_envelopes_caps_at_one_retry() -> None:
    """Doc that genuinely has nothing to extract: 2 attempts, then
    accept failure. NO infinite loop, NO third attempt."""
    from app.agents import extractor

    call_count = {"n": 0}

    async def fake_invoke(llm: Any, messages: Any, usage: Any = None) -> Any:
        call_count["n"] += 1
        raise _empty_envelope_error()

    with patch.object(extractor, "_invoke_llm", side_effect=fake_invoke):
        with patch.object(extractor, "_build_llm", return_value=object()):
            with patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MIN_S", 0.0
            ), patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MAX_S", 0.0
            ):
                result, _ = await extractor._extract_one(
                    _make_doc(),
                    deal_id="deal-3",
                    system_blocks=["instructions"],
                )

    assert call_count["n"] == 2, (
        f"retry must cap at 1 (so 2 total attempts); got {call_count['n']}"
    )
    assert not result.success, "both attempts empty → success must be False"
    # The error string surfaces the underlying _invoke_llm message so
    # an operator can grep logs for "empty envelope" and find both the
    # first attempt and the retry's failure.
    assert "empty envelope" in (result.error or "").lower()


async def test_successful_first_attempt_skips_retry() -> None:
    """Happy path: when the first attempt succeeds we MUST NOT make a
    second LLM call. The retry budget is purely for failure recovery,
    not for double-spending under normal load."""
    from app.agents import extractor

    call_count = {"n": 0}

    async def fake_invoke(llm: Any, messages: Any, usage: Any = None) -> Any:
        call_count["n"] += 1
        return _envelope(fields=8)

    with patch.object(extractor, "_invoke_llm", side_effect=fake_invoke):
        with patch.object(extractor, "_build_llm", return_value=object()):
            result, _ = await extractor._extract_one(
                _make_doc(),
                deal_id="deal-4",
                system_blocks=["instructions"],
            )

    assert call_count["n"] == 1, "successful first attempt must not retry"
    assert result.success
    assert len(result.fields) == 8


# ─────────────────────── concurrent burst — the original bug shape ──


async def test_concurrent_burst_of_eight_with_three_flakes_loses_nothing() -> None:
    """Reproduce the 2026-06-30 production failure shape: 8 chunks fire
    concurrently, 3 chunks intermittently return an empty envelope on
    their FIRST call (the 37.5% rate Sam saw). Assert that the in-band
    retry recovers every flake, so the pipeline reports 8/8 successful.

    The original bug was that 3/8 silently landed FAILED with
    error_kind='empty_envelope' and the analyst had to manually click
    Retry on each one. Post-fix: all 8 land EXTRACTED on the first
    upload pass."""
    from app.agents import extractor

    # Per-chunk attempt counter, keyed by filename so the test can
    # interleave concurrent attempts without tracking call order.
    attempts: dict[str, int] = {}
    # The three flakers: they return empty on attempt 1, populated on
    # attempt 2 — the exact failure shape Sam observed.
    flake_filenames = {"chunk_2.pdf", "chunk_5.pdf", "chunk_7.pdf"}

    async def fake_invoke(llm: Any, messages: Any, usage: Any = None) -> Any:
        # Pull the filename out of the user message so the mock can
        # decide per-doc. Each chunk's HumanMessage was built from
        # _build_user_prompt which embeds the filename — we cheat and
        # inspect ``messages[-1].content``.
        from langchain_core.messages import HumanMessage

        human_msg = next(
            (m for m in reversed(messages) if isinstance(m, HumanMessage)),
            None,
        )
        body = human_msg.content if human_msg is not None else ""
        # Find which chunk file this is by matching a filename token.
        fname = next(
            (f for f in [
                "chunk_1.pdf", "chunk_2.pdf", "chunk_3.pdf", "chunk_4.pdf",
                "chunk_5.pdf", "chunk_6.pdf", "chunk_7.pdf", "chunk_8.pdf",
            ] if f in body),
            "unknown",
        )
        attempts[fname] = attempts.get(fname, 0) + 1
        if fname in flake_filenames and attempts[fname] == 1:
            raise _empty_envelope_error()
        return _envelope(fields=4)

    # Build 8 ExtractorDocuments with distinct filenames.
    from fondok_schemas import DocType

    from app.agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
    )

    docs = [
        ExtractorDocument(
            document_id=f"doc-{i}",
            filename=f"chunk_{i}.pdf",
            doc_type=DocType.OM,
            content=f"This is the content of chunk_{i}.pdf — a Year-1 broker proforma sample.",
            source_pages=[i],
        )
        for i in range(1, 9)
    ]
    payload = ExtractorInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id="deal-burst",
        documents=docs,
    )

    with patch.object(extractor, "_invoke_llm", side_effect=fake_invoke):
        with patch.object(extractor, "_build_llm", return_value=object()):
            with patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MIN_S", 0.0
            ), patch.object(
                extractor, "_EMPTY_ENVELOPE_RETRY_DELAY_MAX_S", 0.0
            ):
                out = await run_extractor(payload)

    assert out.success, f"extractor batch failed: {out.error}"
    assert len(out.extracted_documents) == 8
    per_doc_success = [d.success for d in out.extracted_documents]
    assert all(per_doc_success), (
        f"expected 8/8 success after retry; got {sum(per_doc_success)}/8 "
        f"(this is the original Sam QA 2026-06-30 bug)"
    )
    # Each flaker should have made exactly 2 attempts, every other
    # chunk exactly 1.
    for fname in flake_filenames:
        assert attempts[fname] == 2, (
            f"{fname} should have retried once (2 total attempts); "
            f"got {attempts[fname]}"
        )
    for fname in {f"chunk_{i}.pdf" for i in range(1, 9)} - flake_filenames:
        assert attempts[fname] == 1, (
            f"{fname} succeeded first try and should NOT have retried; "
            f"got {attempts[fname]} attempts"
        )


# ─────────────────────── retry-budget knob ───────────────────────


async def test_retry_disabled_via_env_skips_retry() -> None:
    """When ``EXTRACTOR_EMPTY_ENVELOPE_RETRIES=0`` the in-band retry is
    fully disabled (the SDK-level retry budget still applies, but a
    successful-but-empty response surfaces as failure immediately).
    Useful escape hatch for cost-sensitive eval runs."""
    from app.agents import extractor

    with patch.object(extractor, "_EXTRACTOR_EMPTY_ENVELOPE_RETRY_BUDGET", 0):
        call_count = {"n": 0}

        async def fake_invoke(llm: Any, messages: Any, usage: Any = None) -> Any:
            call_count["n"] += 1
            raise _empty_envelope_error()

        with patch.object(extractor, "_invoke_llm", side_effect=fake_invoke):
            with patch.object(extractor, "_build_llm", return_value=object()):
                result, _ = await extractor._extract_one(
                    _make_doc(),
                    deal_id="deal-no-retry",
                    system_blocks=["instructions"],
                )

        assert call_count["n"] == 1, "retry budget=0 must not retry"
        assert not result.success


def test_retry_budget_clamps_to_one() -> None:
    """``EXTRACTOR_EMPTY_ENVELOPE_RETRIES=99`` clamps to 1. A doc that
    can't be extracted in 2 attempts almost certainly needs human
    reclassification, not another LLM round-trip — and an unclamped
    knob is an operational footgun (an env-var typo could 100x the
    spend on a single bad upload)."""
    from app.agents import extractor

    with patch.dict(os.environ, {"EXTRACTOR_EMPTY_ENVELOPE_RETRIES": "99"}):
        assert extractor._read_empty_envelope_retry_budget() == 1
    with patch.dict(os.environ, {"EXTRACTOR_EMPTY_ENVELOPE_RETRIES": "-3"}):
        assert extractor._read_empty_envelope_retry_budget() == 0
    with patch.dict(os.environ, {"EXTRACTOR_EMPTY_ENVELOPE_RETRIES": "garbage"}):
        assert extractor._read_empty_envelope_retry_budget() == 1


# ─────────────────────── SDK retry budget plumbing ───────────────────


def test_extractor_max_retries_env_var_wires_through() -> None:
    """``EXTRACTOR_LLM_MAX_RETRIES`` / ``LLM_MAX_RETRIES`` override the
    role-default SDK retry count. The default for ``extractor`` is 6
    (bumped from langchain_anthropic's 2 because Anthropic
    ``overloaded_error`` bursts under load were exceeding the budget)."""
    from app.llm import _resolve_max_retries

    # Default — no env override.
    with patch.dict(os.environ, {}, clear=False):
        for key in ("EXTRACTOR_LLM_MAX_RETRIES", "LLM_MAX_RETRIES"):
            os.environ.pop(key, None)
        assert _resolve_max_retries("extractor", None) == 6
        assert _resolve_max_retries("router", None) == 2

    # Role-specific override wins.
    with patch.dict(os.environ, {"EXTRACTOR_LLM_MAX_RETRIES": "10"}):
        assert _resolve_max_retries("extractor", None) == 10
        # Other roles unaffected.
        assert _resolve_max_retries("router", None) == 2

    # Global override applies to roles WITHOUT a per-role var set.
    with patch.dict(
        os.environ,
        {"LLM_MAX_RETRIES": "4"},
        clear=False,
    ):
        os.environ.pop("EXTRACTOR_LLM_MAX_RETRIES", None)
        assert _resolve_max_retries("extractor", None) == 4
        assert _resolve_max_retries("router", None) == 4

    # Explicit caller override beats env.
    assert _resolve_max_retries("extractor", 0) == 0
    assert _resolve_max_retries("extractor", 12) == 12

    # Garbage env value falls back to the per-role default with a
    # warning — never raises.
    with patch.dict(
        os.environ,
        {"EXTRACTOR_LLM_MAX_RETRIES": "not-an-int"},
        clear=False,
    ):
        assert _resolve_max_retries("extractor", None) == 6
