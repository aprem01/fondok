"""Phase 0.4 — ``ValueTrace.reason`` is additive and drives ``classify_state``.

Pure schema tests (no worker). The worker-side companion
``apps/worker/tests/test_reason_codes.py`` runs the same contract through the
worker's dependency so CI (which runs only the worker suite) gates it.
"""

from __future__ import annotations

import pytest
from fondok_schemas import ReasonCode, ValueInput, ValueTrace, apply_states, classify_state
from pydantic import ValidationError

NEEDS_REVIEW_CODES = {
    ReasonCode.NEEDS_REVIEW,
    ReasonCode.BASIS_MISMATCH,
    ReasonCode.PERIOD_MISMATCH,
    ReasonCode.UNIT_UNKNOWN,
}


# ─────────────────────────── round trip ───────────────────────────


def test_round_trip_with_reason() -> None:
    t = ValueTrace(value=0.0, note="no T-12 on the deal", reason=ReasonCode.NO_DOCUMENT)
    payload = t.model_dump(mode="json")
    assert payload["reason"] == "no_document"
    back = ValueTrace.model_validate(payload)
    assert back == t
    assert back.reason is ReasonCode.NO_DOCUMENT
    assert ValueTrace.model_validate_json(t.model_dump_json()) == t


def test_round_trip_without_reason() -> None:
    t = ValueTrace(
        value=3.0,
        formula="a + b",
        inputs=[ValueInput(name="a", value=1.0), ValueInput(name="b", value=2.0)],
    )
    payload = t.model_dump(mode="json")
    assert payload["reason"] is None
    back = ValueTrace.model_validate(payload)
    assert back == t
    assert back.reason is None


def test_reason_accepts_the_raw_string() -> None:
    t = ValueTrace.model_validate({"value": 0, "reason": "stale_run"})
    assert t.reason is ReasonCode.STALE_RUN


def test_reason_rejects_unknown_codes() -> None:
    with pytest.raises(ValidationError):
        ValueTrace.model_validate({"value": 0, "reason": "not_a_code"})


# ─────────────────────────── old JSON ───────────────────────────


OLD_JSON_TRACES = [
    # FON-25 shape: value + formula + inputs, no state.
    {
        "value": 1_000_000.0,
        "formula": "rooms_revenue = occupied_rooms x ADR",
        "inputs": [
            {"name": "occupied_rooms", "value": 5000.0, "assumption_key": "starting_occupancy"},
            {"name": "adr", "value": 200.0, "source": "t12_actual"},
        ],
        "source": None,
        "note": None,
    },
    # FON-65 shape: state persisted, still no reason key.
    {"value": 0.07, "formula": None, "inputs": [], "source": "seed", "note": None, "state": "assumption"},
    # Minimal.
    {"value": 42.0},
]


@pytest.mark.parametrize("payload", OLD_JSON_TRACES)
def test_old_json_without_reason_still_validates(payload: dict) -> None:
    t = ValueTrace.model_validate(payload)
    assert t.reason is None
    assert t.value == payload["value"]


# ─────────────────────────── classify_state ───────────────────────────


@pytest.mark.parametrize("code", sorted(NEEDS_REVIEW_CODES, key=lambda c: c.value))
def test_review_codes_map_to_needs_review_even_when_document_sourced(code: ReasonCode) -> None:
    t = ValueTrace(value=1.0, source="t12_actual", reason=code)
    assert classify_state(t) == "needs_review"
    # An explicit document label passed by the caller does not outrank the reason.
    assert classify_state(t, "om_broker") == "needs_review"


@pytest.mark.parametrize(
    "code", sorted(set(ReasonCode) - NEEDS_REVIEW_CODES, key=lambda c: c.value)
)
def test_other_codes_map_to_awaiting_data_even_when_computed(code: ReasonCode) -> None:
    t = ValueTrace(
        value=0.0,
        formula="noi = gop - fees",
        inputs=[ValueInput(name="gop", value=1.0, traces_to="expense.years[0].gop")],
        reason=code,
    )
    assert classify_state(t) == "awaiting_data"


def test_existing_mappings_unchanged_when_reason_is_none() -> None:
    assert classify_state(ValueTrace(value=1.0, source="needs_review")) == "needs_review"
    assert classify_state(ValueTrace(value=1.0, source="t12_actual")) == "document_sourced"
    assert classify_state(ValueTrace(value=1.0, source="om_comps")) == "document_sourced"
    assert classify_state(ValueTrace(value=1.0, source="seed")) == "assumption"
    assert classify_state(ValueTrace(value=1.0, source="ffe_default")) == "assumption"
    linked = ValueTrace(
        value=1.0,
        formula="x",
        inputs=[ValueInput(name="gop", value=1.0, traces_to="expense.years[0].gop")],
    )
    assert classify_state(linked) == "linked"
    calc = ValueTrace(value=1.0, formula="a + b", inputs=[ValueInput(name="a", value=1.0)])
    assert classify_state(calc) == "calculated"
    assert classify_state(ValueTrace(value=1.0)) == "assumption"


def test_apply_states_uses_the_reason() -> None:
    prov = {
        "years[0].noi": ValueTrace(value=0.0, reason=ReasonCode.PIN_ACTIVE),
        "years[0].gop": ValueTrace(value=0.0, source="t12_actual", reason=ReasonCode.UNIT_UNKNOWN),
        "years[0].rev": ValueTrace(value=1.0, source="t12_actual"),
    }
    apply_states(prov)
    assert prov["years[0].noi"].state == "awaiting_data"
    assert prov["years[0].gop"].state == "needs_review"
    assert prov["years[0].rev"].state == "document_sourced"
