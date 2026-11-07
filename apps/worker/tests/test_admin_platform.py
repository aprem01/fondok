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
            "cell_comments", "verification_reports", "critic_findings", "documents", "deals", "support_grants",
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


@pytest.mark.asyncio
async def test_staff_policy_change_requires_the_fondok_workspace(_rsa_key) -> None:
    """The audit row for a data-policy change lands in the ACTING workspace's
    log, so a staff admin inside a client workspace must not write one there
    (it would name another firm). Reading the policy is fine anywhere."""
    from sqlalchemy import text

    from app.database import get_engine

    await _seed_two_firms()
    b = _tid(FIRM_B_ORG)
    async with await _client() as c:
        from_client = _token(_rsa_key, FIRM_A_ORG, role="org:admin", **STAFF)
        assert (await c.get(f"/admin/tenants/{b}/data_policies", headers=from_client)).status_code == 200
        r = await c.patch(
            f"/admin/tenants/{b}/data_policies", json={"costar_derived_defaults": "withhold"}, headers=from_client
        )
        assert r.status_code == 403 and r.json()["detail"] == "data_policy_change_requires_fondok_workspace"
        async with get_engine().connect() as conn:
            leaked = (
                await conn.execute(
                    text("SELECT count(*) FROM audit_log WHERE tenant_id = :a AND action = 'tenant.data_policy_changed'"),
                    {"a": _tid(FIRM_A_ORG)},
                )
            ).scalar_one()
        assert leaked == 0
        from_fondok = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
        r = await c.patch(
            f"/admin/tenants/{b}/data_policies", json={"costar_derived_defaults": "withhold"}, headers=from_fondok
        )
        assert r.status_code == 200


# ─────────────────────── support access (time-limited, read-only, logged) ───────────────────────


async def _audit_actions(org: str) -> list[str]:
    from sqlalchemy import text

    from app.database import get_engine

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT action FROM audit_log WHERE tenant_id = :t AND action LIKE 'support.%' ORDER BY created_at"),
                {"t": _tid(org)},
            )
        ).fetchall()
    return [r[0] for r in rows]


async def _open(c, rsa_key, org: str = FIRM_A_ORG, *, hours: int = 1, headers: dict | None = None):
    return await c.post(
        "/admin/support-access",
        json={"tenant_id": _tid(org), "reason": "Checking a failed T-12 extraction", "hours": hours},
        headers=headers or _token(rsa_key, INTERNAL_ORG, role="org:member", **STAFF),
    )


def _deal_ids(body: Any) -> set[str]:
    rows = body if isinstance(body, list) else body.get("deals", body.get("items", []))
    return {str(d.get("id")) for d in rows}


@pytest.mark.asyncio
async def test_support_access_reads_the_firm_and_logs_it_there(_rsa_key) -> None:
    from sqlalchemy import text

    from app.database import get_engine

    await _seed_two_firms()
    async with get_engine().connect() as conn:
        a_deals = {str(r[0]) for r in (await conn.execute(text("SELECT id FROM deals WHERE tenant_id = :t"), {"t": _tid(FIRM_A_ORG)})).fetchall()}
    staff = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
    async with await _client() as c:
        opened = await _open(c, _rsa_key)
        assert opened.status_code == 201, opened.text
        grant = opened.json()
        assert grant["firm"] == "Harbor Point" and grant["ended_at"] is None

        as_firm = {**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)}
        r = await c.get("/deals", headers=as_firm)
        assert r.status_code == 200
        assert _deal_ids(r.json()) == a_deals
        # Without the header the same person sees their own workspace, not the firm.
        assert not (_deal_ids((await c.get("/deals", headers=staff)).json()) & a_deals)
        # Polling the same page again is not logged twice.
        await c.get("/deals", headers=as_firm)

        listed = (await c.get("/admin/support-access", headers=staff)).json()["grants"]
        assert [g["id"] for g in listed] == [grant["id"]]

        assert (await c.delete(f"/admin/support-access/{grant['id']}", headers=staff)).status_code == 200
        r = await c.get("/deals", headers=as_firm)
        assert r.status_code == 403 and r.json()["detail"] == "support_access_not_granted"

    assert await _audit_actions(FIRM_A_ORG) == ["support.access_started", "support.viewed", "support.access_ended"]
    assert await _audit_actions(INTERNAL_ORG) == []
    assert await _audit_actions(FIRM_B_ORG) == []


