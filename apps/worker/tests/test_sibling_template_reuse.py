"""Sibling-template extraction reuse (TASK T2) — regression suite.

Covers, per the task quality gate:

* fingerprint groups the real Angler's sibling workbooks and separates
  non-siblings (real files gated behind ``@slow`` + a path skipif; the
  two golden fixtures — different templates — must NOT match)
* provenance recovery finds ≥ 60% of fields on a synthetic grid
* ambiguous values get dropped, not guessed (consensus-anchor conflict
  at apply time; promiscuous values dropped at learn time)
* label-anchored lookup survives an inserted row
* coverage below 70% falls back
* USALI identity-check failure falls back
* flag-off passthrough (dispatch never consults the mapping)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

# Force SQLite + a temp DB before app imports (same pattern as
# test_documents.py) — keeps tests isolated from any local fondok.db.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-sibling-template.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

from app.services.sibling_template import (  # noqa: E402
    COVERAGE_FLOOR,
    apply_mapping,
    compute_template_fingerprint,
    learn_mapping,
    passes_gates,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_REAL_DIR = Path(
    "/Users/prem/fondok/FL Miami South Beach Anglers (Eshan)/"
    "1 - Financials/Detailed Financials"
)


# ─────────────────────────── helpers ───────────────────────────


def _page(
    grid: list[list[str]],
    *,
    sheet_name: str = "P&L",
    page_num: int = 1,
    sheet_state: str = "visible",
) -> dict[str, Any]:
    return {
        "page_num": page_num,
        "text": "\n".join("\t".join(r) for r in grid),
        "tables": [grid],
        "metadata": {
            "source": "xls",
            "sheet_name": sheet_name,
            "sheet_state": sheet_state,
        },
    }


def _pnl_grid() -> list[list[str]]:
    """A small single-sheet P&L: unique values, month columns."""
    return [
        ["Anglers Hotel", "", "", "", ""],
        ["", "Jan", "Feb", "Mar", "Total"],
        ["Revenues", "", "", "", ""],
        ["Rooms", "101000", "102000", "103000", "306000"],
        ["Food & Beverage", "51000", "52000", "53000", "156000"],
        ["Other Operated", "11000", "12000", "13000", "36000"],
        ["Total Revenues", "163000", "166000", "169000", "498000"],
        ["Departmental Expenses", "", "", "", ""],
        ["Rooms", "31000", "32000", "33000", "96000"],
        ["Food & Beverage", "41000", "42000", "43000", "126000"],
        ["Statistics", "", "", "", ""],
        ["Occupancy", "0.81", "0.83", "0.85", "0.83"],
        ["ADR", "241.5", "243.5", "245.5", "243.5"],
    ]


def _extraction_fields() -> list[dict[str, Any]]:
    """What an LLM extraction of ``_pnl_grid`` plausibly looks like."""
    return [
        {"field_name": "property_overview.name", "value": "Anglers Hotel"},
        {"field_name": "p_and_l_usali.revenues.rooms_usd.jan", "value": 101000},
        {"field_name": "p_and_l_usali.revenues.rooms_usd.feb", "value": 102000},
        {"field_name": "p_and_l_usali.revenues.rooms_usd.mar", "value": 103000},
        {"field_name": "p_and_l_usali.revenues.rooms_usd", "value": 306000},
        {"field_name": "p_and_l_usali.revenues.fb_usd", "value": 156000},
        {"field_name": "p_and_l_usali.revenues.other_operated_usd", "value": 36000},
        {"field_name": "p_and_l_usali.revenues.total_revenues_usd", "value": 498000},
        {"field_name": "p_and_l_usali.departmental_expense.rooms_usd", "value": 96000},
        {"field_name": "p_and_l_usali.departmental_expense.fb_usd", "value": 126000},
        {"field_name": "ttm_summary_per_om.occupancy_pct", "value": 0.83},
        {"field_name": "ttm_summary_per_om.adr_usd", "value": 243.5},
    ]


def _sibling_grid() -> list[list[str]]:
    """Same template, next fiscal year: same labels, new numbers."""
    return [
        ["Anglers Hotel", "", "", "", ""],
        ["", "Jan", "Feb", "Mar", "Total"],
        ["Revenues", "", "", "", ""],
        ["Rooms", "111000", "112000", "113000", "336000"],
        ["Food & Beverage", "61000", "62000", "63000", "186000"],
        ["Other Operated", "21000", "22000", "23000", "66000"],
        ["Total Revenues", "193000", "196000", "199000", "588000"],
        ["Departmental Expenses", "", "", "", ""],
        ["Rooms", "35000", "36000", "37000", "108000"],
        ["Food & Beverage", "45000", "46000", "47000", "138000"],
        ["Statistics", "", "", "", ""],
        ["Occupancy", "0.84", "0.86", "0.88", "0.86"],
        ["ADR", "251.5", "253.5", "255.5", "253.5"],
    ]


# ─────────────────────────── fingerprint ───────────────────────────


def test_fingerprint_none_for_non_workbook_parser() -> None:
    pages = [_page(_pnl_grid())]
    assert compute_template_fingerprint(pages, parser="pymupdf") is None
    assert compute_template_fingerprint(pages, parser="llamaparse") is None


def test_fingerprint_stable_across_numbers_and_years() -> None:
    """Same sheets + different numbers ⇒ same fingerprint; digit-bearing
    sheet names ("FY2022" vs "FY2023") must not break grouping."""
    a = [_page(_pnl_grid(), sheet_name="FY2022 P&L")]
    b = [_page(_sibling_grid(), sheet_name="FY2023 P&L")]
    fp_a = compute_template_fingerprint(a, parser="openpyxl")
    fp_b = compute_template_fingerprint(b, parser="openpyxl")
    assert fp_a is not None and fp_a.startswith("tplv1:")
    assert fp_a == fp_b


def test_fingerprint_ignores_hidden_sheets() -> None:
    base = [_page(_pnl_grid(), sheet_name="Summary")]
    with_hidden = [
        _page(_pnl_grid(), sheet_name="Summary"),
        _page(
            [["macro junk", "1", "2"]],
            sheet_name="LDXz8fdsAE2Ug4AWCUzTAA==",
            page_num=2,
            sheet_state="veryHidden",
        ),
    ]
    assert compute_template_fingerprint(
        base, parser="openpyxl"
    ) == compute_template_fingerprint(with_hidden, parser="openpyxl")


def test_fingerprint_differs_for_different_sheet_sets() -> None:
    a = [_page(_pnl_grid(), sheet_name="Summary")]
    b = [
        _page(_pnl_grid(), sheet_name="Summary"),
        _page(_pnl_grid(), sheet_name="Rooms", page_num=2),
    ]
    assert compute_template_fingerprint(
        a, parser="openpyxl"
    ) != compute_template_fingerprint(b, parser="openpyxl")


def test_fingerprint_golden_fixtures_are_not_siblings() -> None:
    """sam_anglers_t12.xlsx and sam_anglers_2023_pnl.xlsx are different
    templates — their fingerprints must NOT match."""
    from app.extraction.parser import _parse_with_openpyxl

    fps = {}
    for name in ("sam_anglers_t12.xlsx", "sam_anglers_2023_pnl.xlsx"):
        path = _FIXTURES / name
        if not path.exists():
            pytest.skip(f"{name} fixture not present")
        parsed = _parse_with_openpyxl(
            file_bytes=path.read_bytes(), filename=name, content_hash="0" * 64
        )
        fps[name] = compute_template_fingerprint(
            parsed.pages, parser=parsed.parser
        )
    vals = list(fps.values())
    assert vals[0] is not None and vals[1] is not None
    assert vals[0] != vals[1]


# ─────────────────────────── provenance recovery ───────────────────────────


def test_provenance_recovery_covers_at_least_60_percent() -> None:
    pages = [_page(_pnl_grid())]
    entries, stats = learn_mapping(pages, _extraction_fields())
    assert stats["numeric"] == 11  # property name is non-numeric
    assert stats["matched"] / stats["numeric"] >= 0.60
    # the headline fields must be anchored
    assert "p_and_l_usali.revenues.total_revenues_usd" in entries
    assert "p_and_l_usali.departmental_expense.fb_usd" in entries


def test_provenance_anchors_are_labels_not_coordinates() -> None:
    pages = [_page(_pnl_grid())]
    entries, _ = learn_mapping(pages, _extraction_fields())
    key = entries["p_and_l_usali.revenues.rooms_usd.jan"]["keys"][0]
    # (sheet, section, row_label, col_header) — all normalized text
    assert key[0] == "p&l"
    assert key[1] == "revenues"
    assert key[2] == "rooms"
    assert "jan" in key[3]


def test_provenance_disambiguates_by_field_name_tokens() -> None:
    """The same value under 'Food & Beverage' and 'Parking' must anchor
    an fb_* field to the F&B row only."""
    grid = [
        ["", "Total"],
        ["Revenues", ""],
        ["Food & Beverage", "77000"],
        ["Parking", "77000"],
    ]
    entries, stats = learn_mapping(
        [_page(grid)],
        [{"field_name": "p_and_l_usali.revenues.fb_usd", "value": 77000}],
    )
    assert stats["matched"] == 1
    keys = entries["p_and_l_usali.revenues.fb_usd"]["keys"]
    assert len(keys) == 1
    assert keys[0][2] == "food & beverage"


def test_promiscuous_values_dropped_at_learn_time() -> None:
    """A value matching dozens of cells is unanchorable — dropped."""
    labels = [f"{a} {b}" for a in "qwertyuiop" for b in ("cost", "fee", "levy")]
    grid = [["", "Total"]] + [[lb, "42"] for lb in labels]
    entries, stats = learn_mapping(
        [_page(grid)],
        [{"field_name": "p_and_l_usali.some_field_usd", "value": 42}],
    )
    assert entries == {}
    assert stats["ambiguous"] == 1


def test_zero_values_never_anchored() -> None:
    entries, _stats = learn_mapping(
        [_page([["Rooms", "0"]])],
        [{"field_name": "p_and_l_usali.revenues.rooms_usd", "value": 0}],
    )
    assert entries == {}


def test_unit_scaled_sheets_match_via_thousands() -> None:
    """Sheet shows 306 ('in $000s'), the LLM extracted 306000."""
    grid = [
        ["", "Total"],
        ["Revenues", ""],
        ["Rooms", "306"],
    ]
    entries, stats = learn_mapping(
        [_page(grid)],
        [{"field_name": "p_and_l_usali.revenues.rooms_usd", "value": 306000}],
    )
    assert stats["matched"] == 1
    entry = entries["p_and_l_usali.revenues.rooms_usd"]
    assert entry["scale"] == 1000.0
    fields, _ = apply_mapping([_page(grid)], entries)
    assert fields[0]["value"] == 306000


# ─────────────────────────── sibling application ───────────────────────────


def test_apply_mapping_transfers_new_values() -> None:
    entries, _ = learn_mapping([_page(_pnl_grid())], _extraction_fields())
    fields, stats = apply_mapping([_page(_sibling_grid())], entries)
    got = {f["field_name"]: f["value"] for f in fields}
    assert got["p_and_l_usali.revenues.total_revenues_usd"] == 588000
    assert got["p_and_l_usali.revenues.rooms_usd.jan"] == 111000
    assert got["p_and_l_usali.departmental_expense.rooms_usd"] == 108000
    assert stats["found"] >= 0.9 * len(entries)
    for f in fields:
        assert f["confidence"] == 0.95


def test_label_anchored_lookup_survives_inserted_row() -> None:
    """A row inserted mid-sheet shifts coordinates but not labels."""
    entries, _ = learn_mapping([_page(_pnl_grid())], _extraction_fields())
    shifted = _sibling_grid()
    # insert a brand-new line item above the F&B revenue row
    shifted.insert(4, ["Spa & Wellness", "9100", "9200", "9300", "27600"])
    fields, _ = apply_mapping([_page(shifted)], entries)
    got = {f["field_name"]: f["value"] for f in fields}
    assert got["p_and_l_usali.revenues.fb_usd"] == 186000
    assert got["p_and_l_usali.revenues.total_revenues_usd"] == 588000


def test_ambiguous_anchor_conflict_dropped_not_guessed() -> None:
    """A value that matched two rows at learn time (kept as consensus
    anchors) must be DROPPED when the sibling's two rows disagree."""
    source = [
        ["", "Total"],
        ["Revenues", ""],
        ["Alpha", "5000"],
        ["Beta", "5000"],  # same value, no token match for the field
    ]
    entries, stats = learn_mapping(
        [_page(source)],
        [{"field_name": "p_and_l_usali.misc_income_usd", "value": 5000}],
    )
    assert stats["matched"] == 1  # kept, with BOTH anchors
    assert len(entries["p_and_l_usali.misc_income_usd"]["keys"]) == 2
    sibling = [
        ["", "Total"],
        ["Revenues", ""],
        ["Alpha", "6000"],
        ["Beta", "7000"],  # anchors now disagree
    ]
    fields, apply_stats = apply_mapping([_page(sibling)], entries)
    assert fields == []
    assert apply_stats["conflict"] == 1


