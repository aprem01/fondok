"""Unit tests for the QA Resolver agent (``apps/worker/app/agents/qa_resolver``).

What we pin:

  * Output parse — the agent's structured envelope round-trips into the
    canonical ``ProposedOverride`` schema (verdict + summary +
    proposed_overrides + audit_note).
  * Allow-list filtering — off-catalog ``field_path`` rows are dropped
    silently, not propagated to the caller.
  * Percentage normalization — when the LLM emits 80 for a 0..1 field
    (cap rate / LTV / occupancy / interest rate) the agent normalizes
    to 0.80 before persisting.
  * Empty proposed_overrides — happy path when the broker reply
    resolves the variance without naming any specific override.
  * Empty inputs — analyst_question or broker_response missing → the
    agent returns success=False instead of calling the LLM.

The Anthropic call is mocked end-to-end; no real LLM traffic.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-qa-resolver.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")
# Disable per-deal budget enforcement for these unit tests — the API
# path test covers that separately.
os.environ["DEFAULT_DEAL_BUDGET_USD"] = "0"


def _qa_input(**overrides: Any) -> Any:
    """Build a baseline QAResolverInput with sensible defaults."""
    from app.agents.qa_resolver import QAResolverInput

    base = dict(
        deal_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        broker_question_id="33333333-3333-3333-3333-333333333333",
        analyst_question=(
            "F&B revenue declined 16% YoY (2024 → 2025: $1.20M → $1.00M). "
            "Can you explain the driver?"
        ),
        broker_response=(
            "F&B operator contract was terminated November 2024. We've "
            "been running the restaurant in-house since with leaner labor; "
            "expect FB margin to recover to the pre-2024 18% baseline."
        ),
        supporting_data={
            "line_item": "fb_revenue",
            "period_key": "2024_vs_2025",
            "variance_pct": -0.16,
            "actual_prior": 1_200_000.0,
            "actual_current": 1_000_000.0,
            "threshold_pct": 0.15,
        },
        current_assumptions={
            "p_and_l_usali.operating_revenue.fb_revenue": 1_000_000.0,
            "p_and_l_usali.operational_kpis.occupancy_pct": 0.78,
        },
    )
    base.update(overrides)
    return QAResolverInput(**base)


class _StubLLM:
    """LangChain runnable stub that returns a canned envelope."""

    def __init__(self, envelope_dict: dict[str, Any]) -> None:
        self._envelope = envelope_dict

    async def ainvoke(self, _messages: list[Any], config: Any | None = None) -> Any:
        # Mirror UsageCapture so the agent stamps a non-empty ModelCall.
        if config and "callbacks" in (config or {}):
            for cb in config["callbacks"]:
                # UsageCapture is an AsyncCallbackHandler — populate via
                # on_llm_end is unwieldy in a unit test, so we set the
                # attributes directly.
                cb.input_tokens = 1200
                cb.output_tokens = 240
                cb.model = "claude-sonnet-4-6-stub"
        from app.agents.qa_resolver import _ResolverEnvelope

        return _ResolverEnvelope.model_validate(self._envelope)


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, envelope: dict[str, Any]) -> None:
    """Replace ``_build_llm`` so ``run_qa_resolver`` uses our stub."""
    from app.agents import qa_resolver as qr

    def stub() -> Any:
        return _StubLLM(envelope)

    monkeypatch.setattr(qr, "_build_llm", stub)
    # Avoid the cost-persistence side-effect (writes to model_calls table).
    async def noop_persist(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "app.cost_persistence.persist_model_calls_standalone", noop_persist
    )


# ─────────────────────────── tests ────────────────────────────


@pytest.mark.asyncio
async def test_resolved_with_two_proposed_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: resolved verdict + two proposed overrides land typed."""
    _patch_resolver(
        monkeypatch,
        envelope={
            "verdict": "resolved",
            "summary": (
                "Broker confirmed F&B contract terminated Nov-24; self-managed "
                "since with leaner labor. FB margin expected to recover to "
                "the pre-closure 18% baseline."
            ),
            "proposed_overrides": [
                {
                    "field_path": "p_and_l_usali.operating_revenue.fb_revenue",
                    "value": 1_150_000.0,
                    "rationale": (
                        "Broker reset F&B contract Nov-24; reverting FB revenue "
                        "to the pre-closure baseline they named."
                    ),
                    "confidence": "high",
                },
                {
                    "field_path": "p_and_l_usali.departmental_expenses.food_beverage",
                    "value": 940_000.0,
                    "rationale": (
                        "Self-managed operations imply ~18% FB margin per "
                        "broker — restating the expense line accordingly."
                    ),
                    "confidence": "medium",
                },
            ],
            "audit_note": (
                "Per broker reply: F&B operator terminated Nov-24, self-managed "
                "since; FB margin restated to pre-closure 18% baseline."
            ),
        },
    )

    from app.agents.qa_resolver import run_qa_resolver

    out = await run_qa_resolver(_qa_input())

    assert out.success is True
    assert out.error is None
    assert out.verdict == "resolved"
    assert "self-managed" in out.summary
    assert len(out.proposed_overrides) == 2
    assert out.proposed_overrides[0].field_path == (
        "p_and_l_usali.operating_revenue.fb_revenue"
    )
    assert out.proposed_overrides[0].confidence == "high"
    assert out.proposed_overrides[1].confidence == "medium"
    assert "broker reply" in out.audit_note.lower()
    # Cost bookkeeping: exactly one model_call entry.
    assert len(out.model_calls) == 1
    assert out.model_calls[0].agent_name == "qa_resolver"
    assert out.model_calls[0].input_tokens == 1200


