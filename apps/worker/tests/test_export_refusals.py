"""Phase 4.2 — every dash an export writes is a TYPED refusal.

Before this phase a ``"—"`` in the live payload, the memo PDF or the workbook
was an untyped glyph: an LP could see that Fondok declined to produce a figure
but nothing said why, and nothing downstream could count or classify the
refusals. ``app.export.refusals.refuse`` now writes the same glyph and records
a :class:`~fondok_schemas.reasons.Refusal`.

What this module pins:

1. **Additive, provably.** ``load_live_payload``'s ``deal`` / ``memo`` dicts
   and the ``model`` key set are compared against
   ``fixtures/live_payload_pre_refusals.json`` — captured by running the
   pre-change tree — with only ``refusals`` allowed to be new.
2. **Every dash is accounted for.** Each ``"—"`` the payload carries has a
   ``Refusal`` on ``model["refusals"]``, and every recorded code is in the
   closed :class:`ReasonCode` vocabulary.
3. **The Excel sheet lists what the payload refused.** A live workbook whose
   payload carried refusals gains a "Refusals" sheet naming each concept, the
   code, and the meaning from ``REASON_META``; a payload with none gains no
   sheet (the Kimpton fixture workbook keeps its 19).
4. **The memo PDF footnote** lists the distinct reasons it dashed for, and
   nothing else about the rendered HTML moves.

Hermetic: per-test SQLite, no LLM, no network.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import (same pattern as
# test_live_payload_wave2_3.py, whose snapshot this file compares against).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-export-refusals.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_SNAPSHOT = Path(__file__).parent / "fixtures" / "live_payload_pre_refusals.json"

#: The one key Phase 4.2 adds to the model. Nothing else may appear.
_ADDED_MODEL_KEY = "refusals"

#: The one key Phase 4.3 adds to the memo header - the reason code beside
#: "Pending analyst decision", so a consumer holding the header never has to
#: re-derive the decision state from the deal's field_overrides.
_ADDED_HEADER_KEY = "recommendation_reason"


def _without_header_reason(memo: dict) -> dict:
    out = dict(memo)
    out["header"] = {k: v for k, v in memo["header"].items() if k != _ADDED_HEADER_KEY}
    return out

_DOWNSIDE = {"hold_years": 7, "starting_occupancy": 0.55}


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("engine_outputs", "scenarios", "extraction_results",
                    "documents", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _seed_deal(
    session, *, deal_id: str, tenant_id: str,
    name: str = "Harbor House Test", city: str | None = "Tampa, FL",
    keys: int | None = 150,
) -> None:
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, city, keys, field_overrides) "
            "VALUES (:id, :tenant, :name, :city, :keys, :fo)"
        ),
        {"id": deal_id, "tenant": tenant_id, "name": name, "city": city,
         "keys": keys, "fo": "{}"},
    )


async def _insert_scenario(
    session, *, deal_id: str, tenant_id: str, name: str, is_base: bool,
    run_id: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO scenarios (id, deal_id, tenant_id, name, description, "
            "is_base, overrides, last_run_id) VALUES "
            "(:id, :deal, :tenant, :name, :desc, :is_base, :ov, :run)"
        ),
        {"id": str(uuid4()), "deal": deal_id, "tenant": tenant_id, "name": name,
         "desc": None, "is_base": is_base, "ov": "[]", "run": run_id},
    )


def _shape(obj) -> object:
    return json.loads(json.dumps(obj, default=str, sort_keys=True))


async def _bare_payload():
    """A deal with no run, no city, no keys — maximum dash surface."""
    from app.database import get_session_factory
    from app.export.live_payload import load_live_payload

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id,
                         name="", city=None, keys=None)
        await session.commit()
        return await load_live_payload(session, deal_id, tenant_id)


async def _modeled_payload():
    """A deal with a canonical run + a saved downside scenario."""
    from app.database import get_session_factory
    from app.export.live_payload import load_live_payload
    from app.services.engine_runner import _coerce_uuid, run_all_engines

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    base_run, scen_run = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        await run_all_engines(session, deal_id=deal_id, tenant_id=tenant_id,
                              run_id=base_run)
        await run_all_engines(session, deal_id=deal_id, tenant_id=tenant_id,
                              run_id=scen_run, overrides=_DOWNSIDE)
        deal_uuid = str(_coerce_uuid(deal_id))
        await _insert_scenario(session, deal_id=deal_uuid, tenant_id=tenant_id,
                               name="Base Case", is_base=True, run_id=base_run)
        await _insert_scenario(session, deal_id=deal_uuid, tenant_id=tenant_id,
                               name="Downside", is_base=False, run_id=scen_run)
        await session.commit()
        return deal_id, await load_live_payload(session, deal_id, tenant_id)


# ═════════════════ 1. the addition is provably additive ═════════════════


@pytest.mark.asyncio
async def test_payload_matches_the_pre_change_snapshot_but_for_refusals() -> None:
    """``deal`` / ``memo`` byte-identical; ``model`` gains only ``refusals``.

    The snapshot was produced by running ``load_live_payload`` on the tree as
    it stood before Phase 4.2. If routing a dash through ``refuse`` ever
    changed a rendered value, this is where it shows up.
    """
    assert _SNAPSHOT.exists(), f"missing pre-change snapshot {_SNAPSHOT}"
    snap = json.loads(_SNAPSHOT.read_text())

    deal, model, memo = await _bare_payload()
    deal = {k: v for k, v in deal.items() if k != "id"}
    model_keys = sorted(k for k in model if k != "deal_id")

    assert _shape(deal) == snap["bare"]["deal"]
    assert _shape(_without_header_reason(memo)) == snap["bare"]["memo"]
    # ...and the one key 4.3 added is the reason, as a bare code.
    assert memo["header"][_ADDED_HEADER_KEY] == "awaiting_analyst"
    assert model_keys == sorted([*snap["bare"]["model_keys"], _ADDED_MODEL_KEY])
    # Every pre-existing model value is untouched too (the bare payload is
    # small enough to compare whole).
    for key, want in snap["bare"]["model"].items():
        assert _shape(model[key]) == want, f"model[{key!r}] drifted"

    _deal_id, (deal2, model2, memo2) = await _modeled_payload()
    deal2 = {k: v for k, v in deal2.items() if k != "id"}
    assert _shape(deal2) == snap["modeled"]["deal"]
    assert _shape(_without_header_reason(memo2)) == snap["modeled"]["memo"]
    assert memo2["header"][_ADDED_HEADER_KEY] == "awaiting_analyst"
    assert sorted(k for k in model2 if k != "deal_id") == sorted(
        [*snap["modeled"]["model_keys"], _ADDED_MODEL_KEY]
    )


# ═════════════════ 2. every dash carries a Refusal ═════════════════


def _dashes(node: object, trail: str = "") -> list[str]:
    """Paths of every value that is exactly the refusal glyph."""
    from fondok_schemas.reasons import REFUSAL_GLYPH

    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _dashes(v, f"{trail}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _dashes(v, f"{trail}[{i}]")
    elif node == REFUSAL_GLYPH:
        out.append(trail)
    return out


@pytest.mark.asyncio
async def test_every_dash_in_the_payload_has_a_typed_refusal() -> None:
    from fondok_schemas.reasons import REASON_META, ReasonCode

    deal, model, memo = await _bare_payload()

    dashes = _dashes(deal) + _dashes(_without_header_reason(memo)) + _dashes(
        {k: v for k, v in model.items() if k != "refusals"}
    )
    assert dashes, "the bare payload should carry dashes to account for"

    refusals = model["refusals"]
    assert refusals, "dashes were written but nothing was recorded"
    assert len(refusals) >= 1

    for r in refusals:
        assert isinstance(r.code, ReasonCode)
        assert r.code in REASON_META
        # Every refusal explains itself to an analyst, not just to a client.
        assert r.detail, f"{r.code} recorded without a detail"

    # The deck's own strings each name the concept they refused.
    concepts = {r.concept for r in refusals}
    assert {"deal.city", "deal.brand", "deal.keys", "deal.service"} <= concepts
    assert "property_overview.name" in concepts
    assert "property_overview.year_built" in concepts


@pytest.mark.asyncio
async def test_refusals_are_deduped_and_deterministic() -> None:
    """Same deal, same list — a leaf formatter refusing per row is collapsed."""
    _deal_id, (_d1, model1, _m1) = await _modeled_payload()
    keys1 = [(r.code.value, r.concept, r.detail) for r in model1["refusals"]]
    assert len(keys1) == len(set(keys1)), "duplicate refusals leaked onto the payload"

    _deal_id2, (_d2, model2, _m2) = await _modeled_payload()
    keys2 = [(r.code.value, r.concept, r.detail) for r in model2["refusals"]]
    assert keys1 == keys2


@pytest.mark.asyncio
async def test_a_complete_deal_records_fewer_refusals_than_a_bare_one() -> None:
    """Grounding a value removes its refusal — the channel tracks reality."""
    _bd, model_bare, _bm = (None, *(await _bare_payload())[1:])
    _deal_id, (_d, model_modeled, _m) = await _modeled_payload()

    bare = {r.concept for r in model_bare["refusals"]}
    modeled = {r.concept for r in model_modeled["refusals"]}
    # The modeled deal has a city and a key count; the bare one does not.
    assert "deal.city" in bare and "deal.city" not in modeled
    assert "deal.keys" in bare and "deal.keys" not in modeled


@pytest.mark.asyncio
async def test_export_memo_header_carries_the_recommendation_reason() -> None:
    """Phase 4.3 reaches the OTHER ``ic_recommendation_label`` consumer.

    A caller holding the export header must never have to re-derive the
    decision state from the deal's ``field_overrides``: the reason travels
    beside the label, as the same bare code ``GET /deals/{id}/memo`` sends.
    """
    from app.database import get_session_factory
    from app.export.live_payload import load_live_payload

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, field_overrides) "
                "VALUES (:id, :tenant, 'Confirmed', 'Tampa, FL', 150, :fo)"
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "fo": json.dumps(
                    {
                        "memo_recommendation_override": "Proceed",
                        "memo_recommendation_confirmed": True,
                    }
                ),
            },
        )
        await session.commit()
        _deal, _model, memo = await load_live_payload(session, deal_id, tenant_id)

    # Confirmed: the label is the verdict and nothing is being refused.
    assert memo["header"]["recommendation"] == "Proceed"
    assert memo["header"]["recommendation_reason"] is None

    # Pending: the canonical string, plus the code that explains it.
    _d, _m, pending = await _bare_payload()
    assert pending["header"]["recommendation"] == "Pending analyst decision"
    assert pending["header"]["recommendation_reason"] == "awaiting_analyst"


# ═════════════════ 3. the Excel "Refusals" sheet ═════════════════


@pytest.mark.asyncio
async def test_excel_refusals_sheet_lists_what_the_payload_refused(
    tmp_path: Path,
) -> None:
    from openpyxl import load_workbook

    from app.export import build_excel

    deal_id, (_deal, model, _memo) = await _modeled_payload()
    assert model["refusals"], "expected the modeled deal to refuse something"

    out = build_excel(deal_id, model, tmp_path / "live.xlsx")
    wb = load_workbook(out, read_only=True)
    assert "Refusals" in wb.sheetnames
    ws = wb["Refusals"]
    rows = [[c.value for c in row] for row in ws.iter_rows(min_row=1, max_col=4)]
    wb.close()

    assert rows[1] == ["Concept", "Where it appeared", "Reason code", "Meaning"]
    listed = {(r[0], r[2]) for r in rows[2:] if r[2]}

    from fondok_schemas.reasons import REASON_META, ReasonCode

    for r in model["refusals"]:
        assert (r.concept or "", r.code.value) in listed, (
            f"{r.concept}/{r.code.value} refused by the payload but absent "
            f"from the Refusals sheet"
        )
    # The Meaning column is REASON_META's label, not a re-typed string.
    for row in rows[2:]:
        if row[2]:
            assert row[3] == REASON_META[ReasonCode(row[2])]["label"]


@pytest.mark.asyncio
async def test_no_refusals_sheet_when_the_payload_refused_nothing(
    tmp_path: Path,
) -> None:
    """The Kimpton fixture workbook keeps exactly the sheets it had."""
    from openpyxl import load_workbook

    from app.export import build_excel
    from app.export.fixtures import load_demo_payload

    _deal, model, _memo = load_demo_payload("kimpton-angler-2026")
    assert "refusals" not in model

    out = build_excel("kimpton-angler-2026", model, tmp_path / "demo.xlsx")
    wb = load_workbook(out, read_only=True)
    names = wb.sheetnames
    wb.close()
    assert "Refusals" not in names
    assert len(names) == 19, f"fixture workbook sheet count moved: {names}"


# ═════════════════ 4. the memo PDF footnote ═════════════════


def test_memo_pdf_footnote_lists_the_distinct_reasons_it_dashed_for() -> None:
    from fondok_schemas.reasons import REASON_META, ReasonCode

    from app.export.memo_pdf import _render_html, _render_html_body

    memo = {
        "header": {"title": "Investment Committee Memorandum",
                   "subject_property": "Harbor House", "location": "Tampa, FL",
                   "recommendation": "Pending analyst decision"},
        "sections": [],
        "appendix": {"documents_reviewed": [], "engines_run": []},
    }
    model: dict = {"deal_name": "Harbor House"}

    body = _render_html_body(memo, model)
    full = _render_html(memo, model)

    assert 'id="refusal-footnote"' not in body, "the body itself must not change"
    assert full.startswith(body[:200])
    assert 'id="refusal-footnote"' in full
    assert "Why some figures show a dash" in full

    # Every code the render used is explained, once, from REASON_META.
    for code in (ReasonCode.NO_SOURCE, ReasonCode.ENGINE_SKIPPED,
                 ReasonCode.NO_DOCUMENT):
        label = REASON_META[code]["label"]
        assert full.count(f"<code>{code.value}</code>") == 1, code
        assert label in full


def test_memo_pdf_with_nothing_to_refuse_gains_no_footnote() -> None:
    """The complete Kimpton memo renders byte-identically to before 4.2.

    It grounds every figure, so it writes no dash and earns no footnote —
    the golden memo export is untouched by this phase.
    """
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html, _render_html_body

    memo, model = kimpton_memo(), kimpton_model()
    assert _render_html(memo, model) == _render_html_body(memo, model)
    assert "refusal-footnote" not in _render_html(memo, model)


# ═════════════════ 5. the helper itself ═════════════════


def test_refuse_returns_the_glyph_with_and_without_a_collector() -> None:
    from fondok_schemas.reasons import REFUSAL_GLYPH, ReasonCode

    from app.export.refusals import collect_refusals, refuse

    # Outside a collector it is a pure function — importing the module can
    # never change what a builder renders.
    assert refuse(ReasonCode.NO_SOURCE, "d", "c") == REFUSAL_GLYPH

    with collect_refusals() as log:
        assert refuse(ReasonCode.NO_DOCUMENT, "no T-12", "noi") == REFUSAL_GLYPH
        assert refuse(ReasonCode.NO_DOCUMENT, "no T-12", "noi") == REFUSAL_GLYPH
    assert len(log) == 2
    assert log[0].code is ReasonCode.NO_DOCUMENT
    assert log[0].concept == "noi"

    # And it stops recording once the block closes.
    assert refuse(ReasonCode.STALE_RUN) == REFUSAL_GLYPH
    assert len(log) == 2


def test_dedupe_and_distinct_codes_read_models_and_dicts_alike() -> None:
    from fondok_schemas.reasons import ReasonCode, Refusal

    from app.export.refusals import dedupe, distinct_codes

    items = [
        Refusal(code=ReasonCode.NO_SOURCE, detail="a", concept="x"),
        {"code": "no_source", "detail": "a", "concept": "x"},
        Refusal(code=ReasonCode.UNIT_UNKNOWN, detail="b", concept="y"),
    ]
    assert len(dedupe(items)) == 2
    assert distinct_codes(items) == [ReasonCode.NO_SOURCE, ReasonCode.UNIT_UNKNOWN]
