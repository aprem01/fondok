"""Comp-set drift detection tests — Wave 1 roadmap item #8.

Covers the pure-function diff algorithm (no DB) plus the SQL-driven
``compute_comp_set_drift`` orchestration and the tenant-scoped
``GET /deals/{deal_id}/comp_set_drift`` endpoint.

We assert:
- identical comp sets across years → empty drifts
- property added in year_to → in `added`
- property removed in year_from → in `removed`
- "Hilton South Beach" vs "Hilton Hotel South Beach" → uncertain (≥0.80)
- "Hilton South Beach" vs "Marriott Pier" → counts as drift (<0.80)
- three years → two consecutive drifts ascending
- tenant scoping is enforced end-to-end
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Force a per-suite SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN. Mirrors the pattern used by
# test_documents.py and test_tenant_isolation.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-comp-set-drift.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-comp-set-drift-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT_A = "11111111-1111-1111-1111-111111110a0a"
TENANT_B = "22222222-2222-2222-2222-2222222b0b0b"


# ─────────────────────────── pure-function tests ───────────────────────────


def _entries(*names: str) -> list:
    from app.services.comp_set_drift import CompSetEntry

    return [CompSetEntry(name=n) for n in names]


def test_identical_compsets_have_no_drift() -> None:
    """Same five properties in both years → unchanged=5, added=removed=0."""
    from app.services.comp_set_drift import _diff_year_pair

    same = _entries(
        "Hilton South Beach",
        "W South Beach",
        "Loews Miami Beach",
        "Eden Roc",
        "Fontainebleau",
    )
    drift = _diff_year_pair(2024, list(same), 2025, list(same))
    assert drift.added == []
    assert drift.removed == []
    assert drift.uncertain_matches == []
    assert {e.name for e in drift.unchanged} == {e.name for e in same}


def test_property_added_in_year_to() -> None:
    """W South Beach appears in 2025 but not 2024 → in `added`."""
    from app.services.comp_set_drift import _diff_year_pair

    y_from = _entries("Hilton South Beach", "Loews Miami Beach")
    y_to = _entries("Hilton South Beach", "Loews Miami Beach", "W South Beach")
    drift = _diff_year_pair(2024, y_from, 2025, y_to)
    assert [e.name for e in drift.added] == ["W South Beach"]
    assert drift.removed == []
    assert drift.uncertain_matches == []


def test_property_removed_in_year_from() -> None:
    """Hilton South Beach was in 2024 but not 2025 → in `removed`."""
    from app.services.comp_set_drift import _diff_year_pair

    y_from = _entries("Hilton South Beach", "Loews Miami Beach")
    y_to = _entries("Loews Miami Beach")
    drift = _diff_year_pair(2024, y_from, 2025, y_to)
    assert [e.name for e in drift.removed] == ["Hilton South Beach"]
    assert drift.added == []
    assert drift.uncertain_matches == []


def test_uncertain_match_above_80_threshold() -> None:
    """'Hilton South Beach' vs 'Hilton Hotel South Beach' should score
    above 0.80 — flagged uncertain, NOT in added/removed."""
    from app.services.comp_set_drift import _diff_year_pair, _similarity

    # Sanity-check the threshold for this pair so a stdlib change in
    # SequenceMatcher would trip this test instead of silently flipping
    # the production behavior.
    assert _similarity(
        "Hilton South Beach", "Hilton Hotel South Beach"
    ) >= 0.80

    y_from = _entries("Hilton South Beach")
    y_to = _entries("Hilton Hotel South Beach")
    drift = _diff_year_pair(2024, y_from, 2025, y_to)
    assert drift.added == []
    assert drift.removed == []
    assert len(drift.uncertain_matches) == 1
    m = drift.uncertain_matches[0]
    assert m["from_name"] == "Hilton South Beach"
    assert m["to_name"] == "Hilton Hotel South Beach"
    assert m["similarity"] >= 0.80


def test_low_similarity_counts_as_drift() -> None:
    """'Hilton South Beach' vs 'Marriott Pier' — score < 0.80 →
    one removed + one added, no uncertain matches."""
    from app.services.comp_set_drift import _diff_year_pair, _similarity

    assert _similarity("Hilton South Beach", "Marriott Pier") < 0.80

    y_from = _entries("Hilton South Beach")
    y_to = _entries("Marriott Pier")
    drift = _diff_year_pair(2024, y_from, 2025, y_to)
    assert [e.name for e in drift.removed] == ["Hilton South Beach"]
    assert [e.name for e in drift.added] == ["Marriott Pier"]
    assert drift.uncertain_matches == []


def test_exact_match_is_case_insensitive() -> None:
    """Canonicalization should treat 'HILTON  South Beach' and 'hilton
    south beach' as the same property."""
    from app.services.comp_set_drift import _diff_year_pair

    y_from = _entries("HILTON  South Beach")
    y_to = _entries("hilton south beach")
    drift = _diff_year_pair(2024, y_from, 2025, y_to)
    assert drift.added == []
    assert drift.removed == []
    assert drift.uncertain_matches == []
    assert len(drift.unchanged) == 1


