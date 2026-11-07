"""Platform overview (``GET /admin/platform``) — Fondok internal admins only.

Two firms get seeded with deals, documents, AI calls, engine outputs and
activity. The endpoint must: refuse anyone who is not an admin of an
internal tenant; report each firm's own numbers (never mixing them); and
turn the numbers into plain "needs attention" / "going well" lines.
"""

from __future__ import annotations

import base64
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "fondok-tests-admin-platform.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ALLOW_TEST_INGEST", "true")

_TEST_KID = "test-kid-platform"

INTERNAL_ORG = "org_FondokInternalPlatform"
FIRM_A_ORG = "org_HarborPointPlatform"
FIRM_B_ORG = "org_NorthBayPlatform"
STRANGER_ORG = "org_UnregisteredPlatform"


def _b64url_uint(value: int) -> str:
    n = value.bit_length()
    length = (n + 7) // 8
    raw = value.to_bytes(length, "big") if length > 0 else b"\x00"
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def _rsa_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(rsa_key: Any, claims: dict[str, Any]) -> str:
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization

    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    payload = {"iat": int(time.time()), "exp": int(time.time()) + 300, **claims}
    return pyjwt.encode(payload, pem, algorithm="RS256", headers={"kid": _TEST_KID})


def _token(rsa_key: Any, org: str, role: str = "org:admin", **extra: Any) -> dict[str, str]:
    claims = {"sub": "user_x", "org_id": org, "org_role": role, **extra}
    return {"Authorization": f"Bearer {_sign(rsa_key, claims)}"}


@pytest.fixture(autouse=True)
def _install_stub_jwks(_rsa_key, monkeypatch):
    from jwt.algorithms import RSAAlgorithm

    from app.auth import clerk_jwt

    numbers = _rsa_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": _TEST_KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }
    keys = {_TEST_KID: RSAAlgorithm.from_jwk(jwk)}
    monkeypatch.setattr(clerk_jwt, "_JWKS_KEYS", keys)
    monkeypatch.setattr(clerk_jwt, "_JWKS_FETCHED_AT", time.monotonic())
    monkeypatch.setattr(clerk_jwt, "_load_jwks", lambda *, force=False: keys)
    yield


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch):
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_engine
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache

    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "0")
    monkeypatch.setenv("DB_SET_TENANT_GUC", "0")
    get_settings.cache_clear()
    reset_tenant_cache()
    await run_startup_migrations()
    async with get_engine().begin() as conn:
        for table in (
            "tenants", "model_calls", "engine_outputs", "audit_log", "memo_edits",
            "cell_comments", "verification_reports", "critic_findings", "documents", "deals",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))
    yield
    get_settings.cache_clear()
    reset_tenant_cache()


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _tid(org: str) -> str:
    from app.api.deals import coerce_org_tenant_id

    tid = coerce_org_tenant_id(org)
    assert tid is not None
    return str(tid)


async def _register(org: str, name: str, kind: str, status: str = "active") -> None:
    from app.tenants import upsert_tenant

    await upsert_tenant(tenant_id=uuid.UUID(_tid(org)), clerk_org_id=org, name=name, kind=kind, status=status)


