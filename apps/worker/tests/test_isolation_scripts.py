"""FON-93 Phase 3 tooling — ``scripts/isolation_check.py`` and
``scripts/isolation_rollout.py``.

* The checker, driven over an ASGI transport against the real app in local
  header mode with two tenants (the comprehensive suite's fixtures), must
  report no leak across every deal-scoped route — with writes on, so the
  mutating routes are probed on scratch deals and the scratch deals are
  cleaned up.
* The checker must not be vacuous: a toy app that serves another tenant's
  deal is reported as a LEAK with exit 1.
* The rollout dry run prints the plan and writes nothing (row counts and
  tenant ids unchanged); ``--execute`` registers the internal tenant and
  moves the listed deals' tenant rows, and refuses when a source tenant
  owns deals that were not listed.
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-isolation-scripts.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-isolation-scripts-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")
os.environ.setdefault("ALLOW_TENANT_HEADER_WITHOUT_JWT", "1")
_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

# The two-tenant fixtures are the comprehensive suite's — one source of truth
# for "two institutions, one deal and one document each".
from tests.test_tenant_isolation_comprehensive import (  # noqa: E402,F401
    TenantFixture,
    _reset_db,
    two_tenants,
    two_tenants_with_docs,
)

_WORKER_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _WORKER_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: ``from __future__ import annotations`` dataclasses
    # resolve their module through ``sys.modules`` at class-creation time.
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


ic = _load("isolation_check")
rollout = _load("isolation_rollout")


# ─────────────────────────── isolation_check ───────────────────────────


@pytest.mark.asyncio
async def test_isolation_check_header_mode_reports_no_leak_and_cleans_up(
    two_tenants_with_docs: tuple[TenantFixture, TenantFixture, str, str],  # noqa: F811 — pytest fixture
) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_a, tenant_b, _doc_a, _doc_b = two_tenants_with_docs
    routes = ic.routes_from_app(app)
    deal_scoped = [r for r in routes if r.deal_scoped]
    # The surface this is supposed to cover really is the whole app.
    assert len(deal_scoped) >= 90, len(deal_scoped)

    out = io.StringIO()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        code, results = await ic.run_checks(
            client,
            ic.Firm("firm A", {"X-Tenant-Id": tenant_a.tenant_id}),
            ic.Firm("firm B", {"X-Tenant-Id": tenant_b.tenant_id}),
            mode="header",
            routes=routes,
            options=ic.RunOptions(allow_writes=True, timeout_s=60),
            out=out,
        )
        # Scratch deals are gone from both lists afterwards.
        for fx in (tenant_a, tenant_b):
            rows = (await client.get("/deals", headers={"X-Tenant-Id": fx.tenant_id})).json()
            assert all("isolation-check scratch" not in str(r.get("name")) for r in rows), rows

    text = out.getvalue()
    by_verdict: dict[str, list[Any]] = {}
    for r in results:
        by_verdict.setdefault(r.verdict, []).append(r)
    assert code == 0, text
    assert not by_verdict.get("LEAK"), [r.as_dict() for r in by_verdict["LEAK"]]
    assert not by_verdict.get("ERROR"), [r.as_dict() for r in by_verdict["ERROR"]]
    # Every deal-scoped route was probed (writes on → nothing skipped as mutating).
    probed = {r.check for r in results if r.verdict in {"PASS", "INCONCLUSIVE"}}
    mutating_skips = [r for r in by_verdict.get("SKIP", []) if "mutating" in r.note]
    assert mutating_skips == []
    assert sum(1 for r in deal_scoped if f"{r.method} {r.path}" in probed) >= 85
    # The load-bearing rows are there and green.
    checks = {r.check: r for r in results}
    assert checks["GET /deals/{deal_id}"].verdict == "PASS"
    assert checks["GET /deals/{deal_id}/search"].verdict == "PASS"
    assert checks["POST /deals/{deal_id}/documents/{doc_id}/extract"].verdict == "PASS"
    assert checks["DELETE /deals/{deal_id}"].verdict == "PASS"
    assert checks["GET /deals lists are disjoint"].verdict == "PASS"
    assert checks["B reads A's scratch deal"].verdict == "PASS"
    assert checks["GET /settings/tenant"].verdict == "PASS"
    assert "RESULT: PASS" in text
    assert "mode: header" in text


@pytest.mark.asyncio
async def test_isolation_check_flags_a_leaking_route() -> None:
    """A toy worker that serves any tenant's deal must produce LEAK + exit 1 —
    otherwise the green run above proves nothing."""
    from fastapi import FastAPI, Header
    from httpx import ASGITransport, AsyncClient

    toy = FastAPI()
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    deal_a = str(uuid.uuid4())

    @toy.get("/health")
    async def health() -> dict[str, Any]:
        return {"version": "toy", "db": "ok", "auth": {"jwt_required": False, "header_tenant_path_enabled": True}}

    @toy.get("/settings/tenant")
    async def settings_tenant(x_tenant_id: str = Header(alias="X-Tenant-Id")) -> dict[str, Any]:
        return {"tenant_id": x_tenant_id, "registry": None}

    @toy.get("/deals")
    async def deals(x_tenant_id: str = Header(alias="X-Tenant-Id")) -> list[dict[str, Any]]:
        return [{"id": deal_a, "name": "A Hotel", "tenant_id": tenant_a}] if x_tenant_id == tenant_a else []

    @toy.get("/deals/{deal_id}/documents")
    async def docs(deal_id: str) -> list[dict[str, Any]]:
        return []

    @toy.get("/deals/{deal_id}")
    async def leaky_deal(deal_id: str) -> dict[str, Any]:
        # No tenant check at all — the bug class this tool exists for.
        return {"id": deal_id, "name": "A Hotel", "tenant_id": tenant_a}

    routes = ic.routes_from_app(toy)
    out = io.StringIO()
    async with AsyncClient(transport=ASGITransport(app=toy), base_url="http://toy") as client:
        code, results = await ic.run_checks(
            client,
            ic.Firm("firm A", {"X-Tenant-Id": tenant_a}),
            ic.Firm("firm B", {"X-Tenant-Id": tenant_b}),
            mode="header",
            routes=routes,
            out=out,
        )
    assert code == 1
    leak = next(r for r in results if r.check == "GET /deals/{deal_id}")
    assert leak.verdict == "LEAK"
    assert "stranger got 200" in leak.note
    assert "RESULT: FAIL" in out.getvalue()


def test_isolation_check_bodies_reach_the_handler_shape() -> None:
    """Required body fields are filled from the route's model so a probe
    does not 422 before the tenant lookup (the ``/search?q=`` lesson)."""
    from app.main import app

    routes = {(r.method, r.path): r for r in ic.routes_from_app(app)}
    create = routes[("POST", "/deals")]
    assert ic.body_for(create) == {"name": "probe"}
    search = routes[("GET", "/deals/{deal_id}/search")]
    assert search.required_query == ("q",)
    assert ic.params_for(search) == {"q": "probe"}
    gate = routes[("POST", "/deals/{deal_id}/gate1")]
    assert ic.body_for(gate) == {"decision": "approve"}


# ─────────────────────────── isolation_rollout ───────────────────────────


INTERNAL_ORG = "org_RolloutTestInternal"


async def _seed_deal(client, tenant: str, name: str) -> str:
    r = await client.post("/deals", json={"name": name, "city": "Probe, XX", "keys": 50}, headers={"X-Tenant-Id": tenant})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _snapshot(engine, tenants: list[str]) -> dict[str, Any]:
    from sqlalchemy import text

    snap: dict[str, Any] = {}
    async with engine.connect() as conn:
        snap["tenants"] = int((await conn.execute(text("SELECT COUNT(*) FROM tenants"))).scalar_one())
        for t in tenants:
            rows = (
                await conn.execute(text("SELECT id FROM deals WHERE tenant_id = :t ORDER BY id"), {"t": t})
            ).fetchall()
            snap[t] = [str(r[0]) for r in rows]
    return snap


def _args(**kw) -> Any:
    argv: list[str] = ["--internal-org", kw.pop("internal_org", INTERNAL_ORG)]
    for d in kw.pop("deal_ids", []):
        argv += ["--deal-id", d]
    for t in kw.pop("fold_tenants", []):
        argv += ["--fold-tenant", t]
    if kw.pop("execute", False):
        argv.append("--execute")
    if kw.pop("move_whole", False):
        argv.append("--move-whole-tenant")
    assert not kw, kw
    return rollout.build_parser().parse_args(argv)


@pytest.mark.asyncio
async def test_rollout_dry_run_prints_the_plan_and_writes_nothing() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.api.deals import coerce_org_tenant_id
    from app.database import get_engine
    from app.main import app
    from app.tenants import get_tenant, reset_tenant_cache

    old_tenant = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deal = await _seed_deal(client, old_tenant, "Sam's old-org deal")
    engine = get_engine()
    before = await _snapshot(engine, [old_tenant])

    out = io.StringIO()
    code = await rollout.run(_args(deal_ids=[deal], fold_tenants=[old_tenant]), out=out)
    text = out.getvalue()

    assert code == 0, text
    assert "DRY RUN" in text and "nothing was written" in text
    assert "WOULD INSERT" in text and INTERNAL_ORG in text
    assert f"source tenant {old_tenant}" in text
    assert deal in text and "Sam's old-org deal" in text
    assert "deals " in text and "rows that would move" in text
    assert "REQUIRE_REGISTERED_TENANT" in text and "/health.auth.registered_tenant_required" in text
    assert "Clerk dashboard — BY HAND" in text

    after = await _snapshot(engine, [old_tenant])
    assert after == before, (before, after)
    reset_tenant_cache()
    assert await get_tenant(coerce_org_tenant_id(INTERNAL_ORG), use_cache=False) is None


@pytest.mark.asyncio
async def test_rollout_reports_a_deal_under_no_known_tenant() -> None:
    out = io.StringIO()
    unknown = str(uuid.uuid4())
    code = await rollout.run(_args(deal_ids=[unknown]), out=out)
    text = out.getvalue()
    assert code == 0
    assert "NOT FOUND under any known tenant" in text and unknown in text
    # …and --execute refuses rather than guessing.
    code = await rollout.run(_args(deal_ids=[unknown], execute=True), out=io.StringIO())
    assert code == 2


@pytest.mark.asyncio
async def test_rollout_execute_registers_and_folds_only_fully_listed_tenants() -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.api.deals import coerce_org_tenant_id
    from app.database import get_engine
    from app.main import app
    from app.tenants import get_tenant, reset_tenant_cache

    target = coerce_org_tenant_id(INTERNAL_ORG)
    assert target is not None
    old_tenant = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await _seed_deal(client, old_tenant, "Listed deal")
        unlisted = await _seed_deal(client, old_tenant, "Unlisted deal")
    engine = get_engine()

    # A tenant with an unlisted deal is refused without --move-whole-tenant…
    code = await rollout.run(_args(deal_ids=[listed], fold_tenants=[old_tenant], execute=True), out=io.StringIO())
    assert code == 2
    async with engine.connect() as conn:
        still = (await conn.execute(text("SELECT COUNT(*) FROM deals WHERE tenant_id = :t"), {"t": old_tenant})).scalar_one()
    assert still == 2
    reset_tenant_cache()
    assert await get_tenant(target, use_cache=False) is None  # the register step did not run either

    # …and folded whole with it.
    out = io.StringIO()
    code = await rollout.run(
        _args(deal_ids=[listed], fold_tenants=[old_tenant], execute=True, move_whole=True), out=out
    )
    assert code == 0, out.getvalue()
    reset_tenant_cache()
    row = await get_tenant(target, use_cache=False)
    assert row is not None and row.kind == "internal" and row.status == "active"
    async with engine.connect() as conn:
        moved = (
            await conn.execute(
                text("SELECT id FROM deals WHERE tenant_id = :t ORDER BY name"), {"t": str(target)}
            )
        ).fetchall()
        left = (await conn.execute(text("SELECT COUNT(*) FROM deals WHERE tenant_id = :t"), {"t": old_tenant})).scalar_one()
    assert {str(r[0]) for r in moved} >= {listed, unlisted}
    assert left == 0
    assert "registered org_RolloutTestInternal" in out.getvalue()
    assert "deals " in out.getvalue() and "moved 2" in out.getvalue()


# ─────────────────────────── holes the checker found (2026-10-17) ───────────────────────────


@pytest.mark.asyncio
async def test_exports_backfill_and_memo_edit_404_a_foreign_deal(
    two_tenants: tuple[TenantFixture, TenantFixture],  # noqa: F811 — pytest fixture
) -> None:
    """The first run of ``isolation_check.py`` against the local app found
    three deal-scoped routes answering a stranger 2xx:

    * the three ``/export/*`` routes built an EMPTY payload for a foreign deal
      (``load_live_payload`` never raises) and served a blank file;
    * ``POST …/backfill_embeddings`` returned its "skipped" envelope before
      the tenant check on SQLite / no-Voyage workers;
    * ``POST …/memo/{section}/edits`` appended an edit + audit row against a
      foreign deal id with no ownership gate (the GET had one).

    All three now 404 like every other deal route; the owner still gets 2xx
    on the two cheap ones (exports are not built here — they are slow and
    the stranger-side 404 is the load-bearing assertion).
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_a, tenant_b = two_tenants
    b = {"X-Tenant-Id": tenant_b.tenant_id}
    a = {"X-Tenant-Id": tenant_a.tenant_id}
    deal = tenant_a.deal_id
    edit = {"original_body": "draft", "new_body": "final", "comment": "probe"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (
            f"/deals/{deal}/export/excel",
            f"/deals/{deal}/export/memo.pdf",
            f"/deals/{deal}/export/presentation.pptx",
        ):
            r = await client.get(path, headers=b)
            assert r.status_code == 404, (path, r.status_code, r.headers.get("content-type"))
            assert "not found" in r.text

        r = await client.post(f"/deals/{deal}/backfill_embeddings", headers=b)
        assert r.status_code == 404, r.text
        r = await client.post(f"/deals/{deal}/backfill_embeddings", headers=a)
        assert r.status_code == 200, r.text  # owner: the SQLite "skipped" envelope

        r = await client.post(f"/deals/{deal}/memo/investment_thesis/edits", json=edit, headers=b)
        assert r.status_code == 404, r.text
        r = await client.get(f"/deals/{deal}/memo/edits", headers=a)
        assert r.status_code == 200 and r.json() == [], r.text  # nothing was written
        r = await client.post(f"/deals/{deal}/memo/investment_thesis/edits", json=edit, headers=a)
        assert r.status_code == 201, r.text
