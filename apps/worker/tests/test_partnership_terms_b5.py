"""B5 / C3 (go-live audit) — the partnership waterfall runs only on terms the
deal carries; the platform's suggested terms are defined once and published
as badged ``seed`` suggestions, never applied."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.fixtures.kimpton_seed import kimpton_partnership_field_overrides

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-partnership-terms-b5.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _migrate() -> None:
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    yield


async def _deal(session, overrides: dict) -> tuple[str, str]:
    deal_id, tenant_id = str(uuid4()), str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, keys, purchase_price,
                               field_overrides, created_at, updated_at)
            VALUES (:id, :tenant, 'Partnership Terms Hotel', 'Draft', 132, 36400000,
                    :ov, :now, :now)
            """
        ),
        {"id": deal_id, "tenant": tenant_id, "ov": json.dumps(overrides),
         "now": datetime.now(UTC)},
    )
    await session.commit()
    return deal_id, tenant_id


async def _run(overrides: dict) -> tuple[dict, str, str]:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    async with get_session_factory()() as session:
        deal_id, tenant_id = await _deal(session, overrides)
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )
    return results, deal_id, tenant_id


# ─────────────────────────── one definition (C3) ───────────────────────────


def test_suggestions_are_defined_once_and_never_defaults() -> None:
    import inspect

    import app.services.engine_runner as runner
    from app.engines.partnership import (
        SUGGESTED_GP_EQUITY_PCT,
        SUGGESTED_LP_EQUITY_PCT,
        SUGGESTED_PREF_RATE,
        SUGGESTED_PROMOTE_TIERS,
        PartnershipInputExt,
    )

    assert (SUGGESTED_GP_EQUITY_PCT, SUGGESTED_LP_EQUITY_PCT, SUGGESTED_PREF_RATE) == (
        0.10, 0.90, 0.10,
    )
    assert len(SUGGESTED_PROMOTE_TIERS) == 6
    defaults = runner._default_assumptions()
    for key in ("gp_equity_pct", "lp_equity_pct", "pref_rate"):
        assert key not in defaults
        # The engine input carries no default of its own either.
        assert PartnershipInputExt.model_fields[key].is_required()
    assert not hasattr(runner, "_KIMPTON_WATERFALL_REFERENCE")
    assert "Kimpton" not in inspect.getsource(runner._build_partnership_waterfall)


# ─────────────────────── not applicable without terms (B5) ──────────────────


@pytest.mark.asyncio
async def test_no_terms_partnership_not_applicable_cash_flow_still_runs() -> None:
    results, _deal_id, _tenant = await _run({})
    part = results["partnership"]
    assert part["status"] == "failed"
    assert part["failure"]["reason"] == "not_applicable"
    assert part["failure"]["message"] == "no partnership terms on this deal"
    assert part["failure"]["missing"] == ["split", "pref_rate", "tiers"]
    cf = results["cash_flow"]
    assert cf["status"] == "complete"
    assert cf["outputs"]["distributions"] == []
    assert cf["outputs"]["distributions_unavailable"].startswith("no partnership waterfall")
    for name in ("revenue", "fb", "expense", "capital", "debt", "returns", "sensitivity"):
        assert results[name]["status"] == "complete", name


@pytest.mark.asyncio
async def test_partial_terms_name_what_is_missing() -> None:
    results, *_ = await _run({"pref_rate": 0.08})
    failure = results["partnership"]["failure"]
    assert failure["reason"] == "not_applicable"
    assert failure["missing"] == ["split", "tiers"]
    assert failure["message"] == (
        "partnership terms incomplete on this deal: no GP / LP equity split, promote tiers"
    )


@pytest.mark.asyncio
async def test_carried_terms_run_the_waterfall() -> None:
    results, *_ = await _run(kimpton_partnership_field_overrides())
    part = results["partnership"]
    assert part["status"] == "complete", part
    assert part["outputs"]["promote_amount"] >= 0
    assert results["cash_flow"]["outputs"]["distributions"]
    assert results["cash_flow"]["outputs"]["distributions_unavailable"] is None