async def _seed_firm(
    org: str,
    *,
    deals: int,
    docs_ok: int,
    docs_failed: int,
    docs_stuck: int,
    ai_calls: int,
    ai_errors: int,
    cost_each: float,
    engine_ok: int,
    engine_failed: int,
    actors: list[str],
    last_activity_days_ago: int = 1,
) -> None:
    from sqlalchemy import text

    from app.database import get_engine

    t = _tid(org)
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)
    async with get_engine().begin() as conn:
        deal_ids = [str(uuid.uuid4()) for _ in range(deals)]
        for d in deal_ids:
            await conn.execute(
                text(
                    "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                    "VALUES (:id, :t, 'Hotel', 'Draft', :c, :c)"
                ),
                {"id": d, "t": t, "c": recent},
            )
        deal = deal_ids[0]
        for status, n, when in (
            ("EXTRACTED", docs_ok, recent),
            ("FAILED", docs_failed, recent),
            ("Processing", docs_stuck, now - timedelta(hours=3)),
        ):
            for _ in range(n):
                await conn.execute(
                    text(
                        "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at, page_count) "
                        "VALUES (:id, :d, :t, 'f.pdf', 'T12', :s, :u, 10)"
                    ),
                    {"id": str(uuid.uuid4()), "d": deal, "t": t, "s": status, "u": when},
                )
        for i in range(ai_calls):
            await conn.execute(
                text(
                    "INSERT INTO model_calls (id, deal_id, tenant_id, agent_name, model, input_tokens, output_tokens, "
                    "cache_read_tokens, cache_creation_tokens, cost_usd, latency_ms, status, created_at) "
                    "VALUES (:id, :d, :t, 'extractor', 'claude-sonnet-4-6', 1000, 100, 500, 0, :c, 1200, :s, :at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "d": deal,
                    "t": t,
                    "c": cost_each,
                    "s": "error" if i < ai_errors else "ok",
                    "at": recent,
                },
            )
        run = str(uuid.uuid4())
        for status, n in (("complete", engine_ok), ("failed", engine_failed)):
            for _ in range(n):
                await conn.execute(
                    text(
                        "INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, engine_name, status, started_at, runtime_ms) "
                        "VALUES (:id, :d, :t, :r, 'returns', :s, :at, 40)"
                    ),
                    {"id": str(uuid.uuid4()), "d": deal, "t": t, "r": run, "s": status, "at": recent},
                )
        for a in actors:
            await conn.execute(
                text(
                    "INSERT INTO audit_log (id, tenant_id, deal_id, actor_id, action, resource_type, created_at) "
                    "VALUES (:id, :t, :d, :a, 'deal.updated', 'deal', :at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "t": t,
                    "d": deal,
                    "a": a,
                    "at": now - timedelta(days=last_activity_days_ago),
                },
            )


async def _seed_two_firms() -> None:
    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    await _register(FIRM_A_ORG, "Harbor Point", "external")
    await _register(FIRM_B_ORG, "North Bay", "external", status="pending_review")
    await _seed_firm(
        FIRM_A_ORG, deals=3, docs_ok=4, docs_failed=1, docs_stuck=1, ai_calls=10, ai_errors=2,
        cost_each=0.5, engine_ok=8, engine_failed=2, actors=["user_a1", "user_a2", "user_a1"],
    )
    await _seed_firm(
        FIRM_B_ORG, deals=1, docs_ok=2, docs_failed=0, docs_stuck=0, ai_calls=4, ai_errors=0,
        cost_each=0.25, engine_ok=5, engine_failed=0, actors=["user_b1"], last_activity_days_ago=20,
    )


@pytest.mark.asyncio
async def test_only_internal_admins_get_the_platform_view(_rsa_key) -> None:
    await _seed_two_firms()
    async with await _client() as c:
        assert (await c.get("/admin/platform", headers=_token(_rsa_key, FIRM_A_ORG))).status_code == 403
        assert (await c.get("/admin/platform", headers=_token(_rsa_key, STRANGER_ORG))).status_code == 403
        member = await c.get("/admin/platform", headers=_token(_rsa_key, INTERNAL_ORG, role="org:member"))
        assert member.status_code == 403
        assert (await c.get("/admin/platform")).status_code in (401, 403)
        ok = await c.get("/admin/platform", headers=_token(_rsa_key, INTERNAL_ORG))
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_rejects_unknown_window(_rsa_key) -> None:
    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    async with await _client() as c:
        r = await c.get("/admin/platform?days=13", headers=_token(_rsa_key, INTERNAL_ORG))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_each_firm_reports_only_its_own_numbers(_rsa_key) -> None:
    await _seed_two_firms()
    async with await _client() as c:
        body = (await c.get("/admin/platform?days=30", headers=_token(_rsa_key, INTERNAL_ORG))).json()

    firms = {f["name"]: f for f in body["firms"]}
    assert set(firms) == {"Fondok Internal", "Harbor Point", "North Bay"}
    a, b = firms["Harbor Point"], firms["North Bay"]

    assert a["deals"]["total"] == 3 and b["deals"]["total"] == 1
    assert a["documents"]["uploaded"] == 6 and a["documents"]["failed"] == 1 and a["documents"]["stuck"] == 1
    assert b["documents"]["uploaded"] == 2 and b["documents"]["failed"] == 0 and b["documents"]["stuck"] == 0
    assert a["ai"]["calls"] == 10 and a["ai"]["errors"] == 2 and a["ai"]["cost_usd"] == pytest.approx(5.0)
    assert b["ai"]["calls"] == 4 and b["ai"]["cost_usd"] == pytest.approx(1.0)
    assert a["engines"]["failed"] == 2 and a["engines"]["failure_rate"] == pytest.approx(0.2)
    assert a["activity"]["active_people"] == 2 and b["activity"]["active_people"] == 1
    assert {p["actor_id"] for p in a["activity"]["people"]} == {"user_a1", "user_a2"}
    assert {p["actor_id"] for p in b["activity"]["people"]} == {"user_b1"}

    t = body["totals"]
    assert t["firms"] == 3 and t["firms_pending"] == 1
    assert t["deals_total"] == 4
    assert t["ai_cost_usd"] == pytest.approx(6.0)
    assert t["ai_calls"] == 14 and t["ai_errors"] == 2
    assert sum(body["daily"]["cost_usd"].values()) == pytest.approx(6.0)
    # The firm with problems is listed first.
    assert body["firms"][0]["name"] == "Harbor Point"


