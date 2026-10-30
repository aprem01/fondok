"""FON-93 — the SQL safety listener in ``raise`` mode over the whole
deal-scoped endpoint surface.

``tests/test_tenant_isolation_comprehensive.py`` proves the *endpoint*
layer scopes every route (cross-tenant → 404). Its listener self-tests
flip ``STRICT_TENANT_ENFORCEMENT=raise`` only around two hand-written
statements. This module re-drives that suite's ``ENDPOINT_CASES`` — same
routes, same bodies, both the owning tenant and the foreign tenant — with
the listener in ``raise`` mode for the entire request, so ANY statement a
route (or a helper it calls) runs against a tenant-scoped table without
a ``tenant_id`` predicate surfaces as a ``MissingTenantFilterError``
instead of a CRITICAL log line nobody reads.

The eight tables added to ``_TENANT_SCOPED_TABLES`` in FON-93 are what
make this meaningful: before, a ``SELECT … FROM scenarios WHERE id = :id``
was invisible to the listener.

``STRICT_TENANT_ENFORCEMENT`` is read from the process environment on
every statement, so the fixture sets it per test and restores it after —
other modules in the same process keep running in ``warn``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-tenant-listener-strict.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-tenant-listener-strict-storage"
)
_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

# Reuse the catalog — one source of truth for "every deal-scoped route".
from tests.test_tenant_isolation_comprehensive import (  # noqa: E402
    ENDPOINT_CASES,
    EndpointCase,
)

TENANT_A = "aaaaaaaa-1111-1111-1111-1111aaaaaaaa"
TENANT_B = "bbbbbbbb-2222-2222-2222-2222bbbbbbbb"

_ENV = "STRICT_TENANT_ENFORCEMENT"


class _Mode:
    """Set the listener mode for a block and restore the prior value."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.prior: str | None = None

    def __enter__(self) -> _Mode:
        self.prior = os.environ.get(_ENV)
        os.environ[_ENV] = self.mode
        return self

    def __exit__(self, *exc: object) -> None:
        if self.prior is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = self.prior


@pytest.fixture(autouse=True)
async def _schema_and_listener():
    """Migrations + listener attached; tables wiped in ``migrations`` mode
    (the wipe is deliberately unscoped and must not trip the listener)."""
    from sqlalchemy import text

    from app.database import get_engine, get_session_factory
    from app.migrations import run_startup_migrations
    from app.tenant_middleware import register_tenant_safety_listener

    with _Mode("migrations"):
        await run_startup_migrations()
        factory = get_session_factory()
        async with factory() as session:
            for tbl in (
                "audit_log",
                "extraction_results",
                "documents",
                "memo_edits",
                "broker_questions",
                "broker_qa_pairs",
                "due_diligence_questions",
                "critic_reports",
                "engine_outputs",
                "verification_reports",
                "scenarios",
                "cell_comments",
                "lineage_records",
                "deals",
            ):
                try:
                    await session.execute(text(f"DELETE FROM {tbl}"))
                except Exception:
                    pass
            await session.commit()
    register_tenant_safety_listener(get_engine())
    yield


@pytest.fixture
async def two_deals() -> tuple[str, str]:
    """One deal per tenant, created through the API under ``raise`` mode —
    creation itself is part of the surface under test."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    with _Mode("raise"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ra = await c.post(
                "/deals", json={"name": "Strict A", "city": "Austin, TX"}, headers={"X-Tenant-Id": TENANT_A}
            )
            assert ra.status_code == 201, ra.text
            rb = await c.post(
                "/deals", json={"name": "Strict B", "city": "Miami, FL"}, headers={"X-Tenant-Id": TENANT_B}
            )
            assert rb.status_code == 201, rb.text
    return ra.json()["id"], rb.json()["id"]


def _ids(c: EndpointCase) -> str:
    return c.label


@pytest.mark.parametrize("case", ENDPOINT_CASES, ids=_ids)
@pytest.mark.asyncio
async def test_endpoint_runs_clean_under_raise_mode_for_owner(
    case: EndpointCase, two_deals: tuple[str, str]
) -> None:
    """The owning tenant exercises the route; every SQL statement it runs
    must carry a tenant predicate (or the listener raises → 500 here)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_a, _ = two_deals
    path = case.mount_prefix + case.path_template.format(deal_id=deal_a)
    with _Mode("raise"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # raise_app_exceptions=True (the default) re-raises a
            # MissingTenantFilterError here instead of swallowing it as 500.
            resp = await c.request(case.method, path, json=case.body, headers={"X-Tenant-Id": TENANT_A})
    assert resp.status_code != 500, f"{case.label}: {resp.text[:300]}"


@pytest.mark.parametrize("case", ENDPOINT_CASES, ids=_ids)
@pytest.mark.asyncio
async def test_endpoint_runs_clean_under_raise_mode_cross_tenant(
    case: EndpointCase, two_deals: tuple[str, str]
) -> None:
    """Tenant A probing tenant B's deal: still 404, still no unscoped SQL."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _, deal_b = two_deals
    path = case.mount_prefix + case.path_template.format(deal_id=deal_b)
    with _Mode("raise"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.request(case.method, path, json=case.body, headers={"X-Tenant-Id": TENANT_A})
    assert resp.status_code not in (200, 201, 202, 204, 500), f"{case.label}: {resp.status_code} {resp.text[:300]}"


@pytest.mark.asyncio
async def test_listener_covers_the_fon93_tables() -> None:
    """The eight tables added in FON-93 are in the scoped set, and an
    unscoped read on each one raises in ``raise`` mode."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.tenant_middleware import _TENANT_SCOPED_TABLES, MissingTenantFilterError

    added = {
        "scenarios",
        "cell_comments",
        "lineage_records",
        "portfolio_library",
        "saved_pipeline_views",
        "pipeline_digest_schedules",
        "pending_batches",
        "template_mappings",
    }
    assert added <= _TENANT_SCOPED_TABLES

    factory = get_session_factory()
    with _Mode("raise"):
        async with factory() as session:
            for tbl in sorted(added):
                with pytest.raises(MissingTenantFilterError):
                    await session.execute(text(f"SELECT * FROM {tbl} LIMIT 1"))
            # The scoped form passes.
            for tbl in sorted(added):
                await session.execute(
                    text(f"SELECT * FROM {tbl} WHERE tenant_id = :t LIMIT 1"), {"t": TENANT_A}
                )
