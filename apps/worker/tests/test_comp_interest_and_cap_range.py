"""R-064 (comp Interest column) + E-020 (Entry Cap "Comps range" hint).

Pure-helper tests for ``services.comp_cap_range`` plus one end-to-end read of
``GET /market/{id}/transaction-comps`` over seeded extraction rows.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-comp-interest-range.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

from app.services.comp_cap_range import (  # noqa: E402
    comp_cap_rate_range,
    normalize_comp_interest,
)

# ───────────────────────────── R-064 — interest ─────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fee_simple", "Fee Simple"),
        ("Fee Simple", "Fee Simple"),
        ("ground_lease", "Ground Lease"),
        ("Ground  Lease", "Ground Lease"),
        ("subject to ground lease", "Ground Lease"),
        (None, None),
        ("", None),
        ("leasehold", None),  # not an explicit ground-lease statement
        ("unknown", None),
    ],
)
def test_normalize_interest_type(raw: object, expected: str | None) -> None:
    assert normalize_comp_interest(raw) == expected


def test_boolean_flags_assert_only_their_own_interest() -> None:
    assert normalize_comp_interest(ground_lease=True) == "Ground Lease"
    assert normalize_comp_interest(fee_simple="yes") == "Fee Simple"
    # A false flag is NOT evidence of the other interest — never inferred.
    assert normalize_comp_interest(ground_lease=False) is None
    assert normalize_comp_interest(fee_simple=False) is None


# ─────────────────────────── E-020 — cap-rate range ──────────────────────────


def test_range_needs_two_disclosed_cap_rates() -> None:
    assert comp_cap_rate_range([]) is None
    assert comp_cap_rate_range([7.5]) is None
    assert comp_cap_rate_range([7.5, None, None]) is None
    assert comp_cap_rate_range([None, 0.0, 7.5]) is None


def test_range_low_high_median_n() -> None:
    assert comp_cap_rate_range([7.5, None, 6.85, 8.1]) == {
        "low_pct": 6.85,
        "high_pct": 8.1,
        "median_pct": 7.5,
        "n": 3,
    }
    even = comp_cap_rate_range([6.0, 7.0, 8.0, 9.0])
    assert even is not None and even["median_pct"] == 7.5 and even["n"] == 4


# ─────────────────────────── endpoint end-to-end ────────────────────────────


@pytest.fixture
async def _db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("extraction_results", "documents", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _seed(session, *, deal_id: str, tenant_id: str, fields: list[dict]) -> None:
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, city, keys, field_overrides) "
            "VALUES (:id, :tenant, 'Comp Test', 'Tampa, FL', 150, '{}')"
        ),
        {"id": deal_id, "tenant": tenant_id},
    )
    doc_id = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status) "
            "VALUES (:id, :deal, :tenant, 'OM.pdf', 'OM', 'Extracted')"
        ),
        {"id": doc_id, "deal": deal_id, "tenant": tenant_id},
    )
    await session.execute(
        text(
            "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields) "
            "VALUES (:id, :doc, :deal, :tenant, :fields)"
        ),
        {
            "id": str(uuid4()),
            "doc": doc_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "fields": json.dumps(fields),
        },
    )


@pytest.mark.asyncio
async def test_endpoint_emits_interest_and_cap_rate_range(_db: None) -> None:
    from app.api.market import transaction_comps
    from app.database import get_session_factory

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    fields = [
        {"field_name": "transaction_comps.1.name", "value": "Alpha Hotel", "source_page": 3},
        {"field_name": "transaction_comps.1.cap_rate_pct", "value": 7.0},
        {"field_name": "transaction_comps.1.interest_type", "value": "fee_simple"},
        {"field_name": "transaction_comps.2.name", "value": "Beta Hotel"},
        {"field_name": "transaction_comps.2.cap_rate_pct", "value": 0.08},
        {"field_name": "transaction_comps.2.interest_type", "value": "Ground Lease"},
        {"field_name": "transaction_comps.3.name", "value": "Gamma Hotel"},
    ]
    factory = get_session_factory()
    async with factory() as session:
        await _seed(session, deal_id=deal_id, tenant_id=tenant_id, fields=fields)
        await session.commit()
        resp = await transaction_comps(
            deal_id=UUID(deal_id), session=session, tenant_id=UUID(tenant_id)
        )

    by_name = {c.name: c for c in resp.comps}
    assert by_name["Alpha Hotel"].interest == "Fee Simple"
    assert by_name["Beta Hotel"].interest == "Ground Lease"
    assert by_name["Gamma Hotel"].interest is None  # not stated → "—"
    rng = resp.cap_rate_range
    assert rng is not None
    assert (rng.low_pct, rng.high_pct, rng.median_pct, rng.n) == (7.0, 8.0, 7.5, 2)


@pytest.mark.asyncio
async def test_endpoint_range_null_below_two_cap_rates(_db: None) -> None:
    from app.api.market import transaction_comps
    from app.database import get_session_factory

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    fields = [
        {"field_name": "transaction_comps.1.name", "value": "Solo Hotel"},
        {"field_name": "transaction_comps.1.cap_rate_pct", "value": 7.25},
        {"field_name": "transaction_comps.2.name", "value": "No Cap Hotel"},
    ]
    factory = get_session_factory()
    async with factory() as session:
        await _seed(session, deal_id=deal_id, tenant_id=tenant_id, fields=fields)
        await session.commit()
        resp = await transaction_comps(
            deal_id=UUID(deal_id), session=session, tenant_id=UUID(tenant_id)
        )
    assert len(resp.comps) == 2
    assert resp.cap_rate_range is None