# ─────────────────────────── verification gates ───────────────────────────


def _mapping_of_size(n: int) -> dict[str, Any]:
    return {
        f"f{i}": {"keys": [["p&l", "", f"row {i}", "total"]], "scale": 1.0}
        for i in range(n)
    }


def test_gate_rejects_coverage_below_floor() -> None:
    ok, reason = passes_gates(
        entries=_mapping_of_size(10),
        applied_fields=[],
        apply_stats={"found": 6, "conflict": 2, "absent": 2},
        source_usali_score=None,
    )
    assert not ok
    assert "coverage" in reason


def test_gate_accepts_coverage_at_floor_without_source_score() -> None:
    ok, _ = passes_gates(
        entries=_mapping_of_size(10),
        applied_fields=[],
        apply_stats={"found": int(10 * COVERAGE_FLOOR)},
        source_usali_score=None,
    )
    assert ok


def test_gate_rejects_usali_identity_drop(monkeypatch) -> None:
    """Sibling scoring >15 points below the source doc ⇒ fall back."""
    import app.services.sibling_template as st

    monkeypatch.setattr(st, "_usali_score_for", lambda fields: 60.0)
    ok, reason = passes_gates(
        entries=_mapping_of_size(10),
        applied_fields=[{"field_name": "f0", "value": 1.0}],
        apply_stats={"found": 10},
        source_usali_score=90.0,
    )
    assert not ok
    assert "USALI" in reason

    monkeypatch.setattr(st, "_usali_score_for", lambda fields: 80.0)
    ok, _ = passes_gates(
        entries=_mapping_of_size(10),
        applied_fields=[{"field_name": "f0", "value": 1.0}],
        apply_stats={"found": 10},
        source_usali_score=90.0,
    )
    assert ok