# ─────────────────────────── DB-driven tests ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db():
    """Truncate state between tests so each starts deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    import contextlib

    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "extraction_results", "documents", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _seed_str_trend(
    *,
    deal_id: str,
    tenant_id: str,
    report_year: int,
    compset: list[tuple[str, int | None]],
    created_at: str,
) -> str:
    """Insert one STR_TREND document + extraction_results row.

    Returns the document_id. Each compset tuple is (name, keys).
    """
    from sqlalchemy import text

    from app.database import get_session_factory

    doc_id = str(uuid4())
    ext_id = str(uuid4())

    fields = [
        {
            "field_name": "str_trend.report_year",
            "value": report_year,
            "confidence": 0.99,
        }
    ]
    for idx, (name, keys) in enumerate(compset, start=1):
        fields.append(
            {
                "field_name": f"ttm_performance.compset.{idx}.name",
                "value": name,
                "confidence": 0.95,
            }
        )
        if keys is not None:
            fields.append(
                {
                    "field_name": f"ttm_performance.compset.{idx}.keys",
                    "value": keys,
                    "confidence": 0.95,
                }
            )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type,
                    status, uploaded_at
                ) VALUES (
                    :id, :deal, :tenant, :fn, 'STR_TREND',
                    'EXTRACTED', :ts
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": f"str_trend_{report_year}.xls",
                "ts": created_at,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id,
                    fields, confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant,
                    :fields, :cr, :ver, :ts
                )
                """
            ),
            {
                "id": ext_id,
                "doc": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fields": json.dumps(fields),
                "cr": json.dumps({"overall": 0.95}),
                "ver": "test-fixture",
                "ts": created_at,
            },
        )
        await session.commit()
    return doc_id


async def _seed_deal(deal_id: str, tenant_id: str) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Draft', :ts, :ts
                )
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "name": "South Beach Test",
                "ts": "2026-04-27 00:00:00",
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_three_years_produce_two_consecutive_drifts() -> None:
    """2023, 2024, 2025 STR_TREND extractions → two drifts ascending:
    (2023→2024) and (2024→2025)."""
    from app.database import get_session_factory
    from app.services.comp_set_drift import compute_comp_set_drift

    deal_id = str(uuid4())
    await _seed_deal(deal_id, TENANT_A)
    # 2023 → 2024: swap Loews for W
    # 2024 → 2025: swap Hilton for The Standard
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2023,
        compset=[("Hilton South Beach", 350), ("Loews Miami Beach", 790)],
        created_at="2026-04-01 00:00:00",
    )
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2024,
        compset=[("Hilton South Beach", 350), ("W South Beach", 408)],
        created_at="2026-04-02 00:00:00",
    )
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2025,
        compset=[("The Standard Miami Beach", 105), ("W South Beach", 408)],
        created_at="2026-04-03 00:00:00",
    )

    factory = get_session_factory()
    async with factory() as session:
        report = await compute_comp_set_drift(
            session, deal_id=deal_id, tenant_id=TENANT_A
        )

    assert report.deal_id == deal_id
    assert len(report.drifts) == 2

    d0 = report.drifts[0]
    assert (d0.year_from, d0.year_to) == (2023, 2024)
    assert [e.name for e in d0.added] == ["W South Beach"]
    assert [e.name for e in d0.removed] == ["Loews Miami Beach"]

    d1 = report.drifts[1]
    assert (d1.year_from, d1.year_to) == (2024, 2025)
    assert [e.name for e in d1.added] == ["The Standard Miami Beach"]
    assert [e.name for e in d1.removed] == ["Hilton South Beach"]


