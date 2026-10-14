"""FON-98 — ``deals.reopening_date`` and the stabilization anchor.

Rani + Eshan: the stabilized year defaults to Year 3 of operations, analyst-
overridable. For a material repositioning / re-flag it is counted from the
reopening date (the first full operating year after reopening is Year 1);
otherwise from the acquisition close.

Covered here:

* the additive migration adds the column (SQLite mirror of the Postgres ALTER);
* create / read / list / update round-trip ``reopening_date`` and null clears it;
* a schema WITHOUT the column (migration not yet run) still serves every route
  and the engine runner still reads the deal row (keys / purchase_price);
* the resolver: both anchors, the clamp, the two honest fallbacks, the
  analyst override winning over either anchor;
* the full chain: a deal with a reopening date publishes
  ``source == "default_year_3_reopening"`` / ``anchor == "reopening"`` on the
  expense block, Debt's stabilized DSCR follows the SAME year, and the
  returns are untouched (the anchor is reporting-only on the default exit
  basis, exactly like ``stabilization_year``);
* no reopening date → the block is byte-identical to before (goldens hold).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-reopening-date.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "engine_outputs", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _column_exists() -> bool:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("PRAGMA table_info(deals)"))).all()
    return any(r[1] == "reopening_date" for r in rows)


# ── the column + the API ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_adds_the_column() -> None:
    assert await _column_exists()


@pytest.mark.asyncio
async def test_create_read_list_update_round_trip() -> None:
    async with _client() as client:
        r = await client.post(
            "/deals", json={"name": "Re-flag Hotel", "reopening_date": "2027-03-15"}
        )
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]
        assert r.json()["reopening_date"] == "2027-03-15"

        assert (await client.get(f"/deals/{deal_id}")).json()["reopening_date"] == (
            "2027-03-15"
        )
        listed = {d["id"]: d for d in (await client.get("/deals")).json()}
        assert listed[deal_id]["reopening_date"] == "2027-03-15"

        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": "2027-09-01"})
        assert r.status_code == 200, r.text
        assert r.json()["reopening_date"] == "2027-09-01"

        # null clears it — the default goes back to Year 3 after close.
        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": None})
        assert r.status_code == 200, r.text
        assert r.json()["reopening_date"] is None
        assert (await client.get(f"/deals/{deal_id}")).json()["reopening_date"] is None


@pytest.mark.asyncio
async def test_optional_and_must_be_a_date() -> None:
    async with _client() as client:
        r = await client.post("/deals", json={"name": "No reopening"})
        assert r.status_code == 201, r.text
        assert r.json()["reopening_date"] is None

        r = await client.post("/deals", json={"name": "Bad", "reopening_date": "March 2027"})
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a_patch_of_the_date_needs_no_justification_note() -> None:
    """It is a deal COLUMN, never a ``field_overrides`` key — the FON-74 gate
    does not see it, and it is reporting-only in the model anyway."""
    async with _client() as client:
        r = await client.post("/deals", json={"name": "Column, not override"})
        deal_id = r.json()["id"]
        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": "2027-03-15"})
        assert r.status_code == 200, r.text
        assert r.json()["reopening_date"] == "2027-03-15"


@pytest.mark.asyncio
async def test_routes_survive_a_schema_without_the_column(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("ALTER TABLE deals DROP COLUMN reopening_date"))
        await session.commit()
    assert not await _column_exists()
    try:
        with caplog.at_level("WARNING", logger="app.api.deals"):
            async with _client() as client:
                r = await client.post(
                    "/deals", json={"name": "Old schema", "reopening_date": "2027-03-15"}
                )
                assert r.status_code == 201, r.text
                assert r.json()["reopening_date"] is None
                deal_id = r.json()["id"]
                r = await client.get(f"/deals/{deal_id}")
                assert r.status_code == 200 and r.json()["reopening_date"] is None
                assert (await client.get("/deals")).status_code == 200
                r = await client.patch(
                    f"/deals/{deal_id}",
                    json={"reopening_date": "2027-03-15", "city": "Miami"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["city"] == "Miami"
                assert r.json()["reopening_date"] is None
        messages = [rec.getMessage() for rec in caplog.records]
        assert any("deals.create: reopening_date=" in m for m in messages)
        assert any("deals.update: reopening_date dropped" in m for m in messages)
    finally:
        await run_startup_migrations()
    assert await _column_exists()


# ── the resolver (pure) ──────────────────────────────────────────────────

_SEVEN = dict(
    total_revenue_by_year=[10.0, 11.0, 12.0, 12.4, 12.8, 13.2, 13.6],
    noi_before_reserve_by_year=[3.0, 3.6, 4.2, 4.3, 4.4, 4.5, 4.6],
    cash_noi_by_year=[2.6, 3.1, 3.7, 3.8, 3.9, 4.0, 4.1],
)
# Close 30 Sep 2025 → projection Year 1 is calendar 2025 (revenue engine rule).
_CLOSE = "2025-09-30"


def test_no_reopening_date_is_year_3_after_close_byte_identical() -> None:
    from app.engines.stabilization import build_stabilized_year

    before = build_stabilized_year(**_SEVEN)
    after = build_stabilized_year(**_SEVEN, acquisition_close_date=_CLOSE, reopening_date=None)
    assert before is not None and after is not None
    assert before.model_dump() == after.model_dump()
    assert after.source == "default_year_3"
    assert after.anchor == "acquisition_close"
    assert after.stabilized_year == 3
    assert after.reopening_date is None
    assert after.anchor_fallback_reason is None


def test_reopening_mid_2027_counts_year_3_from_2028() -> None:
    """Reopen 15 Mar 2027 → first FULL operating year 2028 → Year 3 = 2030 →
    projection index 5 (Year 6) on a 2025 Year-1 calendar."""
    from app.engines.stabilization import (
        build_stabilized_year,
        reopening_anchor_index,
        resolve_stabilized_year_index,
    )

    block = build_stabilized_year(
        **_SEVEN, acquisition_close_date=_CLOSE, reopening_date="2027-03-15"
    )
    assert block is not None
    assert block.source == "default_year_3_reopening"
    assert block.anchor == "reopening"
    assert block.stabilized_year_index == 5
    assert block.stabilized_year == 6
    assert block.clamped is False
    assert block.reopening_date == "2027-03-15"
    assert block.anchor_fallback_reason is None
    # Every figure is that year's.
    assert block.stabilized_revenue == pytest.approx(13.2)
    assert block.stabilized_cash_noi == pytest.approx(4.0)
    # The debt entry point resolves the SAME index.
    assert reopening_anchor_index(
        acquisition_close_date=_CLOSE, reopening_date="2027-03-15", n_years=7
    ) == (5, None)
    assert (
        resolve_stabilized_year_index(
            occupancy_by_year=None,
            stabilized_occupancy=None,
            noi_by_year=_SEVEN["cash_noi_by_year"],
            acquisition_close_date=_CLOSE,
            reopening_date="2027-03-15",
        )
        == 5
    )


def test_a_first_of_january_reopening_is_itself_the_first_full_year() -> None:
    from app.engines.stabilization import (
        first_full_operating_year_after_reopening,
        reopening_anchor_index,
    )
    from datetime import date

    assert first_full_operating_year_after_reopening(date(2027, 1, 1)) == 2027
    assert first_full_operating_year_after_reopening(date(2027, 1, 2)) == 2028
    # 2027 is Year 1 → Year 3 = 2029 → index 4 on a 2025 calendar.
    assert reopening_anchor_index(
        acquisition_close_date=_CLOSE, reopening_date="2027-01-01", n_years=7
    ) == (4, None)


def test_reopening_past_the_hold_clamps_to_the_last_year_and_says_so() -> None:
    from app.engines.stabilization import build_stabilized_year

    # Reopen Jun 2029 → first full 2030 → Year 3 = 2032 → index 7 > 6.
    block = build_stabilized_year(
        **_SEVEN, acquisition_close_date=_CLOSE, reopening_date="2029-06-01"
    )
    assert block is not None
    assert block.anchor == "reopening"
    assert block.stabilized_year == 7
    assert block.clamped is True


def test_reopening_without_a_close_date_falls_back_and_names_the_reason() -> None:
    from app.engines.stabilization import build_stabilized_year

    block = build_stabilized_year(**_SEVEN, reopening_date="2027-03-15")
    assert block is not None
    assert block.source == "default_year_3"
    assert block.anchor == "acquisition_close"
    assert block.stabilized_year == 3
    assert block.reopening_date == "2027-03-15"  # echoed, so the UI can explain
    assert block.anchor_fallback_reason == "no_close_date"


def test_a_reopening_long_before_the_projection_falls_back_and_names_the_reason() -> None:
    from app.engines.stabilization import build_stabilized_year

    block = build_stabilized_year(
        **_SEVEN, acquisition_close_date=_CLOSE, reopening_date="2019-06-01"
    )
    assert block is not None
    assert block.anchor == "acquisition_close"
    assert block.stabilized_year == 3
    assert block.anchor_fallback_reason == "reopening_before_projection"


def test_the_analyst_override_wins_over_the_reopening_anchor() -> None:
    from app.engines.stabilization import build_stabilized_year

    block = build_stabilized_year(
        **_SEVEN,
        acquisition_close_date=_CLOSE,
        reopening_date="2027-03-15",
        stabilization_year=4,
    )
    assert block is not None
    assert block.source == "analyst_override"
    assert block.stabilized_year == 4
    # The anchor the override replaced is still published for the label.
    assert block.anchor == "reopening"
    assert block.reopening_date == "2027-03-15"

    # Re-saving the reopening default unchanged is not an override.
    same = build_stabilized_year(
        **_SEVEN,
        acquisition_close_date=_CLOSE,
        reopening_date="2027-03-15",
        stabilization_year=6,
    )
    assert same is not None
    assert same.source == "default_year_3_reopening"


# ── the full chain ───────────────────────────────────────────────────────


async def _deal(
    session, *, overrides: dict, reopening_date: str | None
) -> tuple[str, str]:
    deal_id, tenant_id = str(uuid4()), str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, field_overrides,
                               reopening_date, created_at, updated_at)
            VALUES (:id, :tenant, :name, 'Draft', :ov, :reopening, :now, :now)
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant_id,
            "name": "Re-flag Hotel",
            "ov": json.dumps(overrides),
            "reopening": reopening_date,
            "now": datetime.now(UTC),
        },
    )
    await session.commit()
    return deal_id, tenant_id


async def _run(*, overrides: dict, reopening_date: str | None) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    factory = get_session_factory()
    async with factory() as session:
        deal_id, tenant_id = await _deal(
            session, overrides=overrides, reopening_date=reopening_date
        )
        return await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )


def _comparable(value):
    if isinstance(value, dict):
        return {k: _comparable(v) for k, v in value.items() if k != "deal_id"}
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    return value


@pytest.mark.asyncio
async def test_full_run_anchors_on_the_reopening_and_debt_follows() -> None:
    from app.engines.debt import _resolve_stabilized_year_index

    close = {"acquisition_close_date": _CLOSE}
    baseline = await _run(overrides=close, reopening_date=None)
    reflag = await _run(overrides=close, reopening_date="2027-03-15")

    stab0 = baseline["expense"]["outputs"]["stabilization"]
    stab1 = reflag["expense"]["outputs"]["stabilization"]
    assert stab0["source"] == "default_year_3"
    assert stab0["anchor"] == "acquisition_close"
    assert stab0["reopening_date"] is None
    assert stab0["stabilized_year"] == 3

    assert stab1["source"] == "default_year_3_reopening"
    assert stab1["anchor"] == "reopening"
    assert stab1["reopening_date"] == "2027-03-15"
    assert stab1["anchor_fallback_reason"] is None
    n_years = len(reflag["expense"]["outputs"]["years"])
    # 2025 calendar → Year 3 after reopening = 2030 = index 5, clamped to hold.
    assert stab1["stabilized_year_index"] == min(5, n_years - 1)
    assert stab1["stabilized_year_index"] != stab0["stabilized_year_index"]

    # The projection calendar the revenue engine published agrees.
    assert reflag["revenue"]["outputs"]["projection_calendar_years"][0] == 2025

    # Debt resolves the SAME index through its own entry point…
    noi = [y["noi"] for y in reflag["expense"]["outputs"]["years"]]
    assert (
        _resolve_stabilized_year_index(
            occupancy_by_year=None,
            stabilized_occupancy=None,
            noi_by_year=noi,
            acquisition_close_date=_CLOSE,
            reopening_date="2027-03-15",
        )
        == stab1["stabilized_year_index"]
    )
    # …and its published stabilized DSCR is that year's NOI over that year's
    # debt service — i.e. it moved with the anchor.
    debt1 = reflag["debt"]["outputs"]
    debt0 = baseline["debt"]["outputs"]
    assert debt1["stabilized_dscr"] != debt0["stabilized_dscr"]
    assert debt1["stabilized_debt_yield"] == pytest.approx(
        noi[stab1["stabilized_year_index"]] / debt1["loan_amount"]
    )

    # Reporting-only: every return is bit-identical (same guard as
    # test_stabilization_year_does_not_move_returns).
    for key in ("gross_sale_price", "levered_irr", "equity_multiple", "terminal_noi"):
        assert reflag["returns"]["outputs"][key] == baseline["returns"]["outputs"][key], key
    for engine in ("revenue", "fb", "expense", "capital", "returns"):
        before = _comparable(baseline[engine]["outputs"])
        after = _comparable(reflag[engine]["outputs"])
        before.pop("stabilization", None)
        after.pop("stabilization", None)
        assert before == after, engine


@pytest.mark.asyncio
async def test_full_run_without_a_close_date_falls_back_honestly() -> None:
    run = await _run(overrides={}, reopening_date="2027-03-15")
    stab = run["expense"]["outputs"]["stabilization"]
    assert stab["source"] == "default_year_3"
    assert stab["anchor"] == "acquisition_close"
    assert stab["reopening_date"] == "2027-03-15"
    assert stab["anchor_fallback_reason"] == "no_close_date"
    assert stab["stabilized_year"] == 3


@pytest.mark.asyncio
async def test_the_runner_still_reads_the_deal_row_on_a_schema_without_the_column() -> None:
    """keys / purchase_price come off the deals row through the SAME SELECT —
    an un-migrated DB must not lose them."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import run_all_engines

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("ALTER TABLE deals DROP COLUMN reopening_date"))
        await session.commit()
    try:
        async with factory() as session:
            deal_id, tenant_id = str(uuid4()), str(uuid4())
            await session.execute(
                text(
                    """
                    INSERT INTO deals (id, tenant_id, name, status, keys, purchase_price,
                                       field_overrides, created_at, updated_at)
                    VALUES (:id, :tenant, 'Old schema', 'Draft', 150, 41000000,
                            '{}', :now, :now)
                    """
                ),
                {"id": deal_id, "tenant": tenant_id, "now": datetime.now(UTC)},
            )
            await session.commit()
            results = await run_all_engines(
                session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
            )
        uses = results["capital"]["outputs"]["uses"]
        purchase = next(u["amount"] for u in uses if u["label"] == "Purchase Price")
        assert purchase == pytest.approx(41_000_000)  # deal row, not the seed
        stab = results["expense"]["outputs"]["stabilization"]
        assert stab["anchor"] == "acquisition_close"
        assert stab["reopening_date"] is None
    finally:
        await run_startup_migrations()