def test_gate_rejects_inconclusive_sibling_when_source_scored(monkeypatch) -> None:
    import app.services.sibling_template as st

    monkeypatch.setattr(st, "_usali_score_for", lambda fields: None)
    ok, _ = passes_gates(
        entries=_mapping_of_size(10),
        applied_fields=[],
        apply_stats={"found": 10},
        source_usali_score=90.0,
    )
    assert not ok


# ─────────────────────────── DB round-trip + dispatch wiring ───────────────────────────


async def _seed_doc(
    session,
    *,
    tenant_id: str,
    deal_id: str,
    grid: list[list[str]],
    fingerprint: str | None,
    content_hash: str,
) -> str:
    from sqlalchemy import text

    doc_id = str(uuid4())
    extraction_data = {
        "parser": "openpyxl",
        "total_pages": 1,
        "content_hash": content_hash,
        "pages": [_page(grid)],
    }
    await session.execute(
        text(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, "
            "doc_type, status, uploaded_at, content_hash, storage_key, "
            "parser, template_fingerprint, extraction_data) "
            "VALUES (:id, :deal, :tenant, :fn, 'T12', 'UPLOADED', "
            ":ts, :ch, :sk, 'openpyxl', :tfp, :data)"
        ),
        {
            "id": doc_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "fn": f"{doc_id}.xlsx",
            "ts": "2026-07-01 00:00:00",
            "ch": content_hash,
            "sk": f"raw/{content_hash}",
            "tfp": fingerprint,
            "data": json.dumps(extraction_data),
        },
    )
    await session.commit()
    return doc_id