@pytest.mark.asyncio
async def test_endpoint_returns_drift_via_api() -> None:
    """The HTTP endpoint serializes the report and respects X-Tenant-Id."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id, TENANT_A)
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2024,
        compset=[("Hilton South Beach", 350)],
        created_at="2026-04-01 00:00:00",
    )
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2025,
        compset=[("W South Beach", 408)],
        created_at="2026-04-02 00:00:00",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/comp_set_drift",
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deal_id"] == deal_id
    assert len(body["drifts"]) == 1
    d = body["drifts"][0]
    assert d["year_from"] == 2024
    assert d["year_to"] == 2025
    assert [e["name"] for e in d["added"]] == ["W South Beach"]
    assert [e["name"] for e in d["removed"]] == ["Hilton South Beach"]


@pytest.mark.asyncio
async def test_tenant_scoping_enforced() -> None:
    """Tenant B asking for tenant A's deal_id sees an empty drifts list,
    not the actual drift. The SQL filters on both extraction.tenant_id
    AND document.tenant_id, so cross-tenant deal_id guessing yields
    nothing rather than leaking data.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id, TENANT_A)
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2024,
        compset=[("Hilton South Beach", 350)],
        created_at="2026-04-01 00:00:00",
    )
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2025,
        compset=[("W South Beach", 408)],
        created_at="2026-04-02 00:00:00",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Tenant A: gets the real drift.
        ra = await client.get(
            f"/deals/{deal_id}/comp_set_drift",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert ra.status_code == 200
        assert len(ra.json()["drifts"]) == 1

        # Tenant B with the same deal_id: empty report.
        rb = await client.get(
            f"/deals/{deal_id}/comp_set_drift",
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert rb.status_code == 200
        assert rb.json()["drifts"] == []


@pytest.mark.asyncio
async def test_extractions_missing_report_year_are_skipped() -> None:
    """Historical STR_TREND extractions that predate the schema field
    have no ``str_trend.report_year`` row. They should be silently
    skipped instead of crashing the diff."""
    import json as _json

    from sqlalchemy import text

    from app.database import get_session_factory
    from app.services.comp_set_drift import compute_comp_set_drift

    deal_id = str(uuid4())
    await _seed_deal(deal_id, TENANT_A)

    # Seed one *legacy* extraction with compset but no report_year.
    doc_id = str(uuid4())
    legacy_fields = [
        {
            "field_name": "ttm_performance.compset.1.name",
            "value": "Hilton South Beach",
            "confidence": 0.9,
        }
    ]
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type,
                    status, uploaded_at
                ) VALUES (
                    :id, :deal, :tenant, 'legacy.xls', 'STR_TREND',
                    'EXTRACTED', '2026-01-01 00:00:00'
                )
                """
            ),
            {"id": doc_id, "deal": deal_id, "tenant": TENANT_A},
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id,
                    fields, confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant,
                    :fields, '{}', 'legacy', '2026-01-01 00:00:00'
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": doc_id,
                "deal": deal_id,
                "tenant": TENANT_A,
                "fields": _json.dumps(legacy_fields),
            },
        )
        await session.commit()

    # Plus a current-schema extraction so the report isn't completely empty.
    await _seed_str_trend(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        report_year=2025,
        compset=[("W South Beach", 408)],
        created_at="2026-04-02 00:00:00",
    )

    async with factory() as session:
        report = await compute_comp_set_drift(
            session, deal_id=deal_id, tenant_id=TENANT_A
        )

    # Only one year is placeable on the timeline → no consecutive pairs.
    assert report.drifts == []
