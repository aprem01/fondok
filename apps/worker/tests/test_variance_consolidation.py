"""FON-54a — variance flags are consolidated by business concept.

Sam's finding (FON-54): "multiple duplicate/machine-named revenue variance
flags with implausibly large variances". Root cause: the variance agent
admits every ``broker_proforma.*`` / ``broker.*`` path *and* any flat key in
its rule table, so one T-12 line became three IC-facing flags titled by raw
extractor paths. These tests pin the consolidation contract on
``GET /analysis/{id}/variance``:

* three raw paths for one concept → ONE flag, ``raw_fields`` keeps all three;
* consolidated ``severity`` = max across the merged rows;
* ``impact_basis`` is ``"noi"`` ONLY for the NOI / GOP concepts — a revenue
  or expense line is never an NOI impact;
* ``concept_label`` is business-readable; ``field`` is the concept key (no
  raw dotted path survives as an IC-facing title);
* severity counts on the response match the consolidated list.

Pure-function tests plus one endpoint-level test that seeds real extraction
rows through the same SQLite path the other API tests use.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_TENANT = "54a0cff3-6f9b-57a9-8d2a-5511f3dd9f7e"


def _flag(field: str, severity: str, *, broker: float, actual: float, rule: str = "BROKER_VS_T12_NOI_VARIANCE", page: int | None = None, ratio: bool = False):
    """Shape a raw row the way ``get_variance`` does from ``_build_flags``:
    ``delta = actual - broker``; ``delta_pct`` is percent-of-actual, except for
    ratio fields (occupancy) where the agent emits absolute points."""
    from app.api.analysis import VarianceFlagOut

    delta = actual - broker
    return VarianceFlagOut(
        field=field,
        rule_id=rule,
        severity=severity,
        actual=actual,
        broker=broker,
        delta=delta,
        delta_pct=abs(delta) if ratio else (delta / abs(actual) if actual else None),
        source_page=page,
        note=f"{field}: broker={broker:,.2f} vs actual={actual:,.2f}; rule {rule}",
    )


# ═══════════════════ pure-function tests ═══════════════════


def test_three_raw_paths_become_one_flag_with_raw_fields() -> None:
    from app.api.analysis import consolidate_variance_flags

    flags = [
        _flag("broker_proforma.rooms_revenue_usd", "Warn", broker=12_900_000, actual=12_300_000, page=14),
        _flag("broker.rooms_revenue", "Critical", broker=12_950_000, actual=12_300_000),
        _flag("rooms_revenue_usd", "Info", broker=12_400_000, actual=12_300_000),
    ]
    out = consolidate_variance_flags(flags)

    assert len(out) == 1
    f = out[0]
    assert f.concept == "rooms_revenue"
    assert f.field == "rooms_revenue"  # no raw dotted path as the IC-facing key
    assert f.concept_label == "Rooms revenue"
    assert [r.field for r in f.raw_fields] == [
        "broker_proforma.rooms_revenue_usd",
        "broker.rooms_revenue",
        "rooms_revenue_usd",
    ]
    # Each raw row keeps its own numbers + rule for the Technical detail.
    assert f.raw_fields[0].broker == 12_900_000
    assert f.raw_fields[0].source_page == 14
    assert all(r.rule_id == "BROKER_VS_T12_NOI_VARIANCE" for r in f.raw_fields)
    # The consolidated flag inherits the page from a raw row that has one.
    assert f.source_page == 14
    # Business-readable note, never the raw path.
    assert f.note is not None
    assert f.note.startswith("Rooms revenue: broker proforma $12,950,000 vs T-12 actual $12,300,000")
    assert "broker overstates the T-12 by 5.3%" in f.note
    assert "Consolidated from 3 broker fields" in f.note
    assert "broker_proforma." not in f.note


def test_severity_is_max_across_merged_rows() -> None:
    from app.api.analysis import consolidate_variance_flags

    flags = [
        _flag("broker_proforma.rooms_revenue_usd", "Info", broker=12_400_000, actual=12_300_000),
        _flag("broker.rooms_revenue", "Warn", broker=12_900_000, actual=12_300_000),
        _flag("rooms_revenue_usd", "Critical", broker=12_950_000, actual=12_300_000),
    ]
    out = consolidate_variance_flags(flags)
    assert len(out) == 1
    assert out[0].severity == "Critical"
    # The primary numbers come from the highest-severity row.
    assert out[0].broker == 12_950_000

    # Severity comparison is case-insensitive (title-case enum vs lowercase).
    lower = [
        _flag("broker_proforma.noi_usd", "warn", broker=5_000_000, actual=4_181_000),
        _flag("noi", "critical", broker=5_200_000, actual=4_181_000),
    ]
    assert consolidate_variance_flags(lower)[0].severity == "critical"


def test_impact_basis_noi_only_for_noi_and_gop() -> None:
    from app.api.analysis import consolidate_variance_flags

    flags = [
        _flag("broker_proforma.noi_usd", "Critical", broker=5_200_000, actual=4_181_000),
        _flag("broker_proforma.gop_usd", "Warn", broker=7_000_000, actual=6_500_000, rule="GOP_MARGIN_RANGE"),
        _flag("broker_proforma.rooms_revenue_usd", "Critical", broker=12_900_000, actual=12_300_000),
        _flag("broker_proforma.total_revenue_usd", "Critical", broker=21_000_000, actual=20_000_000),
        _flag("broker_proforma.insurance_usd", "Warn", broker=502_000, actual=700_000, rule="INSURANCE_PER_KEY"),
        _flag("broker_proforma.occupancy_pct", "Warn", broker=0.80, actual=0.762, rule="BROKER_VS_T12_OCC_VARIANCE", ratio=True),
    ]
    by_concept = {f.concept: f for f in consolidate_variance_flags(flags)}

    assert by_concept["noi"].impact_basis == "noi"
    assert by_concept["gop"].impact_basis == "noi"
    # A revenue-line delta is NOT an NOI impact (the "implausibly large" bug).
    assert by_concept["rooms_revenue"].impact_basis == "revenue"
    assert by_concept["total_revenue"].impact_basis == "revenue"
    assert by_concept["occupancy"].impact_basis == "revenue"
    assert by_concept["insurance"].impact_basis == "expense"
    assert by_concept["noi"].concept_label == "NOI"
    assert by_concept["insurance"].concept_label == "Insurance"
    # Ratio concepts narrate in points, not percent-of-actual.
    assert "3.8 pts" in (by_concept["occupancy"].note or "")


def test_market_forecast_and_off_catalog_rows_pass_through() -> None:
    from app.api.analysis import VarianceFlagOut, consolidate_variance_flags

    market = VarianceFlagOut(
        field="broker_adr_growth_vs_market",
        rule_id="BROKER_VS_CBRE_ADR_GROWTH",
        severity="Warn",
        actual=0.03,
        broker=0.06,
        delta=0.03,
        delta_pct=0.03,
        note="Broker projects 6.0% Y1 ADR growth vs CBRE published submarket forecast of 3.0% (+3.0%)",
    )
    unknown = _flag("broker_proforma.parking_income_usd", "Info", broker=120.0, actual=100.0)
    out = consolidate_variance_flags([market, unknown])

    assert out[0].concept == "broker_adr_growth_vs_market"
    assert out[0].concept_label == "ADR growth vs. market forecast"
    assert out[0].impact_basis == "other"
    assert out[0].note == market.note  # market rows narrate themselves
    assert len(out[0].raw_fields) == 1

    # Off-catalog concept: readable fallback label, ``other`` basis (never noi).
    assert out[1].concept == "parking_income"
    assert out[1].concept_label == "Parking income"
    assert out[1].impact_basis == "other"


def test_output_order_is_first_seen_and_deterministic() -> None:
    from app.api.analysis import consolidate_variance_flags

    flags = [
        _flag("broker_proforma.total_revenue_usd", "Info", broker=20_100_000, actual=20_000_000),
        _flag("broker_proforma.noi_usd", "Critical", broker=5_200_000, actual=4_181_000),
        _flag("broker.total_revenue", "Warn", broker=21_000_000, actual=20_000_000),
    ]
    a = consolidate_variance_flags(flags)
    b = consolidate_variance_flags(list(flags))
    assert [f.concept for f in a] == ["total_revenue", "noi"]
    assert [f.model_dump() for f in a] == [f.model_dump() for f in b]


def test_variance_concept_normalises_prefixes_and_suffixes() -> None:
    from app.api.analysis import variance_concept

    assert variance_concept("broker_proforma.rooms_revenue_usd") == "rooms_revenue"
    assert variance_concept("broker.rooms_revenue") == "rooms_revenue"
    assert variance_concept("rooms_revenue_usd") == "rooms_revenue"
    assert variance_concept("broker_proforma.occupancy_pct") == "occupancy"
    assert variance_concept("NOI") == "noi"
    assert variance_concept("broker_adr_growth_vs_market") == "broker_adr_growth_vs_market"


def test_response_shape_is_additive_for_old_callers() -> None:
    """Old constructors (no concept fields) still validate; new fields default."""
    from app.api.analysis import VarianceFlagOut

    f = VarianceFlagOut(field="occupancy", severity="info")
    assert f.concept is None and f.concept_label is None and f.impact_basis is None
    assert f.raw_fields == []


# ═══════════════════ endpoint-level test (seeded SQLite) ═══════════════════


async def _seed_deal_with_duplicate_broker_paths(deal_id: UUID) -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as s:
        ts = datetime.now(UTC)
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) VALUES (:id,:t,'FON-54a deal','Draft',0.0,:ts,:ts)"
            ),
            {"id": str(deal_id), "t": _TENANT, "ts": ts},
        )
        t12_doc, om_doc = uuid4(), uuid4()
        for doc_id, fname, dtype in ((t12_doc, "t12.xlsx", "T12"), (om_doc, "om.pdf", "OM")):
            await s.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
                    "status, uploaded_at) VALUES (:id,:deal,:t,:f,:dt,'EXTRACTED',:ts)"
                ),
                {"id": str(doc_id), "deal": str(deal_id), "t": _TENANT, "f": fname, "dt": dtype, "ts": ts},
            )
        # Rows are shaped like real ``ExtractionField`` output (``source_page``
        # + ``confidence`` are required — the endpoint validates each row).
        def ef(name: str, value: float, page: int = 1) -> dict:
            return {"field_name": name, "value": value, "source_page": page, "confidence": 0.9}

        t12_fields = [
            ef("rooms_revenue", 12_300_000.0),
            ef("total_revenue", 20_000_000.0),
            ef("noi", 4_181_000.0),
            ef("occupancy", 0.762),
            ef("adr", 250.0),
        ]
        # The three ways the extractor has emitted ONE broker rooms-revenue
        # line, plus NOI + total revenue — the exact shape of Sam's finding.
        om_fields = [
            ef("broker_proforma.rooms_revenue_usd", 12_900_000.0, page=14),
            ef("broker.rooms_revenue", 12_950_000.0, page=15),
            ef("rooms_revenue_usd", 12_400_000.0, page=16),
            ef("broker_proforma.noi_usd", 5_200_000.0, page=14),
            ef("broker_proforma.total_revenue_usd", 21_000_000.0, page=14),
        ]
        for doc_id, fields in ((t12_doc, t12_fields), (om_doc, om_fields)):
            await s.execute(
                text(
                    "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, "
                    "fields, confidence_report, agent_version, created_at) "
                    "VALUES (:id,:doc,:deal,:t,:f,'{}','v1',:ts)"
                ),
                {
                    "id": str(uuid4()),
                    "doc": str(doc_id),
                    "deal": str(deal_id),
                    "t": _TENANT,
                    "f": json.dumps(fields),
                    "ts": ts,
                },
            )
        await s.commit()


@pytest.mark.asyncio
async def test_get_variance_endpoint_consolidates_duplicate_broker_paths() -> None:
    from app.api.analysis import get_variance
    from app.database import get_session_factory

    deal_id = uuid4()
    await _seed_deal_with_duplicate_broker_paths(deal_id)

    factory = get_session_factory()
    async with factory() as s:
        resp = await get_variance(deal_id=deal_id, session=s, tenant_id=UUID(_TENANT))

    assert resp.note is None, resp.note
    assert resp.flags, "expected consolidated flags"

    concepts = [f.concept for f in resp.flags]
    assert len(concepts) == len(set(concepts)), f"duplicate concepts: {concepts}"
    # No raw dotted extractor path survives as an IC-facing field.
    assert all("." not in f.field for f in resp.flags), [f.field for f in resp.flags]
    assert all(f.concept_label and f.impact_basis for f in resp.flags)

    rooms = next(f for f in resp.flags if f.concept == "rooms_revenue")
    admitted = [r for r in rooms.raw_fields if not r.excluded_reason]
    assert {r.field for r in admitted} == {
        "broker_proforma.rooms_revenue_usd",
        "broker.rooms_revenue",
        "rooms_revenue_usd",
    }
    assert all(r.source_doc_type == "OM" for r in admitted)
    # FON-54a input honesty: the T-12's own flat ``rooms_revenue`` line is a
    # candidate by name but NOT a broker claim — disclosed as excluded.
    excluded = [r for r in rooms.raw_fields if r.excluded_reason]
    assert [(r.field, r.source_doc_type) for r in excluded] == [("rooms_revenue", "T12")]
    assert "actuals document" in (excluded[0].excluded_reason or "")
    assert rooms.impact_basis == "revenue"
    assert rooms.concept_label == "Rooms revenue"
    rank = {"critical": 2, "warn": 1, "info": 0}
    assert rank[rooms.severity.lower()] == max(rank[r.severity.lower()] for r in rooms.raw_fields)
    # The consolidated page is one of the raw rows' pages (never invented).
    assert rooms.source_page in {r.source_page for r in rooms.raw_fields}

    noi = next(f for f in resp.flags if f.concept == "noi")
    assert noi.impact_basis == "noi"
    assert len([r for r in noi.raw_fields if not r.excluded_reason]) == 1
    # The T-12's own ``noi`` line is disclosed as excluded, never compared.
    assert [(r.field, r.source_doc_type) for r in noi.raw_fields if r.excluded_reason] == [("noi", "T12")]

    # Severity counts are taken from the consolidated list (case-insensitive).
    assert resp.critical_count + resp.warn_count + resp.info_count == len(resp.flags)
    assert resp.critical_count == sum(1 for f in resp.flags if f.severity.lower() == "critical")
    assert resp.warn_count == sum(1 for f in resp.flags if f.severity.lower() == "warn")