@pytest.fixture
async def db_env():
    """Migrated sqlite DB + a stub deal row; yields (factory, ids)."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import dispose_engine, get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    settings = get_settings()
    tenant_id = str(settings.DEFAULT_TENANT_ID)
    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, "
                "created_at, updated_at) "
                "VALUES (:id, :tenant, 'Sibling Test Hotel', 'Draft', "
                ":ts, :ts)"
            ),
            {"id": deal_id, "tenant": tenant_id, "ts": "2026-07-01 00:00:00"},
        )
        await session.commit()
    yield factory, tenant_id, deal_id
    await dispose_engine()


async def test_learn_persists_and_sibling_reuses(db_env) -> None:
    """maybe_learn_mapping → template_mappings row → try_sibling_reuse
    HIT on a same-fingerprint sibling with the SAME field names (the
    schema-drift kill shot) and values from the sibling's own grid."""
    from sqlalchemy import text

    from app.services.sibling_template import (
        maybe_learn_mapping,
        try_sibling_reuse,
    )

    factory, tenant_id, deal_id = db_env
    fp = compute_template_fingerprint([_page(_pnl_grid())], parser="openpyxl")
    async with factory() as session:
        src_doc = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=_pnl_grid(),
            fingerprint=fp,
            content_hash="a" * 64,
        )
        await maybe_learn_mapping(
            session,
            tenant_id=tenant_id,
            doc_id=src_doc,
            doc_type="T12",
            fields=_extraction_fields(),
            extraction_data={"pages": [_page(_pnl_grid())]},
        )
        row = (
            await session.execute(
                text(
                    "SELECT mapping_json FROM template_mappings "
                    "WHERE tenant_id = :t AND fingerprint = :fp"
                ),
                {"t": tenant_id, "fp": fp},
            )
        ).first()
        assert row is not None, "mapping was not persisted"
        mapping_json = json.loads(row._mapping["mapping_json"])
        assert mapping_json["source_doc_id"] == src_doc
        assert len(mapping_json["entries"]) >= 7

        sib_doc = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=_sibling_grid(),
            fingerprint=fp,
            content_hash="b" * 64,
        )
        hit = await try_sibling_reuse(
            session,
            tenant_id=tenant_id,
            doc_id=sib_doc,
            extraction_data={"pages": [_page(_sibling_grid())]},
        )
        assert hit is not None, "sibling reuse should HIT"
        fields, confidence, source_doc_type = hit
        assert source_doc_type == "T12"
        got = {f["field_name"]: f["value"] for f in fields}
        # exact source schema paths — no drift — with SIBLING values
        assert got["p_and_l_usali.revenues.total_revenues_usd"] == 588000
        assert got["p_and_l_usali.revenues.fb_usd"] == 186000
        assert confidence["overall"] == 0.95

        # self-reuse guard: the source doc must NOT reuse its own mapping
        assert (
            await try_sibling_reuse(
                session,
                tenant_id=tenant_id,
                doc_id=src_doc,
                extraction_data={"pages": [_page(_pnl_grid())]},
            )
            is None
        )