@pytest.mark.asyncio
async def test_signals_name_what_is_wrong_and_what_is_working(_rsa_key) -> None:
    await _seed_two_firms()
    async with await _client() as c:
        body = (await c.get("/admin/platform", headers=_token(_rsa_key, INTERNAL_ORG))).json()

    def lines(firm: str, kind: str) -> str:
        return " | ".join(s["text"] for s in body["signals"] if s["firm"] == firm and s["kind"] == kind)

    bad_a = lines("Harbor Point", "bad")
    assert "stuck in processing" in bad_a
    assert "failed to extract" in bad_a
    assert "model step(s) failed" in bad_a
    assert "AI call(s) errored" in bad_a
    bad_b = lines("North Bay", "bad")
    assert "Waiting for approval" in bad_b
    assert "extracted cleanly" in lines("North Bay", "good")
    assert "Every model step ran cleanly" in lines("North Bay", "good")
    # Bad lines sort before good ones.
    kinds = [s["kind"] for s in body["signals"]]
    assert kinds == sorted(kinds, key=lambda k: k != "bad")


@pytest.mark.asyncio
async def test_quiet_active_firm_is_flagged(_rsa_key) -> None:
    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    await _register(FIRM_B_ORG, "North Bay", "external")
    await _seed_firm(
        FIRM_B_ORG, deals=1, docs_ok=0, docs_failed=0, docs_stuck=0, ai_calls=0, ai_errors=0,
        cost_each=0, engine_ok=0, engine_failed=0, actors=["user_b1"], last_activity_days_ago=20,
    )
    async with await _client() as c:
        body = (await c.get("/admin/platform?days=30", headers=_token(_rsa_key, INTERNAL_ORG))).json()
    assert any(s["firm"] == "North Bay" and "Quiet" in s["text"] for s in body["signals"])


@pytest.mark.asyncio
async def test_each_firm_runs_under_its_own_tenant(_rsa_key, monkeypatch) -> None:
    """The rollup for firm X runs with current_tenant_id == X (so the RLS GUC
    names X), and the caller's own tenant is restored afterwards."""
    from app.api import admin_platform
    from app.auth.context import current_tenant_id

    await _seed_two_firms()
    seen: list[tuple[str, str | None]] = []

    async def spy(session, tenant, since, now):
        seen.append((tenant, current_tenant_id.get()))
        return await admin_platform._firm_rollup(session, tenant, since, now)

    from app.tenants import list_tenants

    tenants = await list_tenants()
    before = current_tenant_id.set("caller-tenant")
    try:
        now = datetime.now(UTC)
        await admin_platform._collect(tenants, now - timedelta(days=30), now, rollup=spy)
        assert current_tenant_id.get() == "caller-tenant"
    finally:
        current_tenant_id.reset(before)
    assert seen and all(t == published for t, published in seen)
    assert {t for t, _ in seen} == {str(x.id) for x in tenants}


@pytest.mark.asyncio
async def test_fondok_workspace_gets_no_adoption_warning(_rsa_key) -> None:
    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    async with await _client() as c:
        body = (await c.get("/admin/platform", headers=_token(_rsa_key, INTERNAL_ORG))).json()
    assert not [s for s in body["signals"] if s["firm"] == "Fondok Internal" and s["area"] == "adoption"]


# ─────────────────────── Fondok staff admin (account-level role) ───────────────────────

