"""Tests for the document-coverage / gap-detection service.

Backs roadmap item #7 — Sam's June 25 framing: "If I have financials
from 2019 to 2025 but I'm missing detailed for 2024 to 2025, only
summary — that's a gap I'd want Fondok to flag."

Each test seeds the documents + extraction_results tables for a synth
deal, calls ``audit_document_coverage`` directly, and asserts the
gap list matches the scenario. We pin ``current_year`` on every call so
the tests stay deterministic across calendar boundaries.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Per-test SQLite DB before any app modules import (mirrors the pattern
# from test_dossier / test_engine_runner — the cached Settings object
# resolves DATABASE_URL exactly once at first import).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-coverage-audit.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


_TENANT_A = "00000000-0000-0000-0000-000000000001"
_TENANT_B = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "extraction_results",
            "documents",
            "deals",
        ):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


# ─────────────────────────── seed helpers ───────────────────────────


async def _seed_deal(deal_id: UUID, *, tenant_id: str = _TENANT_A) -> None:
    """Insert a minimal deal row so document FK lookups succeed."""
    from app.database import get_session_factory

    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Onboarding', :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": tenant_id,
                "name": f"Test Deal {deal_id}",
                "ts": now,
            },
        )
        await session.commit()


async def _seed_pnl(
    deal_id: UUID,
    *,
    tenant_id: str = _TENANT_A,
    doc_type: str,
    period_type: str,
    period_ending: str,
) -> UUID:
    """Insert one EXTRACTED P&L doc + its extraction_results row.

    Returns the document id. ``period_ending`` is an ISO date string
    (``YYYY-MM-DD``); ``period_type`` is one of the values the Extractor
    emits (``annual``, ``trailing_twelve``, ``ytd``, ``monthly``, ...).
    """
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    er_id = uuid4()
    now = datetime.now(UTC)

    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, parser, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, :filename, :doc_type,
                    'EXTRACTED', :ts, 1, 'test', NULL
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "filename": f"{doc_type}_{period_ending}.pdf",
                "doc_type": doc_type,
                "ts": now,
            },
        )
        # The minimum-viable fields list: period_type + period_ending.
        # Real extractions also carry the USALI lines, but the coverage
        # audit only needs the metadata.
        fields = [
            {
                "field_name": "p_and_l_usali.period_type",
                "value": period_type,
                "source_page": 1,
                "confidence": 0.95,
            },
            {
                "field_name": "p_and_l_usali.period_ending",
                "value": period_ending,
                "source_page": 1,
                "confidence": 0.95,
            },
        ]
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
                "id": str(er_id),
                "doc": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "fields": json.dumps(fields),
                "ts": now,
            },
        )
        await session.commit()
    return doc_id


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_sequential_gap_across_lookback_window() -> None:
    """Years 2019, 2020, 2022-2025 present, 2021 missing — the audit
    flags exactly one ``year_missing`` gap for 2021. (Sam's example.)"""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    for year in (2019, 2020, 2022, 2023, 2024, 2025):
        await _seed_pnl(
            deal_id,
            doc_type="T12",
            period_type="annual",
            period_ending=f"{year}-12-31",
        )

    factory = get_session_factory()
    async with factory() as session:
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    sequential = [g for g in coverage.gaps if g.gap_type == "year_missing"]
    assert [g.year for g in sequential] == [2021], (
        f"expected exactly 2021 missing, got {[g.year for g in sequential]}"
    )
    assert sequential[0].message == "Missing 2021 financials"
    assert sequential[0].severity == "warn"
    # All annual entries close on Dec 31 → calendar FY → not dismissible.
    assert sequential[0].dismissible is False


@pytest.mark.asyncio
async def test_annual_no_detail_flag() -> None:
    """2024 has an annual T-12 but zero monthlies → ``annual_no_detail``
    gap surfaces. (Sam's exact ask.)"""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    # Five years of full annual coverage; no monthly detail anywhere.
    for year in (2020, 2021, 2022, 2023, 2024):
        await _seed_pnl(
            deal_id,
            doc_type="T12",
            period_type="annual",
            period_ending=f"{year}-12-31",
        )

    factory = get_session_factory()
    async with factory() as session:
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    annual_no_detail = [
        g for g in coverage.gaps if g.gap_type == "annual_no_detail"
    ]
    # Every covered year flags annual_no_detail (each has annual but no
    # monthly). The product copy mentions 2024 explicitly because that's
    # Sam's framing, but the same logic applies year-over-year.
    assert {g.year for g in annual_no_detail} == {2020, 2021, 2022, 2023, 2024}
    for g in annual_no_detail:
        assert g.severity == "info"
        assert "no monthly breakdown" in g.message


@pytest.mark.asyncio
async def test_partial_monthly_coverage() -> None:
    """Jan-Oct 2024 monthly P&Ls present, Nov-Dec missing → one
    ``month_partial`` gap with ``months_missing == [11, 12]``."""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    # Annual for 2024 too so we don't ALSO see annual_no_detail —
    # we want a clean assertion on the partial-month case.
    await _seed_pnl(
        deal_id,
        doc_type="T12",
        period_type="annual",
        period_ending="2024-12-31",
    )
    for month in range(1, 11):  # Jan-Oct
        await _seed_pnl(
            deal_id,
            doc_type="PNL_MONTHLY",
            period_type="monthly",
            period_ending=f"2024-{month:02d}-28",
        )

    factory = get_session_factory()
    async with factory() as session:
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    month_gaps = [g for g in coverage.gaps if g.gap_type == "month_partial"]
    assert len(month_gaps) == 1
    assert month_gaps[0].year == 2024
    assert month_gaps[0].months_missing == [11, 12]
    assert month_gaps[0].severity == "warn"


@pytest.mark.asyncio
async def test_no_gaps_when_contiguous_with_full_monthly() -> None:
    """Five contiguous years of annual + 12 months for the most recent
    closed year → no gap flags at all."""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    # Annual coverage 2020-2024 + full monthly for every year.
    for year in (2020, 2021, 2022, 2023, 2024):
        await _seed_pnl(
            deal_id,
            doc_type="T12",
            period_type="annual",
            period_ending=f"{year}-12-31",
        )
        for month in range(1, 13):
            await _seed_pnl(
                deal_id,
                doc_type="PNL_MONTHLY",
                period_type="monthly",
                period_ending=f"{year}-{month:02d}-28",
            )

    factory = get_session_factory()
    async with factory() as session:
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    # 2025 is the current year and was deliberately not seeded — the
    # sequential-gap detector flags it because it falls inside the
    # window and has zero coverage.
    sequential = [g for g in coverage.gaps if g.gap_type == "year_missing"]
    assert {g.year for g in sequential} == {2025}
    # No annual_no_detail / month_partial / summary_only flags should
    # land for 2020-2024 — they have full annual + full monthly.
    other_gap_types = {
        g.gap_type for g in coverage.gaps if g.gap_type != "year_missing"
    }
    assert other_gap_types == set(), (
        f"unexpected non-sequential gaps: "
        f"{[(g.gap_type, g.year) for g in coverage.gaps]}"
    )


@pytest.mark.asyncio
async def test_non_calendar_fiscal_year_is_dismissible() -> None:
    """When every annual entry closes on a non-December month (e.g.
    June 30), the deal is on a non-calendar fiscal year and every gap
    flips to ``dismissible=True`` per the Wave 1 product decision."""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    # Fiscal years ending June 30 for FY2021..FY2024 — explicitly skip
    # FY2022 so we get a sequential gap to test dismissibility.
    for fy in (2021, 2023, 2024):
        await _seed_pnl(
            deal_id,
            doc_type="T12",
            period_type="annual",
            period_ending=f"{fy}-06-30",
        )

    factory = get_session_factory()
    async with factory() as session:
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    # All gaps — sequential AND detail — should be dismissible.
    assert coverage.gaps, "expected at least one gap to verify dismissibility"
    assert all(g.dismissible for g in coverage.gaps), (
        f"expected every gap dismissible on non-calendar FY, got: "
        f"{[(g.gap_type, g.year, g.dismissible) for g in coverage.gaps]}"
    )


@pytest.mark.asyncio
async def test_tenant_scoping_blocks_cross_tenant_docs() -> None:
    """A document uploaded under tenant B must NOT appear in tenant A's
    coverage rollup. Belt + braces against the P0 leak fixed in
    ``2a8ed64``."""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id, tenant_id=_TENANT_A)
    # Two annual T-12s — one in each tenant. Tenant A should see only
    # the 2024 doc; tenant B's 2023 doc must be filtered out.
    await _seed_pnl(
        deal_id,
        tenant_id=_TENANT_A,
        doc_type="T12",
        period_type="annual",
        period_ending="2024-12-31",
    )
    await _seed_pnl(
        deal_id,
        tenant_id=_TENANT_B,
        doc_type="T12",
        period_type="annual",
        period_ending="2023-12-31",
    )

    factory = get_session_factory()
    async with factory() as session:
        coverage_a = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    # Tenant A sees only 2024 in year_coverage — never 2023.
    assert set(coverage_a.year_coverage.keys()) == {2024}, (
        f"tenant A leaked tenant B docs: {coverage_a.year_coverage}"
    )

    async with factory() as session:
        coverage_b = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_B,
            current_year=2025,
        )
    # And the reverse — tenant B sees only 2023.
    assert set(coverage_b.year_coverage.keys()) == {2023}, (
        f"tenant B leaked tenant A docs: {coverage_b.year_coverage}"
    )