async def test_sibling_reuse_falls_back_on_low_coverage(db_env) -> None:
    """A different-template workbook that somehow shares the fingerprint
    must fail the coverage gate and return None (LLM fallback)."""
    from app.services.sibling_template import (
        maybe_learn_mapping,
        try_sibling_reuse,
    )

    factory, tenant_id, deal_id = db_env
    fp = compute_template_fingerprint([_page(_pnl_grid())], parser="openpyxl")
    unrelated_grid = [
        ["Completely Different Report", ""],
        ["Widget Sales", "123"],
        ["Gadget Sales", "456"],
    ]
    async with factory() as session:
        src_doc = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=_pnl_grid(),
            fingerprint=fp,
            content_hash="c" * 64,
        )
        await maybe_learn_mapping(
            session,
            tenant_id=tenant_id,
            doc_id=src_doc,
            doc_type="T12",
            fields=_extraction_fields(),
            extraction_data={"pages": [_page(_pnl_grid())]},
        )
        impostor = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=unrelated_grid,
            fingerprint=fp,  # forced collision
            content_hash="d" * 64,
        )
        assert (
            await try_sibling_reuse(
                session,
                tenant_id=tenant_id,
                doc_id=impostor,
                extraction_data={"pages": [_page(unrelated_grid)]},
            )
            is None
        )


