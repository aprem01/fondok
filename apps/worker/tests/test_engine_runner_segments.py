"""Wave 2 P2.1 — Engine-runner integration for revenue segmentation.

These tests exercise the bridge between an STR_SEGMENTATION extraction
and the revenue engine's ``segments`` input. The pure engine math is
covered in ``test_revenue_segmentation.py``; here we pin the data flow:

* STR_SEGMENTATION extraction → ``base["segments"]`` populated with
  the five-segment dict + every field tagged
  ``SOURCE_STR_SEGMENTATION_DEFAULT``.
* When the extraction carries a channel_mix block, transient splits
  into BAR / OTA / Corporate using the report's shares.
* When the extraction omits channel_mix, the engine falls back to the
  60/30/10 institutional default split inside transient.
* An analyst override on ``segments.transient_ota.channel_cost_pct``
  beats the STR seed, and a second unrelated PATCH cycle does not
  clobber the prior override.
* An empty STR_SEGMENTATION extraction (no transient.mix_pct present)
  degrades to the legacy single-line path — the regression guarantee.

Real per-test SQLite DB, hermetic — no Anthropic calls.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-engine-runner-segments.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


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


_TENANT = "00000000-0000-0000-0000-000000000002"


async def _insert_deal(
    deal_id: UUID,
    *,
    name: str,
    keys: int,
    purchase: float,
    field_overrides: dict | None = None,
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, created_at, updated_at, field_overrides
                ) VALUES (
                    :id, :tenant, :name, 'Underwriting', 0.0, :keys,
                    :pp, :ts, :ts, :overrides
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "name": name,
                "keys": keys,
                "pp": purchase,
                "ts": datetime.now(UTC),
                "overrides": json.dumps(field_overrides or {}),
            },
        )
        await session.commit()


async def _set_field_overrides(deal_id: UUID, field_overrides: dict) -> None:
    """Simulate a PATCH /deals/{id} mutation that rewrites the JSON
    field_overrides column. The runner reads it on the next engine run."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("UPDATE deals SET field_overrides = :ov WHERE id = :id"),
            {"id": str(deal_id), "ov": json.dumps(field_overrides)},
        )
        await session.commit()


async def _insert_str_segmentation_extraction(
    deal_id: UUID, *, fields: list[dict[str, object]]
) -> None:
    """Insert an EXTRACTED STR_SEGMENTATION document with the given
    extraction fields."""
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    extraction = {
        "parser": "pymupdf",
        "total_pages": 1,
        "content_hash": "0" * 64,
        "parsed_at": datetime.now(UTC).isoformat(),
        "pages": [{"page_num": 1, "text": "STR segmentation", "tables": [], "metadata": {}}],
    }
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'STR_Segmentation.pdf',
                    'STR_SEGMENTATION', 'EXTRACTED', :ts, 1, :data
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "ts": datetime.now(UTC),
                "data": json.dumps(extraction),
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
                "tenant": _TENANT,
                "fields": json.dumps(fields),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


# ─────────────────────── Test cases ────────────────────────


