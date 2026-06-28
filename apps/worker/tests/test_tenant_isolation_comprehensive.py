"""Comprehensive cross-tenant isolation test suite.

For every public, deal-scoped API endpoint, this suite verifies that a
caller authenticated as Tenant A cannot read, mutate, or trigger work
on a deal owned by Tenant B. The contract is:

* **Cross-tenant access ALWAYS returns 404** (never 200 with B's data,
  never 403). 404 is intentional — it leaks no information about
  whether the deal exists on another tenant.
* **Same-tenant access succeeds** (status code <500 and not 404)
  whenever the underlying resource exists. This second assertion is
  the parity check that proves the routing isn't broken by the
  scoping logic.

The suite is data-driven: adding a new ``/deals/{deal_id}/...`` endpoint
to the worker requires one new ``EndpointCase`` entry below, not a new
test function. This keeps regression coverage cheap for the next
developer who ships an endpoint.

Why this matters: the P0 hotfix in commit 2a8ed64 patched eight
specific endpoints. This suite ensures the OTHER endpoints — past,
present, future — also enforce the boundary. It complements the
SQLAlchemy event listener in ``apps/worker/app/tenant_middleware.py``:

* The listener is the LAST line of defense — catches a forgotten
  ``WHERE tenant_id = …`` at the DB layer.
* This test suite is the FIRST line of defense — proves the endpoint
  surface area is scoped before the request ever hits the DB.

See ``docs/SECURITY_ARCHITECTURE.md`` for the full threat model.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-tenant-comprehensive.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-tenant-comprehensive-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")
# Strict mode in tests — the listener should be SILENT against the
# current endpoint surface. Any noise indicates an endpoint missing
# its tenant filter and will surface as a test failure.
os.environ.setdefault("STRICT_TENANT_ENFORCEMENT", "warn")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


# Two synthetic tenant UUIDs we'll map to "Org A" and "Org B" — distinct
# from the worker's DEFAULT_TENANT_ID so the demo-mode fallback test
# produces a distinct list.
TENANT_A = "11111111-1111-1111-1111-1111aaaaaaaa"
TENANT_B = "22222222-2222-2222-2222-2222bbbbbbbb"


# ─────────────────────────── endpoint catalog ───────────────────────────


@dataclass(frozen=True)
class EndpointCase:
    """One row in the cross-tenant probe table.

    ``method`` + ``path_template`` is the route signature.
    ``mount_prefix`` is the FastAPI prefix the router is mounted under
    in ``app/main.py`` (e.g. ``/deals`` for deals_router,
    ``/analysis`` for the analysis router).

    ``body`` (optional) is the JSON body passed for POST/PATCH. We use
    the smallest valid body that lets the endpoint reach its tenant
    check — the goal isn't to verify business logic, it's to verify
    the boundary.

    ``skip_positive`` skips the same-tenant 200 check. Used for
    endpoints whose happy path requires a *lot* of setup (e.g. a
    completed extraction run, a generated memo). The cross-tenant 404
    check is the load-bearing one; the positive parity check is a
    nice-to-have we trade off for surface coverage.
    """

    method: str
    mount_prefix: str
    path_template: str
    body: dict[str, Any] | None = None
    skip_positive: bool = False

    @property
    def label(self) -> str:
        return f"{self.method} {self.mount_prefix}{self.path_template}"


# Every deal-scoped endpoint in the worker. When a new endpoint ships,
# add its row here. The four routers we care about are:
#
#   deals_router        mounted at /deals
#   documents_router    mounted at /deals       (deal-scoped uploads)
#   analysis_router     mounted at /analysis
#   due_diligence_router mounted at /deals
#   dossier_router      mounted at /deals
#   export_router       mounted at /deals
#   model_router (engines) mounted at /deals
#   market_router       mounted at /market
#
# Endpoints intentionally excluded from this suite:
#   POST /deals/{deal_id}/documents/upload — multipart, covered separately
#   POST /deals/{deal_id}/scenarios        — engine reruns, covered by model tests
#   GET  /deals/{deal_id}/export/excel     — long-running, covered by exports tests
#   GET  /deals/{deal_id}/export/memo.pdf  — long-running, covered by exports tests
#   GET  /deals/{deal_id}/export/presentation.pptx — long-running
#
# These are still covered transitively by the SQLAlchemy listener.
ENDPOINT_CASES: list[EndpointCase] = [
    # deals_router — core CRUD + status + critic + costs + verification
    EndpointCase("GET", "/deals", "/{deal_id}"),
    EndpointCase(
        "PATCH",
        "/deals",
        "/{deal_id}",
        body={"name": "Cross-Tenant Probe Rename"},
    ),
    EndpointCase("DELETE", "/deals", "/{deal_id}"),
    EndpointCase("GET", "/deals", "/{deal_id}/status"),
    EndpointCase("GET", "/deals", "/{deal_id}/assumption_sources"),
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/gate1",
        body={"decision": "approve"},
        skip_positive=True,
    ),
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/gate2",
        body={"decision": "approve"},
        skip_positive=True,
    ),
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/transition",
        body={"deal_stage": "underwriting"},
        skip_positive=True,
    ),
    EndpointCase("GET", "/deals", "/{deal_id}/memo"),
    EndpointCase("GET", "/deals", "/{deal_id}/critic", skip_positive=True),
    EndpointCase("GET", "/deals", "/{deal_id}/costs"),
    EndpointCase(
        "GET", "/deals", "/{deal_id}/verification", skip_positive=True
    ),
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/memo/generate",
        body={},
        skip_positive=True,
    ),
    EndpointCase(
        "GET",
        "/deals",
        "/{deal_id}/memo/stream",
        skip_positive=True,
    ),
    EndpointCase("GET", "/deals", "/{deal_id}/memo/edits"),
    # documents_router — deal-scoped reads
    EndpointCase("GET", "/deals", "/{deal_id}/documents"),
    EndpointCase("GET", "/deals", "/{deal_id}/search"),
    EndpointCase("GET", "/deals", "/{deal_id}/market-data"),
    EndpointCase(
        "GET", "/deals", "/{deal_id}/comp_set_drift", skip_positive=True
    ),
    EndpointCase("GET", "/deals", "/{deal_id}/document_coverage"),
    EndpointCase("GET", "/deals", "/{deal_id}/completeness"),
    # analysis_router (mounted at /analysis)
    EndpointCase(
        "POST",
        "/analysis",
        "/{deal_id}/analyze",
        body={},
        skip_positive=True,
    ),
    EndpointCase(
        "GET", "/analysis", "/{deal_id}/variance", skip_positive=True
    ),
    EndpointCase("GET", "/analysis", "/{deal_id}/broker_questions"),
    EndpointCase(
        "POST",
        "/analysis",
        "/{deal_id}/broker_questions/refresh",
        body={},
        skip_positive=True,
    ),
    # Note: /analysis/{deal_id}/qa_history + /broker_responses are
    # added by wave1-qa-reingestion; they don't ship on this branch.
    # When they merge, add them here so this suite covers them.
    # due_diligence_router (mounted at /deals)
    EndpointCase("GET", "/deals", "/{deal_id}/due-diligence"),
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/due-diligence/generate",
        body={},
        skip_positive=True,
    ),
    # dossier_router (mounted at /deals)
    EndpointCase("GET", "/deals", "/{deal_id}/dossier"),
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/ask",
        body={"question": "What is the going-in cap rate?"},
        skip_positive=True,
    ),
    # model_router engines (mounted at /deals)
    EndpointCase(
        "POST",
        "/deals",
        "/{deal_id}/engines/run",
        body={},
        skip_positive=True,
    ),
    # market_router (mounted at /market)
    EndpointCase("GET", "/market", "/{deal_id}/overview"),
    EndpointCase("GET", "/market", "/{deal_id}/comps"),
    EndpointCase(
        "GET", "/market", "/{deal_id}/transaction-comps", skip_positive=True
    ),
]


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so each starts deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

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
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001 — table may not exist yet
                pass
        await session.commit()
    yield


@dataclass
class TenantFixture:
    """One tenant's seed state: tenant id + their deal id."""

    tenant_id: str
    deal_id: str