@pytest.mark.asyncio
async def test_support_access_is_read_only(_rsa_key) -> None:
    await _seed_two_firms()
    staff = _token(_rsa_key, INTERNAL_ORG, role="org:admin", **STAFF)
    async with await _client() as c:
        assert (await _open(c, _rsa_key)).status_code == 201
        as_firm = {**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)}
        for method, path, body in (
            ("POST", "/deals", {"name": "Should not exist"}),
            ("PATCH", f"/deals/{uuid.uuid4()}", {"name": "x"}),
            ("DELETE", f"/deals/{uuid.uuid4()}", None),
        ):
            r = await c.request(method, path, json=body, headers=as_firm)
            assert r.status_code == 403 and r.json()["detail"] == "support_access_read_only", (method, r.text)


@pytest.mark.asyncio
async def test_support_header_without_a_live_grant_is_refused(_rsa_key) -> None:
    from sqlalchemy import text

    from app.database import get_engine

    await _seed_two_firms()
    staff = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
    as_firm = {**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)}
    async with await _client() as c:
        assert (await c.get("/deals", headers=as_firm)).json()["detail"] == "support_access_not_granted"
        # Someone else's grant does not work for me.
        other = _sign(_rsa_key, {"sub": "user_other_staff", "org_id": INTERNAL_ORG, "org_role": "org:member", **STAFF})
        assert (await _open(c, _rsa_key, headers={"Authorization": f"Bearer {other}"})).status_code == 201
        assert (await c.get("/deals", headers=as_firm)).json()["detail"] == "support_access_not_granted"
        # An expired grant stops matching.
        assert (await _open(c, _rsa_key)).status_code == 201
        async with get_engine().begin() as conn:
            await conn.execute(
                text("UPDATE support_grants SET expires_at = :past WHERE staff_user_id = 'user_x'"),
                {"past": datetime.now(UTC) - timedelta(minutes=1)},
            )
        assert (await c.get("/deals", headers=as_firm)).json()["detail"] == "support_access_not_granted"
        # A malformed header is refused too.
        bad = {**staff, "X-Support-Tenant": "not-a-uuid"}
        assert (await c.get("/deals", headers=bad)).status_code == 403


@pytest.mark.asyncio
async def test_only_two_factor_staff_can_open_or_use_support_access(_rsa_key) -> None:
    await _seed_two_firms()
    async with await _client() as c:
        for headers in (
            _token(_rsa_key, INTERNAL_ORG, role="org:admin"),  # Fondok workspace admin, no staff flag
            _token(_rsa_key, INTERNAL_ORG, role="org:admin", fondok_staff="admin"),  # staff without 2FA
            _token(_rsa_key, FIRM_B_ORG, role="org:admin"),  # a client admin
        ):
            r = await _open(c, _rsa_key, headers=headers)
            assert r.status_code == 403 and r.json()["detail"] == "support_access_requires_staff"
            r = await c.get("/deals", headers={**headers, "X-Support-Tenant": _tid(FIRM_A_ORG)})
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_support_access_only_for_approved_client_firms(_rsa_key) -> None:
    await _seed_two_firms()  # North Bay is pending_review
    async with await _client() as c:
        assert (await _open(c, _rsa_key, INTERNAL_ORG)).status_code == 404
        assert (await _open(c, _rsa_key, FIRM_B_ORG)).status_code == 404
        r = await _open(c, _rsa_key, hours=3)
        assert r.status_code == 422
        assert (await _open(c, _rsa_key, hours=48)).status_code == 422
        # 90-day onboarding-period grant: accepted, expires ~90 days out.
        r = await _open(c, _rsa_key, hours=2160)
        assert r.status_code == 201, r.text
        expires = datetime.fromisoformat(r.json()["expires_at"])
        assert timedelta(days=89, hours=23) < expires - datetime.now(UTC) <= timedelta(days=90)
        r = await c.post(
            "/admin/support-access",
            json={"tenant_id": _tid(FIRM_A_ORG), "reason": "   short   ", "hours": 1},
            headers=_token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF),
        )
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_support_page_that_cannot_be_logged_is_not_served(_rsa_key, monkeypatch) -> None:
    from app import support_access

    await _seed_two_firms()
    staff = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
    async with await _client() as c:
        assert (await _open(c, _rsa_key)).status_code == 201

        async def broken(**kw):
            return False

        monkeypatch.setattr(support_access, "audit_in_firm", broken)
        support_access._VIEWED.clear()
        r = await c.get("/deals", headers={**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)})
        assert r.status_code == 503 and r.json()["detail"] == "support_access_audit_unavailable"


