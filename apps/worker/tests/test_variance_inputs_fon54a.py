"""FON-54a (part 2) — the INPUTS to the variance comparison are honest.

Built from Sam's deal e577f547 (live ``GET /analysis/{id}/variance`` before
the fix): occupancy compared a percent (83) with a fraction (→ +8,206%),
GOP / rooms revenue compared the OM against a single MONTH of the T-12
(514,931 / 954,187 instead of the annual 4,970,460 / 9,332,100), a 2019
P&L's ``p_and_l_usali.gop`` and a 2023 P&L's ``ttm_summary_per_om.*`` were
admitted as "broker" claims, and ``ttm_performance.segment.*`` comp-set
stats were compared as if they were the subject.

Pins:
* broker-side admission — only the OM's own claim (``broker_proforma.*``,
  ``broker.*``, ``ttm_summary_per_om.*``, ``ttm_performance.subject.*`` on
  broker material); actuals documents, market segments and the OM's
  historical-year block are excluded — and disclosed as excluded;
* unit normalisation — occupancy to a fraction before comparing, currency
  to whole dollars; an unestablished unit is not compared;
* actual-side period sanity — the T-12's ANNUAL lines, never a monthly
  slice; T-12 preferred over a P&L;
* plausibility guard — |delta_pct| > 300% is "Basis mismatch — needs
  review" at Info severity, never a Critical variance.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_TENANT = "54a1cff3-6f9b-57a9-8d2a-5511f3dd9f7e"


def _ef(name: str, value: float, page: int = 1, unit: str | None = None):
    from fondok_schemas import ExtractionField

    return ExtractionField(field_name=name, value=value, source_page=page, confidence=0.9, unit=unit)


# ═══════════════════ unit normalisation ═══════════════════


def test_normalize_broker_value_units() -> None:
    from app.agents.variance import normalize_broker_value

    # Occupancy: percent → fraction; fraction untouched; >100 → not established.
    assert normalize_broker_value("ttm_summary_per_om.occupancy_pct", 83.0) == (0.83, "occupancy 83% read as 0.830")
    assert normalize_broker_value("broker_proforma.occupancy", 0.83) == (0.83, None)
    v, note = normalize_broker_value("occupancy_pct", 150.0)
    assert v is None and "not established" in (note or "")
    # Currency: whole dollars; a thousands unit is scaled.
    assert normalize_broker_value("ttm_summary_per_om.rooms_revenue_usd", 9_541_537.0) == (9_541_537.0, None)
    v, note = normalize_broker_value("broker_proforma.noi_usd", 3_356.709, unit="$000")
    assert v == pytest.approx(3_356_709.0) and "×1,000" in (note or "")


def test_path_predicates() -> None:
    from app.agents.variance import is_market_segment, is_om_historical_year, is_period_slice

    assert is_period_slice("p_and_l_usali.monthly.apr_2024.rooms_revenue")
    assert is_period_slice("p_and_l_usali.monthly.jan.gop")
    assert not is_period_slice("p_and_l_usali.operating_revenue.rooms_revenue")
    assert is_om_historical_year("p_and_l_usali.2021.gop_usd")
    assert is_om_historical_year("historical_performance.2022.gop_usd")
    assert not is_om_historical_year("ttm_summary_per_om.gop_usd")
    assert is_market_segment("ttm_performance.segment.luxury_upper_upscale.occupancy_pct")
    assert not is_market_segment("ttm_performance.subject.occupancy")


# ═══════════════════ broker-side admission ═══════════════════


def test_strict_admission_rejects_actuals_documents_segments_and_history() -> None:
    from app.agents.variance import _broker_fields_from_extraction

    # A 2023 P&L that happens to carry OM-style paths — an ACTUALS document.
    rejected: list = []
    out = _broker_fields_from_extraction(
        [_ef("ttm_summary_per_om.occupancy_pct", 72.0), _ef("p_and_l_usali.gop_usd", 4_736_470)],
        doc_type="PNL", strict=True, excluded=rejected,
    )
    assert out == []
    assert [r for (_f, r) in rejected] and all("actuals document" in r for (_f, r) in rejected)

    # An explicit broker_proforma.* path is the broker's claim wherever it sits.
    out = _broker_fields_from_extraction(
        [_ef("broker_proforma.noi_usd", 3_356_709)], doc_type="T12", strict=True
    )
    assert [b.field for b in out] == ["broker_proforma.noi_usd"]
    assert out[0].source_doc_type == "T12"

    # The OM: subject claims admitted (units normalised); segment stats and
    # the historical-year block excluded with reasons; monthly slices dropped.
    rejected = []
    out = _broker_fields_from_extraction(
        [
            _ef("ttm_summary_per_om.rooms_revenue_usd", 9_541_537, page=14),
            _ef("ttm_summary_per_om.occupancy_pct", 83.0, page=12),
            _ef("ttm_performance.subject.adr_usd", 385.0, page=12),
            _ef("ttm_performance.segment.luxury_upper_upscale.occupancy_pct", 74.1, page=20),
            _ef("p_and_l_usali.2021.gop_usd", 4_851_106, page=16),
            _ef("p_and_l_usali.monthly.jan.rooms_revenue_usd", 800_000, page=16),
            _ef("occupancy_pct", 150.0, page=12),  # unit cannot be established
        ],
        doc_type="OM", strict=True, excluded=rejected,
    )
    assert [b.field for b in out] == [
        "ttm_summary_per_om.rooms_revenue_usd",
        "ttm_summary_per_om.occupancy_pct",
        "ttm_performance.subject.adr_usd",
    ]
    occ = out[1]
    assert occ.value == 0.83 and occ.unit_note == "occupancy 83% read as 0.830"
    reasons = {f.field_name: r for (f, r) in rejected}
    assert "market-segment" in reasons["ttm_performance.segment.luxury_upper_upscale.occupancy_pct"]
    assert "historical-year" in reasons["p_and_l_usali.2021.gop_usd"]
    assert "not established" in reasons["occupancy_pct"]
    assert "p_and_l_usali.monthly.jan.rooms_revenue_usd" not in reasons  # silently out of scope


def test_legacy_mode_still_admits_flat_keys_but_drops_segments_and_history() -> None:
    from app.agents.variance import _broker_fields_from_extraction

    out = _broker_fields_from_extraction(
        [
            _ef("noi", 5_200_000),
            _ef("ttm_performance.segment.luxury_upper_upscale.adr_usd", 452.0),
            _ef("p_and_l_usali.2022.rooms_revenue_usd", 9_000_000),
        ]
    )
    assert [b.field for b in out] == ["noi"]


# ═══════════════════ plausibility guard ═══════════════════


def _actuals(**over):
    from fondok_schemas import DepartmentalExpenses, FixedCharges, USALIFinancials, UndistributedExpenses

    base = dict(
        period_label="T-12 Actual", rooms_revenue=9_332_100.0, fb_revenue=3_216_620.0,
        total_revenue=13_796_340.0, dept_expenses=DepartmentalExpenses(), undistributed=UndistributedExpenses(),
        fixed_charges=FixedCharges(), gop=4_970_460.0, noi=1_794_100.0, opex_ratio=0.87,
        occupancy=0.716, adr=372.4, revpar=266.6,
    )
    base.update(over)
    return USALIFinancials(**base)


def test_plausibility_guard_reports_basis_mismatch_at_info() -> None:
    from app.agents.variance import VarianceBrokerField, _build_flags, is_basis_mismatch
    from app.api.analysis import VarianceFlagOut, consolidate_variance_flags

    # A percent that slipped past normalisation (83 vs 0.716) — 8,200% apart.
    flags = _build_flags(
        deal_uuid=uuid4(), actuals=_actuals(),
        broker_fields=[VarianceBrokerField(field="ttm_summary_per_om.occupancy_pct", value=83.0)],
    )
    assert len(flags) == 1
    f = flags[0]
    assert f.severity.value == "Info"
    assert f.note is not None and f.note.startswith("Basis mismatch — needs review")
    assert "83" in f.note and "0.716" in f.note
    assert is_basis_mismatch(f.delta_pct)

    out = consolidate_variance_flags(
        [VarianceFlagOut(field=f.field, rule_id=f.rule_id, severity=f.severity.value, actual=f.actual,
                         broker=f.broker, delta=f.delta, delta_pct=f.delta_pct, note=f.note, basis_mismatch=True)]
    )
    assert out[0].basis_mismatch is True
    assert out[0].severity == "Info"
    assert out[0].note.startswith("Basis mismatch — needs review")

    # A same-basis row for the same concept wins over the mismatch row.
    out = consolidate_variance_flags(
        [
            VarianceFlagOut(field="ttm_summary_per_om.occupancy_pct", severity="Critical", actual=0.716, broker=83.0,
                            delta=-82.284, delta_pct=82.284, basis_mismatch=True),
            VarianceFlagOut(field="broker_proforma.occupancy", severity="Warn", actual=0.716, broker=0.83,
                            delta=-0.114, delta_pct=0.114),
        ]
    )
    assert out[0].basis_mismatch is False
    assert out[0].broker == 0.83 and out[0].severity == "Warn"
    assert [r.basis_mismatch for r in out[0].raw_fields] == [True, False]


# ═══════════════════ Sam's deal — seeded end-to-end ═══════════════════


async def _seed_sams_deal(deal_id: UUID) -> None:
    """Five documents shaped like deal e577f547's live extractions."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    t0 = datetime.now(UTC) - timedelta(days=2)

    def ef(name: str, value: float, page: int = 1) -> dict:
        return {"field_name": name, "value": value, "source_page": page, "confidence": 0.8}

    docs = [
        # (filename, doc_type, created_at offset, fields)
        ("Copy of Miami Beach Anglers Offering Memorandum.pdf", "OM", 0, [
            ef("ttm_summary_per_om.rooms_revenue_usd", 9_541_537, 14),
            ef("ttm_summary_per_om.gop_usd", 5_088_268, 14),
            ef("ttm_summary_per_om.noi_usd", 3_356_709, 14),
            ef("ttm_summary_per_om.occupancy_pct", 83, 12),          # a PERCENT
            ef("ttm_performance.subject.adr_usd", 385, 12),
            ef("ttm_performance.subject.revpar_usd", 319, 12),
            ef("ttm_performance.segment.luxury_upper_upscale.occupancy_pct", 74.1, 20),
            ef("ttm_performance.segment.luxury_upper_upscale.adr_usd", 452.1, 20),
            ef("ttm_performance.segment.luxury_upper_upscale.revpar_usd", 335.0, 20),
            ef("p_and_l_usali.2021.gop_usd", 4_851_106, 16),
            ef("p_and_l_usali.2021.rooms_revenue_usd", 8_100_000, 16),
            ef("p_and_l_usali.2022.rooms_revenue_usd", 9_000_000, 16),
            ef("p_and_l_usali.2023.rooms_revenue_usd", 9_400_000, 16),
        ]),
        ("Copy of The Angler_s - March 2025 Financials.xlsx", "T12", 1, [
            # Monthly slices listed FIRST — the old last-segment bucketing let them win.
            ef("p_and_l_usali.monthly.apr_2024.rooms_revenue", 954_187, 2),
            ef("p_and_l_usali.monthly.apr_2024.gop", 514_931, 2),
            ef("p_and_l_usali.monthly.apr_2024.occupancy", 1, 2),
            ef("p_and_l_usali.monthly.apr_2024.adr", 300, 2),
            ef("occupancy_pct", 71.6, 1),                            # a PERCENT, flat
            ef("adr_usd", 372.4, 1),
            ef("revpar_usd", 266.6, 1),
            ef("p_and_l_usali.operating_revenue.rooms_revenue", 9_332_100, 2),
            ef("p_and_l_usali.operating_revenue.food_beverage_revenue", 3_216_620, 2),
            ef("p_and_l_usali.operating_revenue.total_revenue", 13_796_340, 2),
            ef("p_and_l_usali.gross_operating_profit", 4_970_460, 2),
            ef("p_and_l_usali.net_operating_income.noi_usd", 1_794_100, 2),
            ef("p_and_l_usali.net_operating_income.ebitda", 2_346_710, 2),
        ]),
        # The NEWEST extraction — a detailed P&L whose monthly rows used to win.
        ("Copy of Angler_s 2024 Full Year Detailed P&L.xlsm", "PNL", 3, [
            ef("p_and_l_usali.monthly.jan.rooms_revenue", 954_187, 1),
            ef("p_and_l_usali.monthly.jan.gop", 514_931, 1),
            ef("occupancy_pct", 70.2, 1),
            ef("ttm_summary_per_om.occupancy_pct", 70.2, 1),
            ef("p_and_l_usali.operating_revenue.rooms_revenue", 9_100_000, 1),
            ef("p_and_l_usali.gross_operating_profit", 4_800_000, 1),
        ]),
        ("Copy of Angler_s 2023 P&L.xlsx", "PNL", 2, [
            ef("ttm_summary_per_om.occupancy_pct", 72.0, 1),
            ef("p_and_l_usali.gop_usd", 4_736_470, 1),
            ef("p_and_l_usali.total_revenues_usd", 12_940_200, 1),
            ef("p_and_l_usali.rooms.revenue_usd", 9_000_000, 1),
        ]),
        ("Copy of Angler_s 2019 P&L.xlsx", "PNL", 2, [
            ef("ttm_performance.subject.occupancy", 1, 1),           # the bogus "1"
            ef("p_and_l_usali.gop", 1_912_060, 1),
            ef("p_and_l_usali.rooms.revenue", 6_339_940, 1),
            ef("p_and_l_usali.total_revenue", 8_385_990, 1),
        ]),
    ]

    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) VALUES (:id,:t,'Anglers (FON-54a)','Draft',0.0,:ts,:ts)"
            ),
            {"id": str(deal_id), "t": _TENANT, "ts": t0},
        )
        for fname, dtype, off, fields in docs:
            doc_id = uuid4()
            ts = t0 + timedelta(hours=off)
            await s.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
                    "status, uploaded_at) VALUES (:id,:deal,:t,:f,:dt,'EXTRACTED',:ts)"
                ),
                {"id": str(doc_id), "deal": str(deal_id), "t": _TENANT, "f": fname, "dt": dtype, "ts": ts},
            )
            await s.execute(
                text(
                    "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, "
                    "fields, confidence_report, agent_version, created_at) "
                    "VALUES (:id,:doc,:deal,:t,:f,'{}','v1',:ts)"
                ),
                {"id": str(uuid4()), "doc": str(doc_id), "deal": str(deal_id), "t": _TENANT,
                 "f": json.dumps(fields), "ts": ts},
            )
        await s.commit()