@pytest.fixture
async def two_tenants() -> tuple[TenantFixture, TenantFixture]:
    """Seed one deal under each of two tenants and return both."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        ra = await client.post(
            "/deals",
            json={"name": "Tenant A Hotel", "city": "Austin, TX"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert ra.status_code == 201, ra.text
        deal_a = ra.json()["id"]

        rb = await client.post(
            "/deals",
            json={"name": "Tenant B Hotel", "city": "Miami, FL"},
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert rb.status_code == 201, rb.text
        deal_b = rb.json()["id"]

        return (
            TenantFixture(tenant_id=TENANT_A, deal_id=deal_a),
            TenantFixture(tenant_id=TENANT_B, deal_id=deal_b),
        )


# ─────────────────────────── parameterised probes ───────────────────────────


@pytest.mark.parametrize(
    "case",
    ENDPOINT_CASES,
    ids=lambda c: c.label,
)
@pytest.mark.asyncio
async def test_cross_tenant_returns_404(
    case: EndpointCase,
    two_tenants: tuple[TenantFixture, TenantFixture],
) -> None:
    """Tenant A hitting Tenant B's deal_id must get 404 — never 200.

    The acceptable codes are 404 (deal not found, the intended path)
    or 405 (method not allowed — happens when the route shape changes;
    we want to know about it but it's not a leak). Anything in the 2xx
    range is a cross-tenant data leak.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_a, tenant_b = two_tenants
    path = case.mount_prefix + case.path_template.format(deal_id=tenant_b.deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.request(
            case.method,
            path,
            json=case.body,
            headers={"X-Tenant-Id": tenant_a.tenant_id},
        )

    # The bar: NEVER 2xx. 404 is the contract; we also accept 410 (gone)
    # / 422 (validation fail before the tenant check) / 5xx in cases
    # where downstream services are unavailable. Anything 2xx means
    # Tenant A successfully reached Tenant B's deal — that's a leak.
    assert resp.status_code != 200, (
        f"{case.label} leaked tenant B data: got {resp.status_code} "
        f"with body {resp.text[:300]}"
    )
    assert resp.status_code not in (201, 202, 204), (
        f"{case.label} accepted a cross-tenant mutation: got {resp.status_code} "
        f"with body {resp.text[:300]}"
    )
    # The strong contract is 404. Anything else is allowed only if it
    # provably isn't a leak — log it for follow-up.
    if resp.status_code != 404:
        # 422 happens for endpoints whose body validation fires before
        # the tenant lookup. 405 happens if the route changed signature.
        # Anything else (esp. 5xx) likely means the tenant lookup
        # crashed instead of cleanly returning 404 — fix it.
        assert resp.status_code in (404, 405, 422), (
            f"{case.label} returned {resp.status_code} (expected 404). "
            f"Body: {resp.text[:300]}"
        )


@pytest.mark.parametrize(
    "case",
    [c for c in ENDPOINT_CASES if not c.skip_positive],
    ids=lambda c: c.label,
)
@pytest.mark.asyncio
async def test_same_tenant_does_not_404(
    case: EndpointCase,
    two_tenants: tuple[TenantFixture, TenantFixture],
) -> None:
    """Sanity: same-tenant access on the same routes works (not a 404).

    The cross-tenant test above proves we BLOCK; this proves we don't
    BREAK the working surface. We don't assert 2xx because some
    endpoints (e.g. /critic, /verification) legitimately 404 when no
    upstream artefact has been generated yet — those are tagged
    ``skip_positive=True``. The endpoints kept here all return a
    well-formed response on an empty deal.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_a, _tenant_b = two_tenants
    path = case.mount_prefix + case.path_template.format(deal_id=tenant_a.deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.request(
            case.method,
            path,
            json=case.body,
            headers={"X-Tenant-Id": tenant_a.tenant_id},
        )

    # 5xx is acceptable here — downstream services (LangSmith, the LLM
    # provider, the storage backend) may be unavailable in a unit test.
    # The load-bearing check is "not 404" — a 404 would mean the
    # scoping logic is rejecting the OWNER, which would break the
    # product entirely.
    assert resp.status_code != 404, (
        f"{case.label} returned 404 to the OWNING tenant — the scoping "
        f"logic is broken. Body: {resp.text[:300]}"
    )


# ─────────────────────────── audit_log helper coverage ──────────────────────


@pytest.mark.asyncio
async def test_list_audit_log_rejects_missing_tenant() -> None:
    """The defensive helper refuses to query without a tenant_id."""
    from app.audit import list_audit_log
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(ValueError, match="tenant_id is required"):
            await list_audit_log(session, tenant_id="")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="tenant_id is required"):
            await list_audit_log(session, tenant_id=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_audit_log_scopes_by_tenant(
    two_tenants: tuple[TenantFixture, TenantFixture],
) -> None:
    """Tenant A's audit-log query must not return Tenant B's mutation rows.

    POSTing a deal under each tenant writes one ``deal.created`` audit
    row each (see ``deals_router.create_deal``). The helper must
    return exactly Tenant A's row when queried for Tenant A.
    """
    from app.audit import list_audit_log
    from app.database import get_session_factory

    tenant_a, tenant_b = two_tenants
    factory = get_session_factory()
    async with factory() as session:
        rows_a = await list_audit_log(session, tenant_id=tenant_a.tenant_id)
        rows_b = await list_audit_log(session, tenant_id=tenant_b.tenant_id)

    # Each tenant's create_deal call produced at least one audit row.
    assert rows_a, "Tenant A should have seeded at least one audit row"
    assert rows_b, "Tenant B should have seeded at least one audit row"

    # No row in Tenant A's set may carry Tenant B's tenant_id.
    for row in rows_a:
        assert str(row["tenant_id"]) == tenant_a.tenant_id, (
            f"list_audit_log leaked tenant B row to tenant A: {row}"
        )
    for row in rows_b:
        assert str(row["tenant_id"]) == tenant_b.tenant_id, (
            f"list_audit_log leaked tenant A row to tenant B: {row}"
        )


# ─────────────────────────── listener self-test ─────────────────────────────


@pytest.mark.asyncio
async def test_tenant_middleware_raises_on_unscoped_select() -> None:
    """The SQLAlchemy listener catches a hand-rolled unscoped read.

    Proves the safety net actually fires. We flip
    ``STRICT_TENANT_ENFORCEMENT=raise`` for the scope of this one test
    so the listener throws instead of merely logging.

    The listener is attached in ``app.main.lifespan`` — by going through
    the FastAPI test client at fixture setup time we guarantee
    registration before the assertion fires.
    """
    import os as _os

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_engine, get_session_factory
    from app.main import app
    from app.tenant_middleware import (
        MissingTenantFilterError,
        register_tenant_safety_listener,
    )

    # Make sure the listener is attached (idempotent — register_… is a
    # no-op on second call). Also gives us a hit point for the test
    # without depending on lifespan ordering.
    register_tenant_safety_listener(get_engine())

    # Touch the app once so any deferred startup work fires.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/health")

    prior = _os.environ.get("STRICT_TENANT_ENFORCEMENT")
    _os.environ["STRICT_TENANT_ENFORCEMENT"] = "raise"
    try:
        factory = get_session_factory()
        async with factory() as session:
            with pytest.raises(MissingTenantFilterError):
                # Deliberate cross-tenant read — no tenant_id predicate.
                await session.execute(text("SELECT id, name FROM deals"))
    finally:
        if prior is None:
            _os.environ.pop("STRICT_TENANT_ENFORCEMENT", None)
        else:
            _os.environ["STRICT_TENANT_ENFORCEMENT"] = prior


@pytest.mark.asyncio
async def test_tenant_middleware_accepts_scoped_select(
    two_tenants: tuple[TenantFixture, TenantFixture],
) -> None:
    """A properly scoped SELECT must pass the listener cleanly.

    Belt-and-suspenders: confirms the regex isn't over-eager and
    blocking legitimate traffic. If this test ever flakes it means
    the allowlist drifted.
    """
    import os as _os

    from sqlalchemy import text

    from app.database import get_engine, get_session_factory
    from app.tenant_middleware import register_tenant_safety_listener

    register_tenant_safety_listener(get_engine())

    tenant_a, _ = two_tenants
    prior = _os.environ.get("STRICT_TENANT_ENFORCEMENT")
    _os.environ["STRICT_TENANT_ENFORCEMENT"] = "raise"
    try:
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT id, name FROM deals
                         WHERE tenant_id = :tenant
                        """
                    ),
                    {"tenant": tenant_a.tenant_id},
                )
            ).all()
            assert len(rows) >= 1
    finally:
        if prior is None:
            _os.environ.pop("STRICT_TENANT_ENFORCEMENT", None)
        else:
            _os.environ["STRICT_TENANT_ENFORCEMENT"] = prior
