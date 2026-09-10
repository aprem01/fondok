"""Phase 0.3 / 0.4 — the worker sees the ReasonCode vocabulary and
``ValueTrace.reason`` through its ``fondok-schemas`` dependency.

The thorough contract tests live with the package
(``packages/schemas-py/tests/``); this file is the CI gate (CI runs only the
worker suite) and sits next to test_engine_provenance.py because ``reason``
is the provenance sidecar's refusal channel.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from fondok_schemas import (
    REASON_META,
    ReasonCode,
    Refusal,
    ValueTrace,
    classify_state,
)


def test_reason_vocabulary_is_closed_and_described() -> None:
    assert len(ReasonCode) == 16
    assert set(REASON_META) == set(ReasonCode)
    assert all(m["ui"] == "—" for m in REASON_META.values())


def test_value_trace_round_trips_with_and_without_reason() -> None:
    with_reason = ValueTrace(value=0.0, reason=ReasonCode.STR_UNAVAILABLE)
    dumped = with_reason.model_dump(mode="json")
    assert dumped["reason"] == "str_unavailable"
    assert ValueTrace.model_validate(dumped) == with_reason

    without = ValueTrace(value=1.0, formula="a + b")
    assert ValueTrace.model_validate(without.model_dump(mode="json")) == without
    assert without.reason is None


def test_old_persisted_trace_json_still_validates() -> None:
    old = {"value": 5.0, "formula": None, "inputs": [], "source": "t12_actual", "note": None}
    t = ValueTrace.model_validate(old)
    assert t.reason is None
    assert classify_state(t) == "document_sourced"


def test_reason_drives_state() -> None:
    assert classify_state(ValueTrace(value=1.0, reason=ReasonCode.BASIS_MISMATCH)) == "needs_review"
    assert classify_state(ValueTrace(value=1.0, reason=ReasonCode.NO_DOCUMENT)) == "awaiting_data"


def test_refusal_carrier_round_trips() -> None:
    r = Refusal(code=ReasonCode.AWAITING_ANALYST, concept="ic_recommendation")
    assert Refusal.model_validate(r.model_dump(mode="json")) == r