STAFF = {"fondok_staff": "admin", "two_factor": "true"}


@pytest.mark.asyncio
async def test_staff_admin_sees_platform_from_a_client_workspace(_rsa_key) -> None:
    """A verified staff admin with two-factor on is a Fondok admin in ANY
    workspace — even as a plain member of a client firm."""
    await _seed_two_firms()
    async with await _client() as c:
        for role in ("org:member", "org:admin"):
            r = await c.get("/admin/platform", headers=_token(_rsa_key, FIRM_A_ORG, role=role, **STAFF))
            assert r.status_code == 200, (role, r.text)
            assert {f["name"] for f in r.json()["firms"]} == {"Fondok Internal", "Harbor Point", "North Bay"}
        r = await c.get("/admin/tenants", headers=_token(_rsa_key, FIRM_A_ORG, role="org:member", **STAFF))
        assert r.status_code == 200 and r.json()["scope"] == "all"
        r = await c.get("/admin/health", headers=_token(_rsa_key, FIRM_A_ORG, role="org:member", **STAFF))
        assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"fondok_staff": "admin"},  # no two-factor claim
        {"fondok_staff": "admin", "two_factor": "false"},
        {"fondok_staff": "admin", "two_factor": ""},
        {"fondok_staff": "", "two_factor": "true"},  # template renders empty metadata as ""
        {"fondok_staff": "member", "two_factor": "true"},
        {"fondok_staff": ["admin"], "two_factor": "true"},
        {"two_factor": "true"},
    ],
)
async def test_staff_claim_fails_closed(_rsa_key, claims) -> None:
    await _seed_two_firms()
    async with await _client() as c:
        r = await c.get("/admin/platform", headers=_token(_rsa_key, FIRM_A_ORG, role="org:admin", **claims))
        assert r.status_code == 403
        r = await c.get("/admin/tenants", headers=_token(_rsa_key, FIRM_A_ORG, role="org:admin", **claims))
        assert r.json()["scope"] == "own"


@pytest.mark.asyncio
async def test_two_factor_requirement_can_be_switched_off(_rsa_key, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("FONDOK_STAFF_REQUIRE_2FA", "0")
    get_settings.cache_clear()
    await _seed_two_firms()
    async with await _client() as c:
        r = await c.get("/admin/platform", headers=_token(_rsa_key, FIRM_A_ORG, role="org:member", fondok_staff="admin"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_staff_admin_still_cannot_reach_another_firms_deals(_rsa_key) -> None:
    """The staff role opens the Fondok-admin views only. Deal data stays
    scoped to the active workspace."""
    from sqlalchemy import text

    from app.database import get_engine

    await _seed_two_firms()
    async with get_engine().connect() as conn:
        b_deal = (
            await conn.execute(text("SELECT id FROM deals WHERE tenant_id = :t"), {"t": _tid(FIRM_B_ORG)})
        ).scalar_one()
    async with await _client() as c:
        h = _token(_rsa_key, FIRM_A_ORG, role="org:admin", **STAFF)
        listed = (await c.get("/deals", headers=h)).json()
        rows = listed if isinstance(listed, list) else listed.get("deals", listed.get("items", []))
        assert str(b_deal) not in {str(d.get("id")) for d in rows}
        assert (await c.get(f"/deals/{b_deal}", headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_no_fondok_workspace_can_be_demoted_over_http(_rsa_key) -> None:
    """A staff admin calling from a client workspace is not covered by the
    own-tenant guard, so the internal row is protected on its own."""
    await _seed_two_firms()
    async with await _client() as c:
        h = _token(_rsa_key, FIRM_A_ORG, role="org:member", **STAFF)
        for kind, status in (("external", "active"), ("internal", "suspended")):
            r = await c.post(
                "/admin/tenants",
                json={"clerk_org_id": INTERNAL_ORG, "name": "Fondok Internal", "kind": kind, "status": status},
                headers=h,
            )
            assert r.status_code == 409 and r.json()["detail"] == "cannot_demote_internal_tenant"
        # Approving a client firm still works for staff.
        r = await c.post(
            "/admin/tenants",
            json={"clerk_org_id": FIRM_B_ORG, "name": "North Bay", "kind": "external", "status": "active"},
            headers=h,
        )
        assert r.status_code == 201 and r.json()["status"] == "active"