@pytest.mark.asyncio
async def test_critic_inputs_use_annual_t12_lines_and_normalised_units() -> None:
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id = uuid4()
    await _seed_sams_deal(deal_id)
    factory = get_session_factory()
    async with factory() as s:
        broker, actuals, _ctx, _keys = await _load_critic_inputs(s, deal_id=str(deal_id), tenant_id=_TENANT)

    assert actuals is not None and broker is not None
    # The monthly slices (954,187 / 514,931) never reach the actual side; the
    # T-12's annual lines win over the newer P&L's and over the 2019/2023 P&Ls.
    assert actuals.rooms_revenue == 9_332_100
    assert actuals.gop == 4_970_460
    assert actuals.noi == 1_794_100
    assert actuals.total_revenue == 13_796_340
    assert actuals.occupancy == pytest.approx(0.716)
    assert actuals.adr == pytest.approx(372.4)
    # Broker side: the OM's own TTM claim, units normalised; not its history.
    assert broker.rooms_revenue == 9_541_537
    assert broker.gop == 5_088_268
    assert broker.noi == 3_356_709
    assert broker.occupancy == pytest.approx(0.83)
    assert broker.adr == pytest.approx(385.0)


@pytest.mark.asyncio
async def test_variance_endpoint_on_sams_deal_compares_like_with_like() -> None:
    from app.api.analysis import get_variance
    from app.database import get_session_factory

    deal_id = uuid4()
    await _seed_sams_deal(deal_id)
    factory = get_session_factory()
    async with factory() as s:
        resp = await get_variance(deal_id=deal_id, session=s, tenant_id=UUID(_TENANT))

    assert resp.note is None, resp.note
    by = {f.concept: f for f in resp.flags}

    # Occupancy: 0.83 vs 0.716 — a fraction against a fraction, not 83 vs 1.
    occ = by["occupancy"]
    assert occ.broker == pytest.approx(0.83)
    assert occ.actual == pytest.approx(0.716)
    assert occ.delta_pct == pytest.approx(0.114, abs=1e-6)
    assert occ.basis_mismatch is False
    assert occ.unit_note == "occupancy 83% read as 0.830"

    # Rooms revenue / GOP / NOI: the OM's TTM claim vs the T-12's ANNUAL line.
    assert by["rooms_revenue"].actual == 9_332_100 and by["rooms_revenue"].broker == 9_541_537
    assert by["gop"].actual == 4_970_460 and by["gop"].broker == 5_088_268
    assert by["noi"].actual == 1_794_100 and by["noi"].broker == 3_356_709
    for f in resp.flags:
        assert f.actual not in (954_187, 514_931), f"monthly slice leaked into {f.concept}"

    # Every ADMITTED raw row is the OM's claim; nothing from a P&L / T-12, a
    # segment path or the OM's historical-year block feeds a flag.
    for f in resp.flags:
        for r in f.raw_fields:
            if r.excluded_reason:
                continue
            assert r.source_doc_type == "OM", (f.concept, r.field, r.source_doc_type)
            assert ".segment." not in r.field
            assert not any(p.isdigit() and len(p) == 4 for p in r.field.split(".")[:-1]), r.field
        assert f.source_doc_type == "OM"
    # …and the exclusions are disclosed with their reasons.
    excluded = [r for f in resp.flags for r in f.raw_fields if r.excluded_reason]
    assert any(r.source_doc_type == "PNL" and r.field == "p_and_l_usali.gop" for r in excluded)
    assert any(".segment." in r.field for r in excluded)
    assert any(r.field == "p_and_l_usali.2021.gop_usd" for r in excluded)
    assert all(r.source_document for r in excluded)

    # Plausibility: nothing beyond the guard survives as a severity-bearing flag.
    for f in resp.flags:
        if f.delta_pct is not None and abs(f.delta_pct) > 3.0:
            assert f.basis_mismatch and f.severity == "Info", f.concept
    assert resp.critical_count + resp.warn_count + resp.info_count == len(resp.flags)