async def test_dispatch_flag_off_passthrough_and_flag_on_hit(
    db_env, monkeypatch
) -> None:
    """End-to-end dispatch wiring: with SIBLING_TEMPLATE_REUSE_ENABLED
    False the pipeline must run the (stubbed) LLM even though a mapping
    exists; with True it must skip the LLM and persist
    ``template:sibling:v1;pv=v1``."""
    from sqlalchemy import text

    import app.api.documents as docs_api
    from app.services.sibling_template import maybe_learn_mapping

    factory, tenant_id, deal_id = db_env
    monkeypatch.setenv("EVALS_MOCK", "false")

    fp = compute_template_fingerprint([_page(_pnl_grid())], parser="openpyxl")
    async with factory() as session:
        src_doc = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=_pnl_grid(),
            fingerprint=fp,
            content_hash="e" * 64,
        )
        await maybe_learn_mapping(
            session,
            tenant_id=tenant_id,
            doc_id=src_doc,
            doc_type="T12",
            fields=_extraction_fields(),
            extraction_data={"pages": [_page(_pnl_grid())]},
        )
        sib_off = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=_sibling_grid(),
            fingerprint=fp,
            content_hash="f" * 64,
        )
        sib_on = await _seed_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            grid=_sibling_grid(),
            fingerprint=fp,
            content_hash="0" * 63 + "1",
        )

    llm_calls: list[str] = []

    async def _fake_graph_extraction(**kwargs):
        llm_calls.append(kwargs["doc_id"])
        return (
            [{"field_name": "p_and_l_usali.revenues.rooms_usd", "value": 1.0}],
            {"overall": 0.9, "by_field": {}, "low_confidence_fields": [],
             "requires_human_review": False},
            "router:T12;extractor",
            "T12",
        )

    monkeypatch.setattr(
        docs_api, "_run_graph_extraction", _fake_graph_extraction
    )
    # content-hash cache off so the stubbed rows can't satisfy a
    # cache hit for one another (they have distinct hashes anyway).
    real_settings = docs_api.get_settings()

    # ── flag OFF: LLM (stub) must run, no sibling agent_version ──
    monkeypatch.setattr(
        docs_api,
        "get_settings",
        lambda: real_settings.model_copy(
            update={"SIBLING_TEMPLATE_REUSE_ENABLED": False}
        ),
    )
    await docs_api._run_extraction_pipeline_inner(
        deal_id=deal_id, doc_id=sib_off, tenant_id=tenant_id
    )
    assert sib_off in llm_calls, "flag off must pass through to the LLM"
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT agent_version FROM extraction_results "
                    "WHERE document_id = :d AND tenant_id = :t"
                ),
                {"d": sib_off, "t": tenant_id},
            )
        ).first()
    assert row is not None
    assert not row._mapping["agent_version"].startswith("template:sibling")

    # ── flag ON: sibling HIT, zero (stub-)LLM calls ──
    monkeypatch.setattr(docs_api, "get_settings", lambda: real_settings)
    assert real_settings.SIBLING_TEMPLATE_REUSE_ENABLED is True
    await docs_api._run_extraction_pipeline_inner(
        deal_id=deal_id, doc_id=sib_on, tenant_id=tenant_id
    )
    assert sib_on not in llm_calls, "sibling HIT must skip the LLM"
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT agent_version, fields FROM extraction_results "
                    "WHERE document_id = :d AND tenant_id = :t"
                ),
                {"d": sib_on, "t": tenant_id},
            )
        ).first()
    assert row is not None
    assert row._mapping["agent_version"] == "template:sibling:v1;pv=v1"
    fields = json.loads(row._mapping["fields"])
    got = {f["field_name"]: f["value"] for f in fields}
    assert got["p_and_l_usali.revenues.total_revenues_usd"] == 588000


# ─────────────────────────── real Angler's siblings ───────────────────────────

_real_files_present = _REAL_DIR.exists() and (
    _REAL_DIR / "Angler_s 2022 Detailed P&L.xlsm"
).exists()