@pytest.mark.asyncio
async def test_grant_is_cancelled_when_its_start_cannot_be_logged(_rsa_key, monkeypatch) -> None:
    from app import support_access

    await _seed_two_firms()

    async def broken(**kw):
        return False

    monkeypatch.setattr(support_access, "audit_in_firm", broken)
    async with await _client() as c:
        r = await _open(c, _rsa_key)
        assert r.status_code == 503
    assert await support_access.active_grant(staff_user_id="user_x", tenant_id=uuid.UUID(_tid(FIRM_A_ORG))) is None


@pytest.mark.asyncio
async def test_each_search_is_logged_separately(_rsa_key) -> None:
    from sqlalchemy import text

    from app.database import get_engine

    await _seed_two_firms()
    staff = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
    as_firm = {**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)}
    async with await _client() as c:
        assert (await _open(c, _rsa_key)).status_code == 201
        await c.get("/deals?q=marriott", headers=as_firm)
        await c.get("/deals?q=hilton", headers=as_firm)
        await c.get("/deals?q=hilton", headers=as_firm)
    async with get_engine().connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND action = 'support.viewed'"),
                {"t": _tid(FIRM_A_ORG)},
            )
        ).scalar_one()
    assert n == 2


@pytest.mark.asyncio
async def test_download_links_do_not_outlive_the_grant() -> None:
    from app.api.documents import _DOWNLOAD_URL_TTL_S, _download_ttl_s
    from app.auth.context import current_support_expires_at

    assert _download_ttl_s() == _DOWNLOAD_URL_TTL_S
    token = current_support_expires_at.set(datetime.now(UTC) + timedelta(seconds=90))
    try:
        assert 1 <= _download_ttl_s() <= 90
        current_support_expires_at.set(datetime.now(UTC) - timedelta(seconds=5))
        assert _download_ttl_s() == 1
    finally:
        current_support_expires_at.reset(token)


@pytest.mark.asyncio
async def test_compare_view_runs_nothing_under_support_access(_rsa_key, monkeypatch) -> None:
    """Scenarios compare normally runs a never-run scenario on first view (a
    write). Under support access it must not."""
    from sqlalchemy import text

    from app.api import scenarios as scenarios_api
    from app.database import get_engine

    await _seed_two_firms()
    async with get_engine().connect() as conn:
        deal = (
            await conn.execute(text("SELECT id FROM deals WHERE tenant_id = :t LIMIT 1"), {"t": _tid(FIRM_A_ORG)})
        ).scalar_one()
    async with await _client() as c:
        created = await c.post(
            f"/deals/{deal}/scenarios", json={"name": "Downside"}, headers=_token(_rsa_key, FIRM_A_ORG)
        )
        assert created.status_code in (200, 201), created.text
        sid = created.json()["id"]

        calls: list[str] = []

        async def spy(*a, **kw):
            calls.append(kw.get("scenario_id", "?"))

        monkeypatch.setattr(scenarios_api, "run_all_engines", spy)
        assert (await _open(c, _rsa_key)).status_code == 201
        staff = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
        r = await c.get(
            f"/deals/{deal}/scenarios/compare?scenario_ids={sid}",
            headers={**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)},
        )
        assert r.status_code == 200, r.text
        assert calls == []
        # The firm's own view still materialises as before.
        r = await c.get(f"/deals/{deal}/scenarios/compare?scenario_ids={sid}", headers=_token(_rsa_key, FIRM_A_ORG))
        assert r.status_code == 200 and calls == [sid]


