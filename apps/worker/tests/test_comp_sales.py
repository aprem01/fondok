"""Tests for the Comparable Sales engine (Wave 3 W3.1).

Twelve tests covering: median + weighted derivation, recency bucketing,
market + chain-scale matches, lookback filter, exclude-list,
coverage-quality, weighting-notes emission, fallback behaviour when no
subject metadata is supplied, and the two API endpoints.

The endpoint tests mount the full FastAPI app through ASGI so the
tenant-scope check + Depends(get_tenant_id) wiring is exercised
without standing up the worker process.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

# Per-test SQLite database BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-comp-sales.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


from app.engines.comp_sales import (  # noqa: E402
    W_CHAIN,
    W_MARKET,
    W_RECENCY,
    build_comp_set,
)
from fondok_schemas.comp_sales import CompTransaction  # noqa: E402


# ─────────────────────────── fixtures ────────────────────────────────


# A fixed "today" anchor so recency bucketing is deterministic across
# CI runs. Falls in mid-2026 to match the project's current calendar.
_TODAY = date(2026, 6, 28)


def _comp(
    *,
    transaction_id: str = "doc:1",
    property_name: str = "Test Hotel",
    city: str | None = None,
    state: str | None = None,
    sale_date: date | None = None,
    keys: int = 200,
    sale_price_usd: float = 50_000_000.0,
    sale_price_per_key_usd: float = 250_000.0,
    noi_usd: float | None = 3_750_000.0,
    cap_rate_pct: float | None = 7.5,
    chain_scale: str | None = "upper-upscale",
    brand_family: str | None = "Marriott",
    flag: str | None = None,
    source_document_id: str = "doc-1",
    source_page_number: int | None = 12,
) -> CompTransaction:
    return CompTransaction(
        property_name=property_name,
        city=city,
        state=state,
        sale_date=sale_date,
        keys=keys,
        sale_price_usd=sale_price_usd,
        sale_price_per_key_usd=sale_price_per_key_usd,
        noi_usd=noi_usd,
        cap_rate_pct=cap_rate_pct,
        chain_scale=chain_scale,
        brand_family=brand_family,
        flag=flag,
        source_document_id=source_document_id,
        source_page_number=source_page_number,
        transaction_id=transaction_id,
    )


# ─────────────────────────── unit tests ──────────────────────────────


def test_empty_set_returns_none_for_cap_rate() -> None:
    """No transactions → method=none, derived caps=None, coverage=low."""
    result = build_comp_set(
        deal_id="deal-1",
        transactions=[],
        today=_TODAY,
    )
    assert result.total_count == 0
    assert result.derived_cap_rate_median is None
    assert result.derived_cap_rate_weighted is None
    assert result.derived_cap_rate_method == "none"
    assert result.coverage_quality == "low"
    assert any("0 comps qualified" in n for n in result.weighting_notes)


def test_median_cap_rate_of_5_comps() -> None:
    """Median of [6.0, 6.5, 7.0, 7.5, 8.0] = 7.0."""
    rates = [6.0, 6.5, 7.0, 7.5, 8.0]
    txs = [
        _comp(
            transaction_id=f"doc:{i}",
            cap_rate_pct=r,
            sale_date=date(2025, 1, 1),
        )
        for i, r in enumerate(rates, start=1)
    ]
    result = build_comp_set(
        deal_id="deal-1",
        transactions=txs,
        today=_TODAY,
    )
    assert result.derived_cap_rate_median == pytest.approx(7.0)
    assert result.total_count == 5


def test_weighted_cap_rate_recency_dominates() -> None:
    """1 recent comp at 6.0% + 4 old comps at 8.0% → weighted < median.

    Recency carries 70% of the weight; a 2-yr-old comp gets full
    recency (1.0) while 5-yr-old comps get RECENCY_LE_6YR (0.4).
    Even without market/chain matches, the recent comp's vote
    carries more than its 1-in-5 fraction would suggest.
    """
    recent = _comp(
        transaction_id="doc:1",
        cap_rate_pct=6.0,
        sale_date=date(2025, 6, 1),  # ~1 yr from _TODAY → recency 1.0
    )
    # Use 4 yr old comps (within default 5-yr lookback) so they survive
    # the filter but land in the recency-0.7 bucket — the "old" tier
    # the test is exercising.
    old = [
        _comp(
            transaction_id=f"doc:{i}",
            cap_rate_pct=8.0,
            sale_date=date(2022, 8, 1),  # ~3.9 yrs old → recency 0.7
        )
        for i in range(2, 6)
    ]
    result = build_comp_set(
        deal_id="deal-1",
        transactions=[recent] + old,
        # Pass subject_chain_scale so the engine reports "weighted" method.
        subject_chain_scale="upper-upscale",
        today=_TODAY,
    )
    # Simple median is 8.0 (5 values, middle = 8.0).
    assert result.derived_cap_rate_median == pytest.approx(8.0)
    assert result.derived_cap_rate_weighted is not None
    assert result.derived_cap_rate_weighted < result.derived_cap_rate_median


def test_weighted_cap_rate_same_msa_outweighs_recency_when_close() -> None:
    """Verify the 0.7 / 0.2 / 0.1 weighting components.

    Construct two comps with identical recency + cap rate; one with
    same-MSA + same-chain-scale match, one with neither. The same-MSA
    + same-chain comp has weight = 0.7*1 + 0.2*1 + 0.1*1 = 1.0,
    versus 0.7*1 + 0*0.2 + 0*0.1 = 0.7. Weighted average leans toward
    its cap rate.
    """
    # Same-MSA + chain-scale comp at 6.5
    same = _comp(
        transaction_id="doc:1",
        cap_rate_pct=6.5,
        sale_date=date(2025, 6, 1),
        city="Houston",
        chain_scale="upper-upscale",
    )
    # Off-market + off-chain comp at 7.5
    diff = _comp(
        transaction_id="doc:2",
        cap_rate_pct=7.5,
        sale_date=date(2025, 6, 1),
        city="Boston",
        chain_scale="economy",
    )
    result = build_comp_set(
        deal_id="deal-1",
        transactions=[same, diff],
        subject_market="Houston, TX",
        subject_chain_scale="upper-upscale",
        today=_TODAY,
    )
    # Hand-compute: w_same=1.0, w_diff=0.7 → avg = (6.5*1 + 7.5*0.7) / 1.7
    expected = (6.5 * 1.0 + 7.5 * 0.7) / (1.0 + 0.7)
    assert result.derived_cap_rate_weighted == pytest.approx(expected, abs=1e-6)
    assert result.derived_cap_rate_method == "weighted"
    # And verify the component weights are what we documented.
    assert W_RECENCY == pytest.approx(0.7)
    assert W_MARKET == pytest.approx(0.2)
    assert W_CHAIN == pytest.approx(0.1)


def test_coverage_quality_thresholds() -> None:
    """8+ → high, 4-7 → medium, < 4 → low."""

    def _set(n: int) -> str:
        txs = [
            _comp(
                transaction_id=f"doc:{i}",
                cap_rate_pct=7.0,
                sale_date=date(2025, 1, 1),
            )
            for i in range(n)
        ]
        return build_comp_set(
            deal_id="deal-1", transactions=txs, today=_TODAY
        ).coverage_quality

    assert _set(0) == "low"
    assert _set(3) == "low"
    assert _set(4) == "medium"
    assert _set(7) == "medium"
    assert _set(8) == "high"
    assert _set(12) == "high"


def test_lookback_filter_excludes_old_comps() -> None:
    """Comp with sale_date > lookback_years old falls out + emits note."""
    fresh = _comp(
        transaction_id="doc:1",
        cap_rate_pct=7.0,
        sale_date=date(2024, 1, 1),
    )
    too_old = _comp(
        transaction_id="doc:2",
        cap_rate_pct=12.0,  # would drag the median up if included
        sale_date=date(2018, 1, 1),  # ~8 yrs before _TODAY
    )
    result = build_comp_set(
        deal_id="deal-1",
        transactions=[fresh, too_old],
        lookback_years=5,
        today=_TODAY,
    )
    assert result.total_count == 2  # raw count preserves both
    # Only the fresh comp survived → median = 7.0
    assert result.derived_cap_rate_median == pytest.approx(7.0)
    # The exclude note explicitly mentions the lookback policy.
    assert any("5 yrs old" in n for n in result.weighting_notes)


def test_excluded_transaction_ids_filtered_out() -> None:
    """Explicit analyst exclude removes a comp from derivation."""
    keep = _comp(
        transaction_id="doc:1",
        cap_rate_pct=6.0,
        sale_date=date(2025, 1, 1),
    )
    drop = _comp(
        transaction_id="doc:2",
        cap_rate_pct=9.0,
        sale_date=date(2025, 1, 1),
    )
    result = build_comp_set(
        deal_id="deal-1",
        transactions=[keep, drop],
        exclude_transaction_ids=["doc:2"],
        today=_TODAY,
    )
    # Only the kept comp contributed → median = 6.0
    assert result.derived_cap_rate_median == pytest.approx(6.0)
    assert any("excluded by analyst override" in n for n in result.weighting_notes)


def test_chain_scale_adjacent_match_half_weight() -> None:
    """Adjacent chain scales (upscale ↔ upper-upscale) → 0.5 chain-match.

    Set up a single comp where chain is adjacent to subject. Verify the
    weighted cap rate equals cap_rate (single comp), and that the
    note set + method reflect the weighted path was used.
    """
    adj = _comp(
        transaction_id="doc:1",
        cap_rate_pct=7.25,
        sale_date=date(2025, 6, 1),
        city="Houston",
        chain_scale="upscale",  # adjacent to upper-upscale
    )
    result = build_comp_set(
        deal_id="deal-1",
        transactions=[adj],
        subject_market="Houston, TX",
        subject_chain_scale="upper-upscale",
        today=_TODAY,
    )
    # Single comp → weighted = its cap_rate
    assert result.derived_cap_rate_weighted == pytest.approx(7.25)
    assert result.derived_cap_rate_method == "weighted"


def test_weighting_notes_emitted_for_each_filter() -> None:
    """Each filter decision (exclude, lookback, missing cap) emits a note."""
    txs = [
        # Excluded by analyst
        _comp(
            transaction_id="doc:1",
            cap_rate_pct=6.0,
            sale_date=date(2025, 1, 1),
        ),
        # Too old
        _comp(
            transaction_id="doc:2",
            cap_rate_pct=8.0,
            sale_date=date(2018, 1, 1),
        ),
        # Missing cap rate
        _comp(
            transaction_id="doc:3",
            cap_rate_pct=None,
            sale_date=date(2025, 1, 1),
        ),
        # Qualifies
        _comp(
            transaction_id="doc:4",
            cap_rate_pct=7.0,
            sale_date=date(2025, 1, 1),
        ),
    ]
    result = build_comp_set(
        deal_id="deal-1",
        transactions=txs,
        exclude_transaction_ids=["doc:1"],
        today=_TODAY,
    )
    notes_joined = " | ".join(result.weighting_notes)
    assert "excluded by analyst" in notes_joined
    assert "5 yrs old" in notes_joined
    assert "no cap rate published" in notes_joined


def test_method_falls_back_to_median_when_no_subject_market_or_chain() -> None:
    """No subject metadata → method=median (weighted not reportable)."""
    txs = [
        _comp(
            transaction_id=f"doc:{i}",
            cap_rate_pct=7.0,
            sale_date=date(2025, 1, 1),
        )
        for i in range(3)
    ]
    result = build_comp_set(
        deal_id="deal-1",
        transactions=txs,
        subject_market=None,
        subject_chain_scale=None,
        today=_TODAY,
    )
    assert result.derived_cap_rate_method == "median"
    # The note explains why.
    assert any("recency-only" in n.lower() for n in result.weighting_notes)


# ─────────────────────────── endpoint tests ──────────────────────────


@pytest.mark.asyncio
async def test_endpoint_tenant_scoped() -> None:
    """GET /deals/{deal_id}/comp-sales rejects cross-tenant requests."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant_a = uuid4()
    tenant_b = uuid4()
    deal_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, "
                "purchase_price, service, status, deal_stage, risk, "
                "ai_confidence, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Test Comp Hotel', 'Houston', "
                "200, 50000000, 'Full Service', 'Draft', 'Teaser', "
                "'Medium', 0.8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(deal_id), "tenant": str(tenant_a)},
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Tenant B reaches into tenant A's deal → 404
        r_cross = await client.get(
            f"/deals/{deal_id}/comp-sales",
            headers={"X-Tenant-Id": str(tenant_b)},
        )
        assert r_cross.status_code == 404, (
            f"expected 404 for cross-tenant request, got "
            f"{r_cross.status_code} body={r_cross.text}"
        )
        # Tenant A on its own deal must NOT 404.
        r_same = await client.get(
            f"/deals/{deal_id}/comp-sales",
            headers={"X-Tenant-Id": str(tenant_a)},
        )
        assert r_same.status_code != 404, (
            f"endpoint must not 404 on the owning tenant: status="
            f"{r_same.status_code} body={r_same.text[:300]}"
        )