@pytest.mark.asyncio
async def test_partially_resolved_no_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broker addresses part of it but leaves a gap — empty override list is valid."""
    _patch_resolver(
        monkeypatch,
        envelope={
            "verdict": "partially_resolved",
            "summary": (
                "Broker confirmed insurance market hardening but couldn't "
                "name a placement amount. Recommend follow-up before IC."
            ),
            "proposed_overrides": [],
            "audit_note": (
                "Per broker reply: insurance hardening confirmed; no "
                "renewal quote received. Underwrite to peer +40% pending."
            ),
        },
    )

    from app.agents.qa_resolver import run_qa_resolver

    out = await run_qa_resolver(_qa_input())

    assert out.success is True
    assert out.verdict == "partially_resolved"
    assert out.proposed_overrides == []
    assert "follow-up" in out.summary


@pytest.mark.asyncio
async def test_off_catalog_field_path_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An override naming a path not in ALLOWED_OVERRIDE_PATHS is dropped."""
    _patch_resolver(
        monkeypatch,
        envelope={
            "verdict": "resolved",
            "summary": "Broker explained the swing.",
            "proposed_overrides": [
                # Valid path — kept.
                {
                    "field_path": "p_and_l_usali.fixed_charges.insurance",
                    "value": 720_000.0,
                    "rationale": "Broker confirmed renewal at $720K.",
                    "confidence": "high",
                },
                # Off-catalog path — dropped silently.
                {
                    "field_path": "made.up.path.value",
                    "value": 1.0,
                    "rationale": "Hallucinated path.",
                    "confidence": "low",
                },
            ],
            "audit_note": "Per broker reply: insurance renewal at $720K.",
        },
    )

    from app.agents.qa_resolver import run_qa_resolver

    out = await run_qa_resolver(_qa_input())

    assert out.success is True
    # Only the on-catalog path survived.
    assert len(out.proposed_overrides) == 1
    assert out.proposed_overrides[0].field_path == (
        "p_and_l_usali.fixed_charges.insurance"
    )


@pytest.mark.asyncio
async def test_percentage_value_normalized_from_pct_to_fraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM occasionally emits 78 for occupancy_pct; the agent normalizes to 0.78."""
    _patch_resolver(
        monkeypatch,
        envelope={
            "verdict": "resolved",
            "summary": "Broker shared updated occupancy and cap-rate guidance.",
            "proposed_overrides": [
                {
                    "field_path": "p_and_l_usali.operational_kpis.occupancy_pct",
                    "value": 78.0,  # emitted as percent — must normalize.
                    "rationale": "Broker confirmed stabilized 78% occupancy.",
                    "confidence": "high",
                },
                {
                    "field_path": "broker_proforma.entry_cap_rate",
                    "value": 0.072,  # already 0..1 — leave alone.
                    "rationale": "Broker named 7.2% market cap.",
                    "confidence": "medium",
                },
            ],
            "audit_note": (
                "Per broker reply: stabilized occupancy 78%; market cap 7.2%."
            ),
        },
    )

    from app.agents.qa_resolver import run_qa_resolver

    out = await run_qa_resolver(_qa_input())

    occ = next(
        o for o in out.proposed_overrides
        if o.field_path.endswith("occupancy_pct")
    )
    cap = next(
        o for o in out.proposed_overrides
        if o.field_path.endswith("entry_cap_rate")
    )
    assert occ.value == pytest.approx(0.78)
    assert cap.value == pytest.approx(0.072)


@pytest.mark.asyncio
async def test_missing_broker_response_short_circuits() -> None:
    """Empty broker_response → success=False, no LLM call."""
    from app.agents.qa_resolver import run_qa_resolver

    out = await run_qa_resolver(_qa_input(broker_response="   "))

    assert out.success is False
    assert "required" in (out.error or "")
    assert out.verdict is None
    assert out.proposed_overrides == []


@pytest.mark.asyncio
async def test_llm_failure_surfaces_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raised exception inside the LLM call → success=False with error str."""
    from app.agents import qa_resolver as qr

    class _BoomLLM:
        async def ainvoke(self, _messages: list[Any], config: Any | None = None) -> Any:
            raise RuntimeError("anthropic 503")

    monkeypatch.setattr(qr, "_build_llm", lambda: _BoomLLM())

    from app.agents.qa_resolver import run_qa_resolver

    out = await run_qa_resolver(_qa_input())
    assert out.success is False
    assert "anthropic 503" in (out.error or "")
    assert out.proposed_overrides == []