@pytest.mark.asyncio
async def test_not_applicable_run_is_complete_in_the_run_list() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _results, deal_id, tenant_id = await _run({})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"X-Tenant-Id": tenant_id},
    ) as client:
        r = await client.get(f"/deals/{deal_id}/engines/runs")
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert runs and runs[0]["status"] == "complete"
    assert runs[0]["is_full_chain"] is True


# ───────────────────── published suggestions (C3) ────────────────────────


@pytest.mark.asyncio
async def test_assumption_sources_publish_suggestions_as_seed() -> None:
    from uuid import UUID

    from app.api.deals import _compute_assumption_sources
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        deal_id, tenant_id = await _deal(session, {"pref_rate": 0.08})
        resp = await _compute_assumption_sources(
            session, deal_id=UUID(deal_id), tenant_id=UUID(tenant_id)
        )
    v, s, r = resp.values, resp.sources, resp.reasons
    # Carried term: the deal's own value and label.
    assert v["partnership.pref_rate"] == 0.08
    assert s["partnership.pref_rate"] == "analyst_override"
    assert "partnership.pref_rate" not in r
    # Not carried: the platform suggestion, badged seed / awaiting the analyst.
    assert v["partnership.gp_equity_pct"] == 0.10
    assert s["partnership.gp_equity_pct"] == "seed"
    assert r["partnership.gp_equity_pct"] == "awaiting_analyst"
    assert v["partnership.lp_equity_pct"] == 0.90
    assert v["partnership.waterfall.tier_count"] == 6
    assert (v["partnership.waterfall.0.hurdle_rate"], v["partnership.waterfall.0.gp_split"]) == (0.10, 0.0)
    assert v["partnership.waterfall.0.label"] == "Preferred (to 10%)"
    assert v["partnership.waterfall.5.label"] == "Tier 6 (>30%)"
    assert v["partnership.waterfall.5.lp_split"] == 0.5
    assert s["partnership.waterfall.3.gp_split"] == "seed"
    # The suggestion never became an engine input.
    assert "gp_equity_pct" not in v


@pytest.mark.asyncio
async def test_assumption_sources_publish_carried_tiers_with_their_labels() -> None:
    from uuid import UUID

    from app.api.deals import _compute_assumption_sources
    from app.database import get_session_factory

    overrides = kimpton_partnership_field_overrides()
    overrides["partnership.waterfall.2.removed"] = True
    async with get_session_factory()() as session:
        deal_id, tenant_id = await _deal(session, overrides)
        resp = await _compute_assumption_sources(
            session, deal_id=UUID(deal_id), tenant_id=UUID(tenant_id)
        )
    v, s = resp.values, resp.sources
    assert s["partnership.waterfall.1.hurdle_rate"] == "analyst_override"
    assert v["partnership.waterfall.2.removed"] is True
    assert "partnership.waterfall.2.label" not in v  # removed tier: no label
    assert v["partnership.waterfall.1.label"] == "Tier 2 (to 15%)"
    assert v["partnership.waterfall.3.label"] == "Tier 3 (to 25%)"
    assert s["partnership.pref_rate"] == "analyst_override"
    assert not any(src == "seed" for k, src in s.items() if k.startswith("partnership."))


# ─────────────────────────── exports state it ────────────────────────────


def test_excel_partnership_sheet_states_not_applicable() -> None:
    from openpyxl import Workbook

    from app.export.excel import _build_partnership
    from app.export.live_payload import _partnership_unavailable

    snapshot = {
        "partnership": {
            "status": "failed",
            "outputs": None,
            "failure": {
                "engine": "partnership",
                "reason": "not_applicable",
                "message": "no partnership terms on this deal",
            },
        }
    }
    unavailable = _partnership_unavailable(snapshot)
    assert unavailable == {
        "code": "not_applicable",
        "reason": "no partnership terms on this deal",
    }
    wb = Workbook()
    _build_partnership(wb, {"partnership_unavailable": unavailable})
    cells = [str(c.value) for row in wb["Partnership"].iter_rows() for c in row if c.value]
    assert any("no partnership terms on this deal" in c for c in cells)
    assert not any("engine output on the deal's canonical run" in c for c in cells)