@pytest.mark.asyncio
async def test_endpoint_returns_full_comp_set() -> None:
    """GET /deals/{deal_id}/comp-sales returns a CompSalesSet JSON body.

    Inserts a deal + a single OM extraction row with a comparable_sales.*
    transaction, hits the endpoint, and asserts the response carries
    the expected fields + a non-None derived cap rate.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant_id = uuid4()
    deal_id = uuid4()
    document_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, "
                "purchase_price, service, status, deal_stage, risk, "
                "ai_confidence, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Subject Hotel', 'Houston', 200, "
                "60000000, 'Full Service', 'Draft', 'Teaser', 'Medium', "
                "0.8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                "doc_type, status, storage_key, size_bytes) "
                "VALUES (:id, :deal, :tenant, 'om.pdf', 'OM', 'EXTRACTED', "
                "'om.pdf', 100)"
            ),
            {
                "id": str(document_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
        # Three comp rows under the new namespace. Use sale_dates that
        # fall inside the default 5-yr lookback (today is the system
        # clock — pick 2024/2025 to be safe well into 2027).
        import json as _json
        fields = [
            {"field_name": "comparable_sales.1.property_name",
             "value": "Hyatt Regency Houston Galleria", "page_number": 14},
            {"field_name": "comparable_sales.1.city",
             "value": "Houston", "page_number": 14},
            {"field_name": "comparable_sales.1.state",
             "value": "TX", "page_number": 14},
            {"field_name": "comparable_sales.1.sale_date",
             "value": "2025-02-03", "page_number": 14},
            {"field_name": "comparable_sales.1.keys",
             "value": 325, "page_number": 14},
            {"field_name": "comparable_sales.1.sale_price_usd",
             "value": 78000000, "page_number": 14},
            {"field_name": "comparable_sales.1.cap_rate_pct",
             "value": 6.85, "page_number": 14},
            {"field_name": "comparable_sales.1.chain_scale",
             "value": "upper-upscale", "page_number": 14},
            {"field_name": "comparable_sales.2.property_name",
             "value": "Marriott Memphis Downtown", "page_number": 15},
            {"field_name": "comparable_sales.2.city",
             "value": "Memphis", "page_number": 15},
            {"field_name": "comparable_sales.2.state",
             "value": "TN", "page_number": 15},
            {"field_name": "comparable_sales.2.sale_date",
             "value": "2024-08-15", "page_number": 15},
            {"field_name": "comparable_sales.2.keys",
             "value": 600, "page_number": 15},
            {"field_name": "comparable_sales.2.cap_rate_pct",
             "value": 7.5, "page_number": 15},
            {"field_name": "comparable_sales.2.chain_scale",
             "value": "upper-upscale", "page_number": 15},
            {"field_name": "comparable_sales.3.property_name",
             "value": "Hilton Austin", "page_number": 16},
            {"field_name": "comparable_sales.3.city",
             "value": "Austin", "page_number": 16},
            {"field_name": "comparable_sales.3.state",
             "value": "TX", "page_number": 16},
            {"field_name": "comparable_sales.3.sale_date",
             "value": "2024-04-10", "page_number": 16},
            {"field_name": "comparable_sales.3.keys",
             "value": 800, "page_number": 16},
            {"field_name": "comparable_sales.3.cap_rate_pct",
             "value": 7.0, "page_number": 16},
            {"field_name": "comparable_sales.3.chain_scale",
             "value": "upper-upscale", "page_number": 16},
        ]
        await session.execute(
            text(
                "INSERT INTO extraction_results (id, deal_id, document_id, "
                "tenant_id, fields) "
                "VALUES (:id, :deal, :doc, :tenant, :fields)"
            ),
            {
                "id": str(uuid4()),
                "deal": str(deal_id),
                "doc": str(document_id),
                "tenant": str(tenant_id),
                "fields": _json.dumps(fields),
            },
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/comp-sales",
            headers={"X-Tenant-Id": str(tenant_id)},
        )
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"
        body = r.json()
        # Structural assertions: every required key is present.
        assert body["deal_id"] == str(deal_id)
        assert body["total_count"] == 3
        assert len(body["transactions"]) == 3
        # Median of [6.85, 7.0, 7.5] = 7.0
        assert body["derived_cap_rate_median"] == pytest.approx(7.0, abs=0.01)
        # Coverage label is "low" (only 3 comps).
        assert body["coverage_quality"] == "low"
        # Each transaction carries source-doc provenance.
        for t in body["transactions"]:
            assert t["source_document_id"] == str(document_id)
            assert t["transaction_id"] is not None
