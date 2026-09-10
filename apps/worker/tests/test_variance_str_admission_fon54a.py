"""FON-54a (part 3) — STR / CoStar rows are never the broker's claim.

Live on Sam's deal after part 2: ``ttm_performance.subject.*`` rows from
STR_TREND documents (two CoStar *submarket* PDFs, two STR ``ANG-…-USD-E``
reports) were admitted as broker claims and one became the headline —
"broker understates occupancy by 11.8 pts" (0.7118 vs the T-12's 0.83) and
"broker ADR $288" — hiding the honest result that the OM's own claims are
within a few percent of the T-12.

Pins: in strict mode a claim path (``ttm_performance.subject.*``,
``ttm_summary_per_om.*``, flat known keys) is admitted ONLY from broker
material (OM); rows from STR / CBRE / CAPEX / INSURANCE / PROPERTY_INFO /
unknown documents are excluded with a doc-type-specific reason and stay
disclosed in ``raw_fields``. Explicit ``broker_proforma.*`` / ``broker.*``
paths remain the broker's claim wherever they sit.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_TENANT = "54a2cff3-6f9b-57a9-8d2a-5511f3dd9f7e"


def _ef(name: str, value: float, page: int = 1):
    from fondok_schemas import ExtractionField

    return ExtractionField(field_name=name, value=value, source_page=page, confidence=0.9)


# ═══════════════════ pure admission ═══════════════════


@pytest.mark.parametrize(
    "doc_type, expect_reason",
    [
        ("STR_TREND", "STR-reported"),
        ("STR", "STR-reported"),
        ("CBRE_HORIZONS", "market data"),
        ("CAPEX", "not broker material"),
        ("INSURANCE", "not broker material"),
        ("PROPERTY_INFO", "not broker material"),
        (None, "unknown"),
    ],
)
def test_claim_paths_admitted_only_from_broker_material(doc_type, expect_reason) -> None:
    from app.agents.variance import _broker_fields_from_extraction

    rejected: list = []
    out = _broker_fields_from_extraction(
        [
            _ef("ttm_performance.subject.occupancy_pct", 0.7118),
            _ef("ttm_performance.subject.adr_usd", 288.0),
            _ef("ttm_summary_per_om.occupancy_pct", 0.716),
            _ef("occupancy_pct", 0.716),
            _ef("broker_proforma.adr_usd", 251.89),  # explicit broker path → still the claim
        ],
        doc_type=doc_type, strict=True, excluded=rejected,
    )
    assert [b.field for b in out] == ["broker_proforma.adr_usd"]
    reasons = {f.field_name: r for (f, r) in rejected}
    assert set(reasons) == {
        "ttm_performance.subject.occupancy_pct",
        "ttm_performance.subject.adr_usd",
        "ttm_summary_per_om.occupancy_pct",
        "occupancy_pct",
    }
    assert all(expect_reason in r for r in reasons.values()), reasons
    assert all("not the broker's claim" in r for r in reasons.values()), reasons


def test_claim_paths_admitted_from_the_om() -> None:
    from app.agents.variance import _broker_fields_from_extraction

    out = _broker_fields_from_extraction(
        [
            _ef("ttm_summary_per_om.occupancy_pct", 0.831),
            _ef("ttm_performance.subject.adr_usd", 237.64),
            _ef("broker_proforma.occupancy_pct", 0.80),
            _ef("adr_usd", 251.89),
        ],
        doc_type="OM", strict=True,
    )
    assert [b.field for b in out] == [
        "ttm_summary_per_om.occupancy_pct",
        "ttm_performance.subject.adr_usd",
        "broker_proforma.occupancy_pct",
        "adr_usd",
    ]
    assert all(b.source_doc_type == "OM" for b in out)


# ═══════════════════ Sam's deal — the exact live rows ═══════════════════


async def _seed(deal_id: UUID) -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    t0 = datetime.now(UTC) - timedelta(days=1)

    def ef(name: str, value: float, page: int = 1) -> dict:
        return {"field_name": name, "value": value, "source_page": page, "confidence": 0.8}

    docs = [
        ("Copy of Miami Beach Anglers Offering Memorandum.pdf", "OM", 0, [
            ef("ttm_summary_per_om.occupancy_pct", 0.831, 12),
            ef("broker_proforma.occupancy_pct", 0.80, 30),
            ef("ttm_summary_per_om.adr_usd", 237.64, 12),
            ef("broker_proforma.adr_usd", 251.89, 30),
            ef("ttm_summary_per_om.rooms_revenue_usd", 9_541_537, 14),
            ef("ttm_summary_per_om.noi_usd", 3_356_709, 14),
        ]),
        ("Copy of The Angler_s - March 2025 Financials.xlsx", "T12", 1, [
            ef("occupancy_pct", 0.83, 1),
            ef("adr_usd", 232.77, 1),
            ef("p_and_l_usali.operating_revenue.rooms_revenue", 9_332_100, 2),
            ef("p_and_l_usali.operating_revenue.total_revenue", 13_796_340, 2),
            ef("p_and_l_usali.gross_operating_profit", 4_970_460, 2),
            ef("p_and_l_usali.net_operating_income.noi_usd", 1_794_100, 2),
        ]),
        # STR / CoStar documents — the newest extractions, all carrying the
        # ``ttm_performance.subject.*`` path the extractor uses for their headline.
        ("Miami Beach-Hospitality-Capital Submarket-2025-12-10.pdf", "STR_TREND", 4, [
            ef("ttm_performance.subject.occupancy_pct", 0.7118, 1),
            ef("ttm_performance.subject.adr_usd", 288.0, 1),
        ]),
        ("Miami Beach-Hospitality-Submarket-2025-06-01.pdf", "STR_TREND", 3, [
            ef("ttm_performance.subject.occupancy_pct", 0.716, 1),
        ]),
        ("Copy of ANG-20250500-USD-E.xlsx", "STR_TREND", 2, [
            ef("ttm_performance.subject.occupancy_pct", 0.829, 1),
        ]),
        ("Copy of ANG-20231200-USD-E.xlsx", "STR_TREND", 2, [
            ef("ttm_performance.subject.occupancy_pct", 0.824, 1),
        ]),
    ]

    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) VALUES (:id,:t,'Anglers (STR admission)','Draft',0.0,:ts,:ts)"
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
async def test_str_rows_never_headline_the_broker_flag() -> None:
    from app.api.analysis import get_variance
    from app.database import get_session_factory

    deal_id = uuid4()
    await _seed(deal_id)
    async with get_session_factory()() as s:
        resp = await get_variance(deal_id=deal_id, session=s, tenant_id=UUID(_TENANT))

    assert resp.note is None, resp.note
    by = {f.concept: f for f in resp.flags}
    str_occ = {0.7118, 0.716, 0.829, 0.824}

    # Occupancy: the headline is an OM row against the T-12's own 0.83 — never
    # an STR / CoStar figure. (Both OM claims are within ~3 pts of the T-12;
    # the consolidated primary is the higher-severity OM row.)
    occ = by["occupancy"]
    assert occ.source_doc_type == "OM"
    assert occ.actual == pytest.approx(0.83)
    assert occ.broker in (pytest.approx(0.831), pytest.approx(0.80))
    assert occ.broker not in str_occ
    assert abs(occ.delta_pct) <= 0.031  # pts — no "understates by 11.8 pts"
    admitted = [r for r in occ.raw_fields if not r.excluded_reason]
    assert {r.field for r in admitted} == {"ttm_summary_per_om.occupancy_pct", "broker_proforma.occupancy_pct"}
    assert all(r.source_doc_type == "OM" for r in admitted)
    excluded = [r for r in occ.raw_fields if r.excluded_reason]
    assert {r.source_document for r in excluded} >= {
        "Miami Beach-Hospitality-Capital Submarket-2025-12-10.pdf",
        "Miami Beach-Hospitality-Submarket-2025-06-01.pdf",
        "Copy of ANG-20250500-USD-E.xlsx",
        "Copy of ANG-20231200-USD-E.xlsx",
    }
    for r in excluded:
        if r.source_doc_type == "STR_TREND":
            assert "STR-reported" in r.excluded_reason and "not the broker's claim" in r.excluded_reason
            assert r.broker in str_occ

    # ADR: the OM's $237.64 / $251.89 against the T-12's $232.77 — not the
    # CoStar submarket $288.
    adr = by["adr"]
    assert adr.source_doc_type == "OM"
    assert adr.actual == pytest.approx(232.77)
    assert adr.broker in (pytest.approx(237.64), pytest.approx(251.89))
    assert adr.broker != 288.0
    assert {r.field for r in adr.raw_fields if not r.excluded_reason} == {
        "ttm_summary_per_om.adr_usd", "broker_proforma.adr_usd",
    }
    assert any(
        r.excluded_reason and r.source_doc_type == "STR_TREND" and r.broker == 288.0
        for r in adr.raw_fields
    )

    # Nothing admitted anywhere comes from an STR / CoStar document.
    for f in resp.flags:
        for r in f.raw_fields:
            if not r.excluded_reason:
                assert r.source_doc_type == "OM", (f.concept, r.field, r.source_doc_type)