@pytest.mark.slow
@pytest.mark.skipif(
    not _real_files_present,
    reason="real Angler's data-room files not present on this machine",
)
class TestRealAnglersSiblings:
    """The exact sibling set this feature targets: ~49-sheet SAP BPC
    workbooks, 2019-2023. Parsing all five takes ~40s → @slow."""

    @pytest.fixture(scope="class")
    def parsed_years(self):
        from app.extraction.parser import _parse_with_openpyxl

        out = {}
        for year in (2019, 2020, 2021, 2022, 2023):
            path = _REAL_DIR / f"Angler_s {year} Detailed P&L.xlsm"
            out[year] = _parse_with_openpyxl(
                file_bytes=path.read_bytes(),
                filename=path.name,
                content_hash="0" * 64,
            )
        return out

    def test_fingerprint_groups_real_siblings(self, parsed_years) -> None:
        fps = {
            y: compute_template_fingerprint(d.pages, parser=d.parser)
            for y, d in parsed_years.items()
        }
        # 2020-2023 share the 49-sheet template (2023 adds only
        # veryHidden macro sheets, which the fingerprint ignores).
        assert len({fps[y] for y in (2020, 2021, 2022, 2023)}) == 1
        # 2019 is a genuinely different template revision (no Venue5/6,
        # single BCC sheet) — grouping it would be wrong.
        assert fps[2019] != fps[2020]

    def test_fingerprint_separates_real_siblings_from_fixtures(
        self, parsed_years
    ) -> None:
        from app.extraction.parser import _parse_with_openpyxl

        real_fp = compute_template_fingerprint(
            parsed_years[2022].pages, parser="openpyxl"
        )
        for name in ("sam_anglers_t12.xlsx", "sam_anglers_2023_pnl.xlsx"):
            parsed = _parse_with_openpyxl(
                file_bytes=(_FIXTURES / name).read_bytes(),
                filename=name,
                content_hash="0" * 64,
            )
            assert (
                compute_template_fingerprint(parsed.pages, parser="openpyxl")
                != real_fp
            )

    def test_mapping_transfers_across_real_years(self, parsed_years) -> None:
        """Learn from 2022, locate fields in 2021 — the coverage gate
        must clear and every consensus value must be label-consistent."""
        from app.services.sibling_template import _build_key_index

        # simulate an LLM extraction of the 2022 Summary sheet by
        # reading canonical line items via their labels
        wanted = [
            ("p_and_l_usali.revenues.rooms_usd", "revenues", "rooms"),
            ("p_and_l_usali.revenues.fb_usd", "revenues", "food & beverage"),
            ("p_and_l_usali.revenues.total_revenues_usd", "", "total revenue"),
            (
                "p_and_l_usali.departmental_expense.rooms_usd",
                "departmental expense",
                "rooms",
            ),
            (
                "p_and_l_usali.departmental_expense.fb_usd",
                "departmental expense",
                "food & beverage",
            ),
            ("ttm_summary_per_om.adr_usd", "", "average daily rate"),
            ("ttm_summary_per_om.occupancy_pct", "", "total occupancy"),
        ]
        months = [
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ]

        def label_read(parsed) -> dict[str, float]:
            idx = _build_key_index(parsed.pages)
            out: dict[str, float] = {}
            for fname, section, label in wanted:
                for mon in [*months, "total"]:
                    for key, slot in idx.items():
                        sheet, sec, rl, ch = key
                        if sheet != "summary" or len(slot["values"]) != 1:
                            continue
                        if section and sec != section:
                            continue
                        if rl != label:
                            continue
                        parts = ch.split("|")
                        if "actual" not in parts or mon not in parts:
                            continue
                        suffix = f".{mon}" if mon != "total" else ""
                        out[fname + suffix] = next(iter(slot["values"]))
                        break
            return out

        source_fields = [
            {"field_name": k, "value": v}
            for k, v in label_read(parsed_years[2022]).items()
        ]
        assert len(source_fields) >= 60
        entries, stats = learn_mapping(parsed_years[2022].pages, source_fields)
        # scope-guardrail number: ≥50% provenance coverage on the REAL files
        assert stats["matched"] / stats["numeric"] >= 0.50

        fields, apply_stats = apply_mapping(parsed_years[2021].pages, entries)
        ok, reason = passes_gates(
            entries=entries,
            applied_fields=fields,
            apply_stats=apply_stats,
            source_usali_score=None,
        )
        assert ok, f"real sibling transfer failed the gate: {reason}"
        # zero wrong values vs an independent label-driven read of 2021
        truth = label_read(parsed_years[2021])
        got = {f["field_name"]: f["value"] for f in fields}
        checked = 0
        for name in set(got) & set(truth):
            assert abs(got[name] - truth[name]) <= 0.01, name
            checked += 1
        assert checked >= 0.7 * len(entries)
