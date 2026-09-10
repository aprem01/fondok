"""Phase 0.3 — the ReasonCode vocabulary is importable, closed, and described.

Run from ``packages/schemas-py`` (``python -m pytest -q``); pure Pydantic, no
worker dependencies. Locks the exact 16-code set and order so the TypeScript
mirrors (``packages/schemas-ts/src/index.ts`` and
``apps/web/src/lib/ontology/reasons.generated.ts``) have one fixed target.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import fondok_schemas
import pytest
from fondok_schemas import REASON_META, REFUSAL_GLYPH, ReasonCode, Refusal
from fondok_schemas.reasons import ReasonMeta
from pydantic import ValidationError

EXPECTED_CODES = [
    "no_document",
    "no_source",
    "unit_unknown",
    "period_mismatch",
    "basis_mismatch",
    "basis_excluded",
    "awaiting_analyst",
    "needs_review",
    "pin_active",
    "str_unavailable",
    "not_knowable_as_of",
    "as_of_unknown",
    "stale_run",
    "engine_skipped",
    "inconclusive",
    "not_applicable",
]


def test_exported_from_package_root() -> None:
    for name in ("ReasonCode", "Refusal", "REASON_META", "REFUSAL_GLYPH", "ReasonMeta"):
        assert name in fondok_schemas.__all__, name
        assert hasattr(fondok_schemas, name), name


def test_enum_is_exactly_the_sixteen_codes_in_order() -> None:
    assert [c.value for c in ReasonCode] == EXPECTED_CODES
    assert len(ReasonCode) == 16


def test_enum_is_a_str_so_it_serialises_as_its_value() -> None:
    assert isinstance(ReasonCode.NO_DOCUMENT, str)
    assert ReasonCode("basis_mismatch") is ReasonCode.BASIS_MISMATCH
    assert json.dumps({"r": ReasonCode.STALE_RUN}) == '{"r": "stale_run"}'


def test_meta_covers_every_code_exactly_once() -> None:
    assert set(REASON_META) == set(ReasonCode)
    assert list(REASON_META) == list(ReasonCode)  # same order as the enum


@pytest.mark.parametrize("code", list(ReasonCode))
def test_meta_entries_are_complete(code: ReasonCode) -> None:
    meta: ReasonMeta = REASON_META[code]
    assert set(meta) == {"label", "ui", "explanation"}
    assert meta["label"].strip() and len(meta["label"]) <= 40
    assert meta["ui"] == REFUSAL_GLYPH == "—"
    # One plain sentence for a tester: non-empty, ends in a full stop.
    assert meta["explanation"].strip().endswith(".")


def test_labels_are_unique() -> None:
    labels = [m["label"] for m in REASON_META.values()]
    assert len(labels) == len(set(labels))


def test_refusal_round_trips_through_json() -> None:
    doc = uuid4()
    since = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    r = Refusal(
        code=ReasonCode.PERIOD_MISMATCH,
        detail="only a YTD slice",
        concept="rooms_revenue",
        document_id=doc,
        since=since,
    )
    payload = r.model_dump(mode="json")
    assert payload["code"] == "period_mismatch"
    assert payload["document_id"] == str(doc)
    back = Refusal.model_validate(payload)
    assert back == r
    assert Refusal.model_validate_json(r.model_dump_json()) == r


def test_refusal_minimal_and_string_code() -> None:
    r = Refusal.model_validate({"code": "no_document"})
    assert r.code is ReasonCode.NO_DOCUMENT
    assert r.detail is None and r.concept is None
    assert r.document_id is None and r.since is None


def test_refusal_rejects_unknown_code_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Refusal.model_validate({"code": "missing"})
    with pytest.raises(ValidationError):
        Refusal.model_validate({"code": "no_document", "message": "nope"})