@pytest.mark.asyncio
async def test_firm_admin_can_see_and_end_support_access_on_their_firm(_rsa_key) -> None:
    from sqlalchemy import text

    from app.database import get_engine

    await _seed_two_firms()
    staff = _token(_rsa_key, INTERNAL_ORG, role="org:member", **STAFF)
    firm_admin = _token(_rsa_key, FIRM_A_ORG, role="org:admin")
    firm_member = _token(_rsa_key, FIRM_A_ORG, role="org:member")
    other_firm_admin = _token(_rsa_key, FIRM_B_ORG, role="org:admin")
    async with await _client() as c:
        grant = (await _open(c, _rsa_key)).json()
        as_firm = {**staff, "X-Support-Tenant": _tid(FIRM_A_ORG)}
        assert (await c.get("/deals", headers=as_firm)).status_code == 200

        # The firm's admin sees it (without the staff member's id); a member and another firm do not.
        mine = (await c.get("/admin/support-access/on-my-firm", headers=firm_admin)).json()["grants"]
        assert [g["id"] for g in mine] == [grant["id"]] and "staff_user_id" not in mine[0]
        assert mine[0]["reason"] == "Checking a failed T-12 extraction"
        assert (await c.get("/admin/support-access/on-my-firm", headers=firm_member)).status_code == 403
        assert (await c.get("/admin/support-access/on-my-firm", headers=other_firm_admin)).json()["grants"] == []
        # Another firm's admin cannot end it; the staff header cannot be used to pose as the firm here.
        assert (await c.delete(f"/admin/support-access/on-my-firm/{grant['id']}", headers=other_firm_admin)).status_code == 404
        assert (await c.delete(f"/admin/support-access/on-my-firm/{grant['id']}", headers=as_firm)).status_code in (403, 404)

        assert (await c.delete(f"/admin/support-access/on-my-firm/{grant['id']}", headers=firm_admin)).status_code == 200
        r = await c.get("/deals", headers=as_firm)
        assert r.status_code == 403 and r.json()["detail"] == "support_access_not_granted"
        assert (await c.get("/admin/support-access", headers=staff)).json()["grants"] == []

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT payload FROM audit_log WHERE tenant_id = :t AND action = 'support.access_ended'"),
                {"t": _tid(FIRM_A_ORG)},
            )
        ).fetchall()
    assert len(rows) == 1
    payload = rows[0][0]
    payload = payload if isinstance(payload, dict) else __import__("json").loads(payload)
    assert payload["metadata"]["ended_by"] == "firm_admin"


@pytest.mark.asyncio
async def test_long_support_grants_sunset(_rsa_key, monkeypatch) -> None:
    """After LONG_GRANT_OPEN_UNTIL, 90-day grants can't be opened; short ones still can."""
    from app import support_access

    await _seed_two_firms()
    assert support_access.long_grant_allowed(24, now=support_access.LONG_GRANT_OPEN_UNTIL + timedelta(days=1))
    assert not support_access.long_grant_allowed(2160, now=support_access.LONG_GRANT_OPEN_UNTIL)
    assert support_access.long_grant_allowed(2160, now=support_access.LONG_GRANT_OPEN_UNTIL - timedelta(seconds=1))

    monkeypatch.setattr(support_access, "LONG_GRANT_OPEN_UNTIL", datetime.now(UTC) - timedelta(minutes=1))
    async with await _client() as c:
        r = await _open(c, _rsa_key, hours=2160)
        assert r.status_code == 422
        assert "long_support_grants_closed" in r.text
        assert (await _open(c, _rsa_key, hours=24)).status_code == 201
