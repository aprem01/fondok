"""FON-109 — CBRE / HotStats P&L benchmarks are suggestions, not engine input.

Sam's decision (2026-10-15): "implement the change before this re-test so
CBRE/HotStats benchmarks are suggestions that require analyst approval rather
than automatically flowing into the model."

Covered here:

* a PNL_BENCHMARK extraction no longer moves ``rooms_dept_pct`` /
  ``fb_dept_pct`` / ``mgmt_fee_pct`` / ``ffe_reserve_pct`` — the values fall
  through to the seed / hotel-type default (or the portfolio P&L);
* ``GET /deals/{id}/benchmark-suggestions`` names, per assumption, the
  suggested value, the benchmark line, the peer-set size, the document + page,
  and the value in use with its source; tenant-scoped (cross-tenant → 404);
* applying a suggestion is a ``PATCH`` of ``field_overrides`` with the
  prefilled note: the model then runs on it, badged ``analyst_override``, and
  the endpoint reports it applied; without a note the PATCH is refused;
* precedence is otherwise unchanged (portfolio P&L > seed; analyst > all).

Real per-test SQLite DB; no LLM.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-benchmark-suggestions-fon109.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-00000000f109"
_OTHER_TENANT = "00000000-0000-0000-0000-00000000f10a"
_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _insert_deal(
    deal_id: UUID,
    *,
    field_overrides: dict[str, object] | None = None,
    tenant: str = _TENANT,
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, field_overrides, created_at, updated_at
                ) VALUES (
                    :id, :tenant, 'FON-109 Hotel', 'Underwriting', 0.0, 132,
                    36400000, :fo, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": tenant,
                "fo": json.dumps(field_overrides or {}),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_doc(
    deal_id: UUID,
    *,
    doc_type: str,
    filename: str,
    fields: list[dict[str, object]],
    ts: datetime = _T0,
    tenant: str = _TENANT,
) -> UUID:
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count
                ) VALUES (
                    :id, :deal, :tenant, :filename, :doc_type, 'EXTRACTED', :ts, 1
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant,
                "filename": filename,
                "doc_type": doc_type,
                "ts": ts,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant,
                "fields": json.dumps(fields),
                "ts": ts,
            },
        )
        await session.commit()
    return doc_id


def _row(name: str, value: object, page: int = 1) -> dict[str, object]:
    return {"field_name": name, "value": value, "source_page": page, "confidence": 0.9}


#: A legacy-summary benchmark — exactly the four aliases the old loader applied.
_LEGACY_BENCHMARK = [
    _row("pnl_benchmark.peer_set_size", 14, page=2),
    _row("pnl_benchmark.rooms_dept_pct", 0.22, page=7),
    _row("pnl_benchmark.fb_dept_margin", 0.31, page=8),
    _row("pnl_benchmark.mgmt_fee_pct", 2.5, page=9),  # percent units → 0.025
    _row("pnl_benchmark.ffe_reserve_pct", 0.05, page=9),
]


async def _base_for(deal_id: UUID, tenant: str = _TENANT) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        return await _load_engine_inputs(session, str(deal_id), tenant_id=tenant)


async def _call(method: str, path: str, tenant: str = _TENANT, json_body: object = None) -> tuple[int, dict]:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.request(method, path, headers={"X-Tenant-Id": tenant}, json=json_body)
    return r.status_code, (r.json() if r.content else {})


# ─────────────────────── the benchmark is not applied ───────────────────────


@pytest.mark.asyncio
async def test_benchmark_no_longer_flows_into_engine_input() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="CBRE Trends 2025.pdf",
                      fields=_LEGACY_BENCHMARK)
    base = await _base_for(deal_id)
    sources = base["__sources__"]

    # Management fee / FF&E reserve stay on the seed, labelled as such.
    assert base["mgmt_fee_pct"] == pytest.approx(0.03)
    assert base["ffe_reserve_pct"] == pytest.approx(0.04)
    assert sources["mgmt_fee_pct"] == "seed"
    assert sources["ffe_reserve_pct"] == "seed"
    assert "pnl_benchmark" not in sources.values()
    # The departmental ratios are not patched — the expense engine keeps its
    # hotel-type default.
    overrides = base.get("overrides") or {}
    assert "rooms_dept_pct" not in overrides
    assert "fb_dept_pct" not in overrides
    # A benchmark no longer grounds the fee, so the seed reason names the
    # one document type that does.
    reasons = base.get("__reasons__") or {}
    assert reasons["mgmt_fee_pct"]["code"].value == "no_document"
    assert "PORTFOLIO_PNL" in reasons["mgmt_fee_pct"]["detail"]


@pytest.mark.asyncio
async def test_engine_input_is_identical_with_and_without_a_benchmark() -> None:
    plain, with_bench = uuid4(), uuid4()
    await _insert_deal(plain)
    await _insert_deal(with_bench)
    await _insert_doc(with_bench, doc_type="PNL_BENCHMARK", filename="b.pdf", fields=_LEGACY_BENCHMARK)
    a = await _base_for(plain)
    b = await _base_for(with_bench)
    for key in ("mgmt_fee_pct", "ffe_reserve_pct", "overrides"):
        assert a.get(key) == b.get(key), key


# ─────────────────────────── suggestion endpoint ───────────────────────────


@pytest.mark.asyncio
async def test_suggestions_endpoint_names_value_line_peer_set_doc_page_and_current() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    doc_id = await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="CBRE Trends 2025.pdf",
                               fields=_LEGACY_BENCHMARK)
    code, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    assert code == 200, body
    assert body["peer_set_size"] == 14
    by_key = {s["key"]: s for s in body["suggestions"]}
    assert set(by_key) == {"rooms_dept_pct", "fb_dept_pct", "mgmt_fee_pct", "ffe_reserve_pct"}

    rooms = by_key["rooms_dept_pct"]
    assert rooms["suggested_value"] == pytest.approx(0.22)
    assert rooms["benchmark_line"] == "rooms_dept_expense"
    assert rooms["ratio_source"] == "legacy_summary"
    assert rooms["field_name"] == "pnl_benchmark.rooms_dept_pct"
    assert rooms["peer_set_size"] == 14
    assert rooms["document_id"] == str(doc_id)
    assert rooms["filename"] == "CBRE Trends 2025.pdf"
    assert rooms["page"] == 7
    assert rooms["current_value"] == pytest.approx(0.30)  # full-service default
    assert rooms["current_source"] == "seed"
    assert rooms["applied"] is False
    assert rooms["suggested_note"] == (
        "Applied CBRE benchmark Rooms Expense 22% from CBRE Trends 2025.pdf p.7"
    )

    fb = by_key["fb_dept_pct"]
    assert fb["suggested_value"] == pytest.approx(0.69)  # 1 − 0.31 margin
    assert fb["page"] == 8

    mgmt = by_key["mgmt_fee_pct"]
    assert mgmt["suggested_value"] == pytest.approx(0.025)
    assert mgmt["current_value"] == pytest.approx(0.03)
    assert mgmt["current_source"] == "seed"
    assert mgmt["suggested_note"] == (
        "Applied CBRE benchmark Management Fees 2.5% from CBRE Trends 2025.pdf p.9"
    )

    ffe = by_key["ffe_reserve_pct"]
    assert ffe["suggested_value"] == pytest.approx(0.05)
    assert ffe["current_value"] == pytest.approx(0.04)
    assert ffe["benchmark_line"] == "ffe_reserve_pct"


@pytest.mark.asyncio
async def test_suggestion_uses_the_per_line_ratio_the_benchmark_column_shows() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(
        deal_id, doc_type="PNL_BENCHMARK", filename="HotStats.xlsx",
        fields=[
            _row("pnl_benchmark.peer.rooms_revenue.total_usd", 10_000_000.0, page=3),
            _row("pnl_benchmark.peer.rooms_dept_expense.total_usd", 2_460_000.0, page=4),
            _row("pnl_benchmark.peer.total_revenue.total_usd", 16_000_000.0, page=3),
            _row("pnl_benchmark.peer.mgmt_fee.total_usd", 480_000.0, page=6),
            # A legacy alias that disagrees — the per-line total wins, like the column.
            _row("pnl_benchmark.rooms_dept_pct", 0.20, page=1),
        ],
    )
    code, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    assert code == 200, body
    by_key = {s["key"]: s for s in body["suggestions"]}
    assert by_key["rooms_dept_pct"]["suggested_value"] == pytest.approx(0.246)
    assert by_key["rooms_dept_pct"]["ratio_source"] == "computed_from_totals"
    assert by_key["rooms_dept_pct"]["field_name"] == "pnl_benchmark.peer.rooms_dept_expense.total_usd"
    assert by_key["rooms_dept_pct"]["page"] == 4
    assert by_key["mgmt_fee_pct"]["suggested_value"] == pytest.approx(0.03)
    assert by_key["mgmt_fee_pct"]["page"] == 6
    assert body["peer_set_size"] is None
    # Not published → not suggested (never filled).
    assert "ffe_reserve_pct" not in by_key
    assert "fb_dept_pct" not in by_key


@pytest.mark.asyncio
async def test_implausible_benchmark_values_are_not_suggested() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="b.pdf", fields=[
        _row("pnl_benchmark.mgmt_fee_pct", 0.25),  # 25% fee — outside (0, 10%)
        _row("pnl_benchmark.ffe_reserve_pct", 0.0),
    ])
    code, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    assert code == 200, body
    assert body["suggestions"] == []


@pytest.mark.asyncio
async def test_no_benchmark_means_no_suggestions() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    code, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    assert code == 200, body
    assert body["suggestions"] == [] and body["peer_set_size"] is None


@pytest.mark.asyncio
async def test_suggestions_endpoint_is_tenant_scoped() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="b.pdf", fields=_LEGACY_BENCHMARK)
    code, _ = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions", tenant=_OTHER_TENANT)
    assert code == 404


# ──────────────────────────── apply = override ────────────────────────────


@pytest.mark.asyncio
async def test_applying_a_suggestion_writes_an_override_with_its_note() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="CBRE Trends 2025.pdf",
                      fields=_LEGACY_BENCHMARK)
    _, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    by_key = {s["key"]: s for s in body["suggestions"]}

    # Without a note the PATCH is refused (FON-74) — nothing is applied silently.
    code, err = await _call("PATCH", f"/deals/{deal_id}", json_body={
        "field_overrides": {"mgmt_fee_pct": {"value": by_key["mgmt_fee_pct"]["suggested_value"]}},
    })
    assert code == 422, err

    overrides = {
        key: {"value": by_key[key]["suggested_value"], "note": by_key[key]["suggested_note"]}
        for key in ("mgmt_fee_pct", "rooms_dept_pct")
    }
    code, deal = await _call("PATCH", f"/deals/{deal_id}", json_body={"field_overrides": overrides})
    assert code == 200, deal

    base = await _base_for(deal_id)
    assert base["mgmt_fee_pct"] == pytest.approx(0.025)
    assert base["__sources__"]["mgmt_fee_pct"] == "analyst_override"
    assert base["overrides"]["rooms_dept_pct"] == pytest.approx(0.22)
    assert base["__sources__"]["rooms_dept_pct"] == "analyst_override"
    # The un-applied ones did not move.
    assert base["ffe_reserve_pct"] == pytest.approx(0.04)
    assert "fb_dept_pct" not in base["overrides"]

    _, after = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    after_by_key = {s["key"]: s for s in after["suggestions"]}
    mgmt = after_by_key["mgmt_fee_pct"]
    assert mgmt["applied"] is True
    assert mgmt["current_source"] == "analyst_override"
    assert mgmt["current_value"] == pytest.approx(0.025)
    assert mgmt["current_note"] == by_key["mgmt_fee_pct"]["suggested_note"]
    assert after_by_key["rooms_dept_pct"]["applied"] is True
    assert after_by_key["ffe_reserve_pct"]["applied"] is False


# ─────────────────────── precedence otherwise unchanged ───────────────────────


@pytest.mark.asyncio
async def test_portfolio_pnl_still_wins_over_the_seed_with_a_benchmark_present() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="b.pdf",
                      fields=_LEGACY_BENCHMARK, ts=_T0 + timedelta(days=1))
    await _insert_doc(deal_id, doc_type="PORTFOLIO_PNL", filename="Our portfolio.xlsx", fields=[
        _row("portfolio_pnl.management_fee_pct", 0.035),
        _row("portfolio_pnl.rooms_dept_pct", 0.26),
    ])
    base = await _base_for(deal_id)
    assert base["mgmt_fee_pct"] == pytest.approx(0.035)
    assert base["__sources__"]["mgmt_fee_pct"] == "portfolio_pnl"
    assert base["overrides"]["rooms_dept_pct"] == pytest.approx(0.26)

    _, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    by_key = {s["key"]: s for s in body["suggestions"]}
    assert by_key["mgmt_fee_pct"]["current_value"] == pytest.approx(0.035)
    assert by_key["mgmt_fee_pct"]["current_source"] == "portfolio_pnl"
    assert by_key["rooms_dept_pct"]["current_value"] == pytest.approx(0.26)
    assert by_key["rooms_dept_pct"]["current_source"] == "portfolio_pnl"


@pytest.mark.asyncio
async def test_analyst_override_still_beats_portfolio_pnl() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, field_overrides={"mgmt_fee_pct": {"value": 0.04, "note": "HMA term sheet"}})
    await _insert_doc(deal_id, doc_type="PORTFOLIO_PNL", filename="p.xlsx", fields=[
        _row("portfolio_pnl.management_fee_pct", 0.035),
    ])
    await _insert_doc(deal_id, doc_type="PNL_BENCHMARK", filename="b.pdf", fields=_LEGACY_BENCHMARK)
    base = await _base_for(deal_id)
    assert base["mgmt_fee_pct"] == pytest.approx(0.04)
    assert base["__sources__"]["mgmt_fee_pct"] == "analyst_override"
    _, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    mgmt = {s["key"]: s for s in body["suggestions"]}["mgmt_fee_pct"]
    assert mgmt["applied"] is False
    assert mgmt["current_note"] == "HMA term sheet"


def test_suggested_note_without_a_page_says_so() -> None:
    from app.services.benchmark_suggestions import format_pct, suggested_note

    assert format_pct(0.0325) == "3.25%"
    assert format_pct(0.246) == "24.6%"
    assert suggested_note(line_label="FF&E Reserve", value=0.04, filename=None, page=None) == (
        "Applied CBRE benchmark FF&E Reserve 4% from the uploaded P&L benchmark report (page not stated)"
    )


@pytest.mark.asyncio
async def test_ffe_reserve_is_suggested_from_the_per_line_ratio() -> None:
    """FON-109 follow-up (Sam QA 2026-10-28): the CBRE/HotStats CSV states the
    FF&E reserve as ``pnl_benchmark.peer.ffe_reserve.ratio_pct``; only the
    legacy ``pnl_benchmark.ffe_reserve_pct`` alias was read, so the Future
    P&L showed no suggestion and no Apply button for FF&E while management
    fee (mapped per line) worked."""
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(
        deal_id, doc_type="PNL_BENCHMARK", filename="CBRE_HotStats.csv",
        fields=[
            _row("pnl_benchmark.peer.mgmt_fee.ratio_pct", 3.0, page=1),
            _row("pnl_benchmark.peer.ffe_reserve.ratio_pct", 4.5, page=1),
        ],
    )
    code, body = await _call("GET", f"/deals/{deal_id}/benchmark-suggestions")
    assert code == 200, body
    by_key = {s["key"]: s for s in body["suggestions"]}
    assert by_key["mgmt_fee_pct"]["suggested_value"] == pytest.approx(0.03)
    ffe = by_key["ffe_reserve_pct"]
    assert ffe["suggested_value"] == pytest.approx(0.045)
    assert ffe["ratio_source"] == "reported"
    assert ffe["field_name"] == "pnl_benchmark.peer.ffe_reserve.ratio_pct"
    # The benchmark column lists it under its own category too.
    from app.services.pnl_benchmark_map import map_benchmark_to_categories

    cats = {c["key"]: c for c in map_benchmark_to_categories({
        "pnl_benchmark.peer.ffe_reserve.ratio_pct": 4.5,
    })}
    assert cats["ffe_reserve"]["ratio"] == pytest.approx(0.045)
    assert cats["ffe_reserve"]["label"] == "FF&E Reserve"