@pytest.mark.asyncio
async def test_lookback_override_shrinks_window() -> None:
    """Passing ``lookback_years=2`` should trim the sequential-gap
    window so gaps before the window are NOT flagged."""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    # Cover 2019 and 2024 only.
    for year in (2019, 2024):
        await _seed_pnl(
            deal_id,
            doc_type="T12",
            period_type="annual",
            period_ending=f"{year}-12-31",
        )

    factory = get_session_factory()
    async with factory() as session:
        # Default 5-year window: 2020-2024 all flagged as missing
        # (except 2024 which is covered) — that's 2020, 2021, 2022, 2023,
        # plus 2025 = 5 gaps. But we explicitly trim to 2.
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            lookback_years=2,
            current_year=2025,
        )

    # With lookback_years=2, the window is 2023..2025. Of those, 2024
    # is covered → 2023 and 2025 are flagged. 2019-2022 sit outside the
    # window and must NOT appear.
    sequential_years = {
        g.year for g in coverage.gaps if g.gap_type == "year_missing"
    }
    assert sequential_years == {2023, 2025}, (
        f"lookback override didn't trim window: got {sequential_years}"
    )
    assert coverage.lookback_years == 2


@pytest.mark.asyncio
async def test_summary_only_year_flagged() -> None:
    """A year with only a YTD partial — no annual T-12, no monthlies —
    should produce a ``summary_only`` gap."""
    from app.database import get_session_factory
    from app.services.coverage_audit import audit_document_coverage

    deal_id = uuid4()
    await _seed_deal(deal_id)
    # 2023 has annual coverage. 2024 has ONLY a YTD-through-October.
    await _seed_pnl(
        deal_id,
        doc_type="T12",
        period_type="annual",
        period_ending="2023-12-31",
    )
    await _seed_pnl(
        deal_id,
        doc_type="PNL_YTD",
        period_type="ytd",
        period_ending="2024-10-31",
    )

    factory = get_session_factory()
    async with factory() as session:
        coverage = await audit_document_coverage(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT_A,
            current_year=2025,
        )

    summary_only = [g for g in coverage.gaps if g.gap_type == "summary_only"]
    assert [g.year for g in summary_only] == [2024], (
        f"expected summary_only flag for 2024, got "
        f"{[(g.gap_type, g.year) for g in coverage.gaps]}"
    )
    assert summary_only[0].severity == "warn"