async def test_str_segmentation_extraction_seeds_segments_with_provenance() -> None:
    """A vanilla STR Segmentation extraction (transient + group, no
    channel mix) populates ``base['segments']`` with the five-segment
    default split (BAR 60% / OTA 30% / Corp 10% within transient), and
    every segment field gets tagged with the canonical provenance."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_STR_SEGMENTATION_DEFAULT,
        _load_engine_inputs,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="STR Segments Demo", keys=200, purchase=40_000_000)

    await _insert_str_segmentation_extraction(
        deal_id,
        fields=[
            {"field_name": "str_segmentation.report_year", "value": 2025},
            {"field_name": "str_segmentation.ttm.overall.occupancy_pct", "value": 0.78},
            {"field_name": "str_segmentation.ttm.overall.adr_usd", "value": 250.0},
            {"field_name": "str_segmentation.ttm.transient.mix_pct", "value": 0.80},
            {"field_name": "str_segmentation.ttm.transient.adr_usd", "value": 260.0},
            {"field_name": "str_segmentation.ttm.group.mix_pct", "value": 0.20},
            {"field_name": "str_segmentation.ttm.group.adr_usd", "value": 235.0},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    segments = base.get("segments") or []
    assert len(segments) == 5
    by_name = {s["name"]: s for s in segments}

    # 60/30/10 default split inside transient (no channel_mix block).
    # transient_mix = 0.80 → BAR 0.48, OTA 0.24, Corporate 0.08.
    # Group = 0.20, Contract = 0.00. Total = 1.00.
    # The seed normalizes; verify approx within rounding tolerance.
    assert by_name["transient_bar"]["mix_pct"] == pytest.approx(0.48, abs=1e-6)
    assert by_name["transient_ota"]["mix_pct"] == pytest.approx(0.24, abs=1e-6)
    assert by_name["corporate"]["mix_pct"] == pytest.approx(0.08, abs=1e-6)
    assert by_name["group"]["mix_pct"] == pytest.approx(0.20, abs=1e-6)
    assert by_name["contract"]["mix_pct"] == pytest.approx(0.00, abs=1e-6)

    # Institutional channel-cost defaults applied per segment.
    assert by_name["transient_bar"]["channel_cost_pct"] == pytest.approx(0.02)
    assert by_name["transient_ota"]["channel_cost_pct"] == pytest.approx(0.20)
    assert by_name["corporate"]["channel_cost_pct"] == pytest.approx(0.08)
    assert by_name["group"]["channel_cost_pct"] == pytest.approx(0.05)
    assert by_name["contract"]["channel_cost_pct"] == pytest.approx(0.02)

    # Per-segment ADR uses the extracted segment ADR when available.
    assert by_name["transient_bar"]["adr"] == pytest.approx(260.0)
    assert by_name["group"]["adr"] == pytest.approx(235.0)

    # Provenance map — every segment field tagged with the canonical
    # STR-segmentation source label so the UI badge renders "STR Segmentation".
    sources = base.get("__sources__", {})
    assert sources["segments.transient_bar.mix_pct"] == SOURCE_STR_SEGMENTATION_DEFAULT
    assert sources["segments.transient_ota.channel_cost_pct"] == SOURCE_STR_SEGMENTATION_DEFAULT
    assert sources["segments.group.adr"] == SOURCE_STR_SEGMENTATION_DEFAULT


async def test_channel_mix_extraction_splits_transient_using_report_shares() -> None:
    """When the STR Segmentation report carries a channel_mix block
    (Direct / OTA / Brand.com / Voice) the seed must split transient
    demand using those shares — NOT the 60/30/10 default. This is the
    most consequential extraction signal; an institutional reviewer
    expects the seeded OTA share to mirror what's in the report."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Channel Mix Demo", keys=180, purchase=32_000_000)

    await _insert_str_segmentation_extraction(
        deal_id,
        fields=[
            {"field_name": "str_segmentation.ttm.overall.occupancy_pct", "value": 0.75},
            {"field_name": "str_segmentation.ttm.overall.adr_usd", "value": 240.0},
            {"field_name": "str_segmentation.ttm.transient.mix_pct", "value": 0.80},
            {"field_name": "str_segmentation.ttm.transient.adr_usd", "value": 245.0},
            {"field_name": "str_segmentation.ttm.group.mix_pct", "value": 0.20},
            # Channel mix block: 50% Direct, 30% OTA, 20% Brand
            # (no Corporate channel, so corporate share inside
            # transient is 0).
            {"field_name": "str_segmentation.ttm.channel_mix.direct_pct", "value": 0.50},
            {"field_name": "str_segmentation.ttm.channel_mix.ota_pct", "value": 0.30},
            {"field_name": "str_segmentation.ttm.channel_mix.brand_pct", "value": 0.20},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    by_name = {s["name"]: s for s in (base.get("segments") or [])}
    # Transient = 80%; OTA share within transient = 30%; Corporate
    # share within transient = 0 (not broken out). BAR mops up the
    # remainder including Direct + Brand + Voice = 70%.
    # → BAR mix = 0.80 × 0.70 = 0.56; OTA mix = 0.80 × 0.30 = 0.24.
    assert by_name["transient_bar"]["mix_pct"] == pytest.approx(0.56, abs=1e-6)
    assert by_name["transient_ota"]["mix_pct"] == pytest.approx(0.24, abs=1e-6)
    assert by_name["corporate"]["mix_pct"] == pytest.approx(0.00, abs=1e-6)
    assert by_name["group"]["mix_pct"] == pytest.approx(0.20, abs=1e-6)


async def test_analyst_override_on_segment_channel_cost_beats_str_seed() -> None:
    """When the analyst pins a per-segment channel cost via the
    persisted field_overrides, that value wins over both the STR
    seed and the institutional default — the override-routing
    contract."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_ANALYST_OVERRIDE,
        _load_engine_inputs,
    )

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="OTA Override Demo",
        keys=160,
        purchase=28_000_000,
        field_overrides={
            # Analyst negotiated a custom OTA agreement that brought
            # commission down to 12% — beats the 20% institutional
            # default.
            "segments.transient_ota.channel_cost_pct": 0.12,
        },
    )
    await _insert_str_segmentation_extraction(
        deal_id,
        fields=[
            {"field_name": "str_segmentation.ttm.overall.adr_usd", "value": 230.0},
            {"field_name": "str_segmentation.ttm.transient.mix_pct", "value": 0.85},
            {"field_name": "str_segmentation.ttm.group.mix_pct", "value": 0.15},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    by_name = {s["name"]: s for s in (base.get("segments") or [])}
    assert by_name["transient_ota"]["channel_cost_pct"] == pytest.approx(0.12)
    # The other segments keep the institutional defaults — override
    # is targeted.
    assert by_name["transient_bar"]["channel_cost_pct"] == pytest.approx(0.02)
    assert by_name["corporate"]["channel_cost_pct"] == pytest.approx(0.08)

    # Provenance: the overridden field is tagged ANALYST_OVERRIDE,
    # everyone else stays on STR_SEGMENTATION_DEFAULT.
    sources = base.get("__sources__", {})
    assert (
        sources["segments.transient_ota.channel_cost_pct"]
        == SOURCE_ANALYST_OVERRIDE
    )


async def test_two_patch_cycles_first_override_survives_second_unrelated_patch() -> None:
    """A pin on transient_ota.channel_cost_pct must survive a later
    unrelated PATCH that, say, adjusts group.adr. This pins the
    survival contract — analyst overrides are CUMULATIVE across PATCH
    cycles, never wiped by an unrelated edit."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="Two PATCH Demo",
        keys=170,
        purchase=29_000_000,
        field_overrides={
            "segments.transient_ota.channel_cost_pct": 0.15,
        },
    )
    await _insert_str_segmentation_extraction(
        deal_id,
        fields=[
            {"field_name": "str_segmentation.ttm.overall.adr_usd", "value": 245.0},
            {"field_name": "str_segmentation.ttm.transient.mix_pct", "value": 0.80},
            {"field_name": "str_segmentation.ttm.group.mix_pct", "value": 0.20},
        ],
    )

    # Cycle 1 — verify the override is in effect.
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))
    by_name = {s["name"]: s for s in base["segments"]}
    assert by_name["transient_ota"]["channel_cost_pct"] == pytest.approx(0.15)

    # Cycle 2 — simulate a PATCH that ADDS a group.adr override
    # without touching the OTA override. The persisted field_overrides
    # JSON is the union of every prior pin (the API merges, not
    # replaces).
    await _set_field_overrides(
        deal_id,
        {
            "segments.transient_ota.channel_cost_pct": 0.15,
            "segments.group.adr": 280.0,
        },
    )
    async with factory() as session:
        base2 = await _load_engine_inputs(session, str(deal_id))
    by_name2 = {s["name"]: s for s in base2["segments"]}
    # The original OTA override survives unchanged.
    assert by_name2["transient_ota"]["channel_cost_pct"] == pytest.approx(0.15)
    # The new group.adr override is in effect.
    assert by_name2["group"]["adr"] == pytest.approx(280.0)


async def test_no_str_segmentation_extraction_degrades_to_single_line_path() -> None:
    """A deal with no STR Segmentation upload must NOT pick up a
    seeded segments list — it stays on the legacy single-line path.
    This is the backward-compat guarantee: every existing Kimpton-like
    pro forma renders identically pre- and post-Wave 2."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(deal_id, name="No STR Seg Demo", keys=132, purchase=36_400_000)

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    # No segments seeded → engine input ``segments=[]`` → legacy path.
    assert base.get("segments", []) == []
