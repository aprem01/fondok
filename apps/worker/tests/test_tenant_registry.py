"""FON-93 — tenant registry, enforcement flag, admin endpoints, webhook,
observability scoping, /settings/tenant and the RLS GUC hook.

Clerk's JWKS fetch is patched out exactly as in ``test_rbac.py``: we mint
an RS256 key, prime the module-level cache and sign tokens locally.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "fondok-tests-tenant-registry.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")
os.environ.setdefault("ALLOW_TEST_INGEST", "true")

_TEST_KID = "test-kid-fon93"

INTERNAL_ORG = "org_FondokInternal"
CUSTOMER_ORG = "org_HarborPointCapital"
STRANGER_ORG = "org_SelfCreatedByAnalyst"

# A valid Svix secret: ``whsec_`` + base64 of 24 random bytes.
WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef01234567").decode()


# ─────────────────────── key + JWT helpers (mirrors test_rbac) ───────────────────────


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


def _admin_token(rsa_key: Any, org: str, *, sub: str = "user_admin") -> str:
    return _sign(rsa_key, {"sub": sub, "org_id": org, "org_role": "org:admin"})


def _member_token(rsa_key: Any, org: str, *, sub: str = "user_member") -> str:
    return _sign(rsa_key, {"sub": sub, "org_id": org, "org_role": "org:member"})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
async def _settings_and_registry_reset(monkeypatch):
    """Every test starts with enforcement OFF, no webhook secret, an empty
    registry and a cold cache; the settings cache is cleared on both sides.

    Async (not ``run_until_complete`` on a sync fixture): under the shared
    session loop the sync form raises "loop already running" once earlier
    modules have started it.
    """
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_engine
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache

    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "0")
    monkeypatch.setenv("DB_SET_TENANT_GUC", "0")
    monkeypatch.delenv("CLERK_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()
    reset_tenant_cache()

    await run_startup_migrations()
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM tenants"))
    yield
    get_settings.cache_clear()
    reset_tenant_cache()


def _enable_enforcement(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "1")
    get_settings.cache_clear()


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register(org: str, name: str, kind: str, status: str = "active"):
    from app.api.deals import coerce_org_tenant_id
    from app.tenants import upsert_tenant

    tid = coerce_org_tenant_id(org)
    assert tid is not None
    return await upsert_tenant(
        tenant_id=tid, clerk_org_id=org, name=name, kind=kind, status=status
    )


# ─────────────────────── registry module ───────────────────────


@pytest.mark.asyncio
async def test_registry_upsert_lookup_and_cache() -> None:
    from app.api.deals import coerce_org_tenant_id
    from app.tenants import get_tenant, is_internal, is_registered, list_tenants, upsert_tenant

    tid = coerce_org_tenant_id(CUSTOMER_ORG)
    assert tid is not None
    assert await get_tenant(tid) is None
    assert await is_registered(tid) is False

    stored = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    assert stored.id == tid and stored.status == "active" and stored.kind == "external"
    assert await is_registered(tid) is True
    assert await is_internal(tid) is False

    # Upsert updates in place (approval / suspension path).
    await upsert_tenant(
        tenant_id=tid, clerk_org_id=CUSTOMER_ORG, name="Harbor Point", kind="external", status="suspended"
    )
    assert await is_registered(tid) is False  # cache was cleared by the write
    rows = await list_tenants()
    assert [r.clerk_org_id for r in rows] == [CUSTOMER_ORG]

    # preserve_existing never downgrades.
    await upsert_tenant(
        tenant_id=tid, clerk_org_id=CUSTOMER_ORG, name="x", kind="external",
        status="pending_review", preserve_existing=True,
    )
    assert (await get_tenant(tid, use_cache=False)).status == "suspended"

    with pytest.raises(ValueError):
        await upsert_tenant(tenant_id=tid, clerk_org_id=CUSTOMER_ORG, name="x", kind="weird", status="active")
    with pytest.raises(ValueError):
        await upsert_tenant(tenant_id=tid, clerk_org_id="user_abc", name="x", kind="external", status="active")


# ─────────────────────── enforcement flag ───────────────────────


@pytest.mark.asyncio
async def test_enforcement_off_serves_unregistered_org(_rsa_key) -> None:
    """Default posture: production behaviour unchanged — any org is served."""
    async with await _client() as c:
        r = await c.get("/deals", headers=_bearer(_member_token(_rsa_key, STRANGER_ORG)))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_enforcement_on_refuses_unregistered_suspended_and_pending(
    _rsa_key, monkeypatch
) -> None:
    _enable_enforcement(monkeypatch)
    await _register(CUSTOMER_ORG, "Harbor Point", "external", status="active")
    await _register("org_Suspended", "Gone", "external", status="suspended")
    await _register("org_Pending", "New", "external", status="pending_review")

    async with await _client() as c:
        ok = await c.get("/deals", headers=_bearer(_member_token(_rsa_key, CUSTOMER_ORG)))
        assert ok.status_code == 200, ok.text
        for org in (STRANGER_ORG, "org_Suspended", "org_Pending"):
            r = await c.get("/deals", headers=_bearer(_admin_token(_rsa_key, org)))
            assert r.status_code == 403, (org, r.text)
            assert r.json()["detail"] == "tenant_not_registered"
        # Writes are refused the same way — the dependency is shared.
        w = await c.post(
            "/deals", json={"name": "nope"}, headers=_bearer(_admin_token(_rsa_key, STRANGER_ORG))
        )
        assert w.status_code == 403, w.text
        # /health stays public and reports the posture.
        h = await c.get("/health")
        assert h.json()["auth"]["registered_tenant_required"] is True


@pytest.mark.asyncio
async def test_enforcement_does_not_touch_local_header_path(monkeypatch) -> None:
    """The header path (tests / local dev) is gated by its own flag and is
    not a registry concern — the suite keeps working with enforcement on."""
    _enable_enforcement(monkeypatch)
    async with await _client() as c:
        r = await c.get("/deals", headers={"X-Tenant-Id": "44444444-4444-4444-4444-444444444444"})
    assert r.status_code == 200, r.text


# ─────────────────────── /admin/tenants ───────────────────────


@pytest.mark.asyncio
async def test_admin_tenants_list_scopes_by_kind(_rsa_key) -> None:
    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    await _register(CUSTOMER_ORG, "Harbor Point", "external")

    async with await _client() as c:
        # Internal admin sees everything.
        r = await c.get("/admin/tenants", headers=_bearer(_admin_token(_rsa_key, INTERNAL_ORG)))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scope"] == "all"
        assert {t["clerk_org_id"] for t in body["tenants"]} == {INTERNAL_ORG, CUSTOMER_ORG}

        # External admin sees only its own row.
        r = await c.get("/admin/tenants", headers=_bearer(_admin_token(_rsa_key, CUSTOMER_ORG)))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scope"] == "own"
        assert [t["clerk_org_id"] for t in body["tenants"]] == [CUSTOMER_ORG]

        # Unregistered admin sees nothing (and learns nothing).
        r = await c.get("/admin/tenants", headers=_bearer(_admin_token(_rsa_key, STRANGER_ORG)))
        assert r.status_code == 200, r.text
        assert r.json()["tenants"] == []

        # Members are not admins.
        r = await c.get("/admin/tenants", headers=_bearer(_member_token(_rsa_key, INTERNAL_ORG)))
        assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_admin_tenants_register_requires_internal_caller(_rsa_key) -> None:
    from app.api.deals import coerce_org_tenant_id
    from app.tenants import get_tenant

    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    await _register(CUSTOMER_ORG, "Harbor Point", "external")
    body = {"clerk_org_id": "org_NewInstitution", "name": "New Institution", "kind": "external"}

    async with await _client() as c:
        # External admin may NOT register anyone (including itself).
        r = await c.post("/admin/tenants", json=body, headers=_bearer(_admin_token(_rsa_key, CUSTOMER_ORG)))
        assert r.status_code == 403, r.text
        r = await c.post("/admin/tenants", json=body, headers=_bearer(_admin_token(_rsa_key, STRANGER_ORG)))
        assert r.status_code == 403, r.text
        assert await get_tenant(coerce_org_tenant_id("org_NewInstitution")) is None

        # Internal admin can.
        r = await c.post("/admin/tenants", json=body, headers=_bearer(_admin_token(_rsa_key, INTERNAL_ORG)))
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["clerk_org_id"] == "org_NewInstitution"
        assert created["kind"] == "external" and created["status"] == "active"
        assert created["id"] == str(coerce_org_tenant_id("org_NewInstitution"))

        # Validation: bad kind / non-org id.
        r = await c.post(
            "/admin/tenants",
            json={**body, "kind": "superuser"},
            headers=_bearer(_admin_token(_rsa_key, INTERNAL_ORG)),
        )
        assert r.status_code == 422, r.text
        r = await c.post(
            "/admin/tenants",
            json={**body, "clerk_org_id": "user_notanorg"},
            headers=_bearer(_admin_token(_rsa_key, INTERNAL_ORG)),
        )
        assert r.status_code == 422, r.text

        # Upsert approves a pending org.
        await _register("org_Pending", "Pending Co", "external", status="pending_review")
        r = await c.post(
            "/admin/tenants",
            json={"clerk_org_id": "org_Pending", "name": "Pending Co", "kind": "external", "status": "active"},
            headers=_bearer(_admin_token(_rsa_key, INTERNAL_ORG)),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "active"


# ─────────────────────── /webhooks/clerk ───────────────────────


def _svix_headers(body: bytes, *, secret: str = WEBHOOK_SECRET, ts: int | None = None, msg_id: str = "msg_1") -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    key = base64.b64decode(secret[len("whsec_"):])
    sig = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {
        "svix-id": msg_id,
        "svix-timestamp": str(ts),
        "svix-signature": "v1," + base64.b64encode(sig).decode(),
        "content-type": "application/json",
    }


def _org_created(org: str, name: str) -> bytes:
    return json.dumps({"type": "organization.created", "data": {"id": org, "name": name, "slug": name.lower()}}).encode()


def test_verify_svix_signature_pure() -> None:
    from app.api.webhooks import verify_svix_signature

    body = b'{"type":"organization.created"}'
    h = _svix_headers(body)
    ok = verify_svix_signature(
        secret=WEBHOOK_SECRET, msg_id=h["svix-id"], timestamp=h["svix-timestamp"],
        signature_header=h["svix-signature"], body=body, tolerance_s=300,
    )
    assert ok is True
    # Rotated secret: a second v1 entry that matches is accepted.
    assert verify_svix_signature(
        secret=WEBHOOK_SECRET, msg_id=h["svix-id"], timestamp=h["svix-timestamp"],
        signature_header="v1,AAAA " + h["svix-signature"], body=body, tolerance_s=300,
    )
    # Tampered body / wrong secret / stale timestamp / missing header → False.
    assert not verify_svix_signature(
        secret=WEBHOOK_SECRET, msg_id=h["svix-id"], timestamp=h["svix-timestamp"],
        signature_header=h["svix-signature"], body=body + b" ", tolerance_s=300,
    )
    assert not verify_svix_signature(
        secret="whsec_" + base64.b64encode(b"another-secret-another-sec").decode(),
        msg_id=h["svix-id"], timestamp=h["svix-timestamp"],
        signature_header=h["svix-signature"], body=body, tolerance_s=300,
    )
    old = _svix_headers(body, ts=int(time.time()) - 3600)
    assert not verify_svix_signature(
        secret=WEBHOOK_SECRET, msg_id=old["svix-id"], timestamp=old["svix-timestamp"],
        signature_header=old["svix-signature"], body=body, tolerance_s=300,
    )
    assert not verify_svix_signature(
        secret=WEBHOOK_SECRET, msg_id=None, timestamp=h["svix-timestamp"],
        signature_header=h["svix-signature"], body=body, tolerance_s=300,
    )
    assert not verify_svix_signature(
        secret="not-base64-!!", msg_id=h["svix-id"], timestamp=h["svix-timestamp"],
        signature_header=h["svix-signature"], body=body, tolerance_s=300,
    )


@pytest.mark.asyncio
async def test_webhook_refuses_without_secret() -> None:
    body = _org_created(STRANGER_ORG, "Stranger")
    async with await _client() as c:
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body))
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "webhook_secret_not_configured"


@pytest.mark.asyncio
async def test_webhook_organization_created_parks_as_pending(monkeypatch) -> None:
    from app.api.deals import coerce_org_tenant_id
    from app.config import get_settings
    from app.tenants import get_tenant, is_registered

    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()

    body = _org_created(STRANGER_ORG, "Stranger Capital")
    async with await _client() as c:
        # Bad signature → 401, nothing written.
        r = await c.post(
            "/webhooks/clerk", content=body,
            headers={**_svix_headers(body), "svix-signature": "v1,AAAA"},
        )
        assert r.status_code == 401, r.text
        assert await get_tenant(coerce_org_tenant_id(STRANGER_ORG)) is None

        # Unsigned → 401.
        r = await c.post("/webhooks/clerk", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 401, r.text

        # Good signature → pending_review / external.
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body))
        assert r.status_code == 200, r.text
        t = r.json()["tenant"]
        assert t["kind"] == "external" and t["status"] == "pending_review"
        tid = coerce_org_tenant_id(STRANGER_ORG)
        assert t["id"] == str(tid)
        # pending_review is NOT registered for enforcement purposes.
        assert await is_registered(tid) is False

        # Replay for an org an admin already approved does not downgrade it.
        await _register(CUSTOMER_ORG, "Harbor Point", "external", status="active")
        body2 = _org_created(CUSTOMER_ORG, "Harbor Point")
        r = await c.post("/webhooks/clerk", content=body2, headers=_svix_headers(body2, msg_id="msg_2"))
        assert r.status_code == 200, r.text
        assert r.json()["tenant"]["status"] == "active"

        # Other event types are acknowledged and ignored.
        other = json.dumps({"type": "user.created", "data": {"id": "user_1"}}).encode()
        r = await c.post("/webhooks/clerk", content=other, headers=_svix_headers(other, msg_id="msg_3"))
        assert r.status_code == 200 and r.json()["ignored"] == "user.created"


@pytest.mark.asyncio
async def test_webhook_then_enforcement_blocks_until_approved(_rsa_key, monkeypatch) -> None:
    """End to end: analyst self-creates an org → webhook parks it → 403 under
    enforcement → internal admin approves → 200."""
    from app.config import get_settings

    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", WEBHOOK_SECRET)
    _enable_enforcement(monkeypatch)
    get_settings.cache_clear()
    await _register(INTERNAL_ORG, "Fondok Internal", "internal")

    body = _org_created(STRANGER_ORG, "Stranger Capital")
    async with await _client() as c:
        assert (await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body))).status_code == 200
        r = await c.get("/deals", headers=_bearer(_admin_token(_rsa_key, STRANGER_ORG)))
        assert r.status_code == 403 and r.json()["detail"] == "tenant_not_registered"

        approve = await c.post(
            "/admin/tenants",
            json={"clerk_org_id": STRANGER_ORG, "name": "Stranger Capital", "kind": "external", "status": "active"},
            headers=_bearer(_admin_token(_rsa_key, INTERNAL_ORG)),
        )
        assert approve.status_code == 201, approve.text
        r = await c.get("/deals", headers=_bearer(_admin_token(_rsa_key, STRANGER_ORG)))
        assert r.status_code == 200, r.text


# ─────────────────────── /settings/tenant ───────────────────────


@pytest.mark.asyncio
async def test_settings_tenant_returns_callers_tenant(_rsa_key) -> None:
    from app.api.deals import coerce_org_tenant_id
    from app.config import get_settings

    await _register(CUSTOMER_ORG, "Harbor Point", "external")
    async with await _client() as c:
        r = await c.get("/settings/tenant", headers=_bearer(_member_token(_rsa_key, CUSTOMER_ORG)))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant_id"] == str(coerce_org_tenant_id(CUSTOMER_ORG))
        assert body["tenant_id"] != get_settings().DEFAULT_TENANT_ID
        assert body["org_id"] == CUSTOMER_ORG
        assert body["registry"]["name"] == "Harbor Point"

        # Unregistered org: still its own tenant, registry is null.
        r = await c.get("/settings/tenant", headers=_bearer(_member_token(_rsa_key, STRANGER_ORG)))
        assert r.status_code == 200, r.text
        assert r.json()["tenant_id"] == str(coerce_org_tenant_id(STRANGER_ORG))
        assert r.json()["registry"] is None

        # No credentials at all → the usual 401 (header path is on in tests,
        # so flip it off for this one probe).
        from app.config import get_settings as _gs

        os.environ["ALLOW_TENANT_HEADER_WITHOUT_JWT"] = "0"
        _gs.cache_clear()
        try:
            r = await c.get("/settings/tenant")
            assert r.status_code == 401, r.text
        finally:
            os.environ["ALLOW_TENANT_HEADER_WITHOUT_JWT"] = "1"
            _gs.cache_clear()


# ─────────────────────── /observability scoping ───────────────────────


async def _ingest(c, headers: dict[str, str], deal_id: str, agent: str, tokens: int) -> None:
    r = await c.post(
        "/observability/_test/model-call",
        json={
            "deal_id": deal_id,
            "agent_name": agent,
            "model": "claude-sonnet-4-6",
            "input_tokens": tokens,
            "output_tokens": 10,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_observability_requires_auth_and_scopes_to_tenant(_rsa_key) -> None:
    from app.config import get_settings

    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    a = _bearer(_member_token(_rsa_key, CUSTOMER_ORG))
    b = _bearer(_member_token(_rsa_key, STRANGER_ORG))
    internal = _bearer(_member_token(_rsa_key, INTERNAL_ORG))
    deal_a, deal_b = str(uuid.uuid4()), str(uuid.uuid4())

    async with await _client() as c:
        await _ingest(c, a, deal_a, "extractor", 1000)
        await _ingest(c, a, deal_a, "extractor", 1000)
        await _ingest(c, b, deal_b, "analyst", 5000)

        # Tenant A sees only its own two calls, on both endpoints.
        r = await c.get("/observability/cache-stats?n=50", headers=a)
        assert r.status_code == 200, r.text
        assert r.json()["samples"] == 2
        assert {x["agent"] for x in r.json()["by_agent"]} == {"extractor"}
        r = await c.get("/observability/agent-costs?days=1", headers=a)
        assert r.status_code == 200, r.text
        assert {x["agent"] for x in r.json()["by_agent"]} == {"extractor"}

        # Tenant B sees only its own.
        r = await c.get("/observability/cache-stats?n=50", headers=b)
        assert r.json()["samples"] == 1
        assert {x["agent"] for x in r.json()["by_agent"]} == {"analyst"}

        # all=1 is refused for external / unregistered tenants …
        r = await c.get("/observability/cache-stats?n=50&all=1", headers=a)
        assert r.status_code == 403, r.text
        r = await c.get("/observability/agent-costs?days=1&all=1", headers=b)
        assert r.status_code == 403, r.text
        # … and honoured for an internal tenant.
        r = await c.get("/observability/cache-stats?n=50&all=1", headers=internal)
        assert r.status_code == 200, r.text
        assert r.json()["samples"] >= 3 and r.json()["tenant_id"] is None

        # No credentials → 401 (flip the local header path off for the probe).
        os.environ["ALLOW_TENANT_HEADER_WITHOUT_JWT"] = "0"
        get_settings.cache_clear()
        try:
            for path in ("/observability/cache-stats", "/observability/agent-costs"):
                r = await c.get(path)
                assert r.status_code == 401, (path, r.text)
            r = await c.post(
                "/observability/_test/model-call",
                json={"deal_id": deal_a, "agent_name": "x", "model": "m"},
            )
            assert r.status_code == 401, r.text
        finally:
            os.environ["ALLOW_TENANT_HEADER_WITHOUT_JWT"] = "1"
            get_settings.cache_clear()


# ─────────────────────── RLS GUC hook ───────────────────────


def test_tenant_guc_hook_publishes_contextvar_tenant() -> None:
    """The after_begin hook runs set_config with the request's tenant and
    does nothing when no tenant was resolved."""
    from app.auth.context import current_tenant_id
    from app.database import TENANT_GUC_SQL, _publish_tenant_guc

    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, Any]] = []

        def execute(self, stmt, params=None):
            self.calls.append((stmt, params))

    conn = _Conn()
    token = current_tenant_id.set(None)
    try:
        _publish_tenant_guc(None, None, conn)
        assert conn.calls == []
        current_tenant_id.set("11111111-1111-1111-1111-111111111111")
        _publish_tenant_guc(None, None, conn)
    finally:
        current_tenant_id.reset(token)
    assert len(conn.calls) == 1
    stmt, params = conn.calls[0]
    assert stmt is TENANT_GUC_SQL
    assert params == {"tenant": "11111111-1111-1111-1111-111111111111"}
    assert "set_config('app.tenant_id'" in str(stmt)


@pytest.mark.asyncio
async def test_tenant_guc_is_a_noop_on_sqlite_even_when_enabled(_rsa_key, monkeypatch) -> None:
    """Flag on + SQLite DSN: sessions work unchanged (the hook is Postgres-only)."""
    from app.config import get_settings
    from app.database import _tenant_guc_is_active

    monkeypatch.setenv("DB_SET_TENANT_GUC", "1")
    get_settings.cache_clear()
    assert get_settings().DB_SET_TENANT_GUC is True
    assert _tenant_guc_is_active() is False
    async with await _client() as c:
        r = await c.get("/deals", headers=_bearer(_member_token(_rsa_key, CUSTOMER_ORG)))
        assert r.status_code == 200, r.text
        assert (await c.get("/health")).json()["auth"]["rls_tenant_guc"] is True


@pytest.mark.asyncio
async def test_auth_dependency_sets_tenant_contextvar(_rsa_key) -> None:
    """Every auth path publishes the tenant for the DB hook to read."""
    from app.api.deals import coerce_org_tenant_id
    from app.auth import get_current_auth
    from app.auth.context import current_tenant_id

    await get_current_auth(
        authorization=f"Bearer {_member_token(_rsa_key, CUSTOMER_ORG)}", x_tenant_id=None
    )
    assert current_tenant_id.get() == str(coerce_org_tenant_id(CUSTOMER_ORG))

    await get_current_auth(authorization=None, x_tenant_id="12121212-1212-1212-1212-121212121212")
    assert current_tenant_id.get() == "12121212-1212-1212-1212-121212121212"


# ─────────────────────── registry outage ≠ "not registered" (review 2026-10-14) ───────────────────────


class _BrokenEngine:
    """Stands in for ``get_engine()`` while the registry DB is "down"."""

    def connect(self):
        raise ConnectionError("registry db unreachable")


@pytest.mark.asyncio
async def test_registry_outage_is_503_not_403_and_is_never_cached(_rsa_key, monkeypatch) -> None:
    import app.tenants as tenants
    from app.api.deals import coerce_org_tenant_id
    from app.tenants import TenantRegistryUnavailable, get_tenant, is_registered

    _enable_enforcement(monkeypatch)
    await _register(CUSTOMER_ORG, "Harbor Point", "external", status="active")
    tid = coerce_org_tenant_id(CUSTOMER_ORG)
    tenants.reset_tenant_cache()

    real_engine = tenants.get_engine
    monkeypatch.setattr(tenants, "get_engine", lambda: _BrokenEngine())
    # The module API: enforcement raises, the informational lookup degrades.
    with pytest.raises(TenantRegistryUnavailable):
        await is_registered(tid)
    assert await get_tenant(tid) is None
    assert tid not in tenants._CACHE  # a failure is never cached as a miss

    async with await _client() as c:
        r = await c.get("/deals", headers=_bearer(_member_token(_rsa_key, CUSTOMER_ORG)))
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == "tenant_registry_unavailable"
        assert r.headers.get("retry-after") == "2"
        # /settings/tenant (informational) still answers, with no registry row.
        s = await c.get("/settings/tenant", headers=_bearer(_member_token(_rsa_key, CUSTOMER_ORG)))
        assert s.status_code == 503  # the auth dependency runs first under enforcement

        # DB back: the very next request is served — nothing stale to wait out.
        monkeypatch.setattr(tenants, "get_engine", real_engine)
        r = await c.get("/deals", headers=_bearer(_member_token(_rsa_key, CUSTOMER_ORG)))
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_registry_misses_expire_in_two_seconds_hits_in_ten(monkeypatch) -> None:
    """A registration or suspension written on ANOTHER replica (no local
    cache clear) lands here promptly: misses live 2 s, hits 10 s."""
    from sqlalchemy import text

    import app.tenants as tenants
    from app.api.deals import coerce_org_tenant_id
    from app.database import get_engine
    from app.tenants import get_tenant, is_registered

    assert tenants._MISS_TTL_S == pytest.approx(2.0) and tenants._CACHE_TTL_S == pytest.approx(10.0)
    tid = coerce_org_tenant_id("org_OtherReplicaRegistered")
    assert tid is not None
    t0 = 1_000.0
    clock = {"now": t0}
    monkeypatch.setattr(tenants.time, "monotonic", lambda: clock["now"])

    assert await is_registered(tid) is False  # cached miss at t0
    # Another replica registers the org — direct SQL, so THIS process's
    # cache is not cleared.
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, clerk_org_id, name, kind, status, created_at) "
                "VALUES (:id, 'org_OtherReplicaRegistered', 'Other', 'external', 'active', :now)"
            ),
            {"id": str(tid), "now": "2026-10-14T00:00:00+00:00"},
        )
    clock["now"] = t0 + 1.5
    assert await is_registered(tid) is False  # still inside the miss TTL
    clock["now"] = t0 + 2.1
    assert await is_registered(tid) is True  # miss expired → re-read → served

    # Another replica suspends it: served until the hit TTL lapses, then refused.
    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE tenants SET status = 'suspended' WHERE id = :id"), {"id": str(tid)})
    clock["now"] = t0 + 2.1 + 9.0
    assert await is_registered(tid) is True
    clock["now"] = t0 + 2.1 + 10.5
    assert await is_registered(tid) is False
    assert (await get_tenant(tid, use_cache=False)).status == "suspended"


# ─────────────────────── webhook: organization.updated / .deleted ───────────────────────


def _org_event(event_type: str, org: str, **data: Any) -> bytes:
    return json.dumps({"type": event_type, "data": {"id": org, **data}}).encode()


@pytest.mark.asyncio
async def test_webhook_updated_refreshes_name_only_and_deleted_suspends_keeping_kind(monkeypatch) -> None:
    from app.api.deals import coerce_org_tenant_id
    from app.config import get_settings
    from app.tenants import get_tenant, is_registered, update_tenant

    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    await _register(INTERNAL_ORG, "Fondok Internal", "internal", status="active")
    tid = coerce_org_tenant_id(INTERNAL_ORG)

    async with await _client() as c:
        # organization.updated → name refresh; kind and status untouched.
        body = _org_event("organization.updated", INTERNAL_ORG, name="Fondok Internal (renamed)", slug="fondok")
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="u1"))
        assert r.status_code == 200, r.text
        t = r.json()["tenant"]
        assert t["name"] == "Fondok Internal (renamed)" and t["kind"] == "internal" and t["status"] == "active"
        # A rename with no usable name changes nothing.
        body = _org_event("organization.updated", INTERNAL_ORG)
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="u2"))
        assert r.status_code == 200 and r.json()["tenant"]["name"] == "Fondok Internal (renamed)"
        # Unknown org → acknowledged, nothing inserted.
        body = _org_event("organization.updated", STRANGER_ORG, name="Nope")
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="u3"))
        assert r.status_code == 200 and r.json() == {"ok": True, "ignored": "organization.updated", "reason": "not_registered"}
        assert await get_tenant(coerce_org_tenant_id(STRANGER_ORG)) is None

        # organization.deleted → suspended, kind + name kept, row kept.
        body = _org_event("organization.deleted", INTERNAL_ORG, deleted=True)
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="d1"))
        assert r.status_code == 200, r.text
        t = r.json()["tenant"]
        assert t["status"] == "suspended" and t["kind"] == "internal" and t["name"] == "Fondok Internal (renamed)"
        assert await is_registered(tid) is False
        # A replayed organization.created never re-activates it …
        body = _org_created(INTERNAL_ORG, "Fondok Internal")
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="c1"))
        assert r.status_code == 200 and r.json()["tenant"]["status"] == "suspended"
        # … nor does an organization.updated.
        body = _org_event("organization.updated", INTERNAL_ORG, name="Back?")
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="u4"))
        assert r.status_code == 200 and r.json()["tenant"]["status"] == "suspended"
        # Deleting an org nobody registered is acknowledged without a row.
        body = _org_event("organization.deleted", STRANGER_ORG, deleted=True)
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="d2"))
        assert r.status_code == 200 and r.json()["reason"] == "not_registered"
        # A handled event without an org id is a 400, still signed.
        body = json.dumps({"type": "organization.deleted", "data": {"deleted": True}}).encode()
        r = await c.post("/webhooks/clerk", content=body, headers=_svix_headers(body, msg_id="d3"))
        assert r.status_code == 400
        # Unsigned deliveries of the new events are refused like the old one.
        body = _org_event("organization.deleted", INTERNAL_ORG, deleted=True)
        r = await c.post("/webhooks/clerk", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 401

    # The module API never re-activates either.
    with pytest.raises(ValueError):
        await update_tenant(tid, status="active")
    with pytest.raises(ValueError):
        await update_tenant(tid, status="weird")


# ─────────────────────── RLS GUC: factory-level listener ───────────────────────


def test_tenant_guc_listener_is_registered_on_the_session_class() -> None:
    from sqlalchemy import event

    from app.database import TenantSession, _tenant_guc_after_begin, get_session_factory, tenant_guc_hook_registered

    assert tenant_guc_hook_registered() is True
    assert event.contains(TenantSession, "after_begin", _tenant_guc_after_begin)
    # Every factory session is a TenantSession underneath.
    assert get_session_factory().kw.get("sync_session_class") is TenantSession


@pytest.mark.asyncio
async def test_factory_sessions_publish_the_guc_when_the_flag_is_on(monkeypatch) -> None:
    """A session from ``get_session_factory()()`` (the background pipelines'
    path, NOT the request dependency) fires the hook on every transaction
    with the contextvar tenant. The Postgres statement itself is stubbed —
    SQLite has no ``set_config`` — the Postgres test below runs it for real."""
    from sqlalchemy import text

    import app.database as database
    from app.auth.context import current_tenant_id
    from app.database import get_session_factory

    published: list[str | None] = []

    def _spy(session, transaction, connection) -> None:
        published.append(current_tenant_id.get())

    monkeypatch.setattr(database, "_tenant_guc_is_active", lambda: True)
    monkeypatch.setattr(database, "_publish_tenant_guc", _spy)
    token = current_tenant_id.set("33333333-3333-3333-3333-333333333333")
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()
            # Autobegin after the commit → the hook fires again (mid-request commit).
            await session.execute(text("SELECT 1"))
    finally:
        current_tenant_id.reset(token)
    assert published == ["33333333-3333-3333-3333-333333333333"] * 2


@pytest.mark.asyncio
async def test_factory_sessions_do_not_publish_on_sqlite_even_with_the_flag_on(monkeypatch) -> None:
    from sqlalchemy import text

    import app.database as database
    from app.auth.context import current_tenant_id
    from app.config import get_settings
    from app.database import get_session_factory

    monkeypatch.setenv("DB_SET_TENANT_GUC", "1")
    get_settings.cache_clear()
    calls: list[Any] = []
    monkeypatch.setattr(database, "_publish_tenant_guc", lambda *a: calls.append(a))
    token = current_tenant_id.set("33333333-3333-3333-3333-333333333333")
    try:
        async with get_session_factory()() as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        current_tenant_id.reset(token)
    assert calls == []  # the listener is registered but no-ops on SQLite


@pytest.mark.asyncio
async def test_factory_sessions_publish_the_guc_on_postgres(monkeypatch) -> None:
    """Real Postgres: ``current_setting('app.tenant_id')`` sees the tenant
    inside a factory session's transaction. Opt-in via
    ``FONDOK_TEST_POSTGRES_URL`` (no Postgres in the default suite)."""
    url = os.environ.get("FONDOK_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("FONDOK_TEST_POSTGRES_URL not set — Postgres GUC check skipped")
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.database as database
    from app.auth.context import current_tenant_id
    from app.database import TenantSession

    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession, sync_session_class=TenantSession)
    monkeypatch.setattr(database, "_tenant_guc_is_active", lambda: True)
    tenant = "44444444-4444-4444-4444-444444444444"
    token = current_tenant_id.set(tenant)
    try:
        async with factory() as session:
            seen = (await session.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar_one()
            assert seen == tenant
            await session.commit()
            seen = (await session.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar_one()
            assert seen == tenant  # re-published after the commit
    finally:
        current_tenant_id.reset(token)
        await engine.dispose()


# ─────────────────────── background entrypoints publish their tenant ───────────────────────


@pytest.mark.asyncio
async def test_background_run_model_and_cost_persistence_publish_the_tenant(monkeypatch) -> None:
    from app.api import model as model_api
    from app.auth.context import current_tenant_id
    from app import cost_persistence

    seen: dict[str, str | None] = {}

    async def _fake_run_all(session, **kw):
        seen["run_all"] = current_tenant_id.get()

    async def _fake_run_one(session, **kw):
        seen["run_one"] = current_tenant_id.get()

    async def _fake_persist(session, **kw):
        seen["persist"] = current_tenant_id.get()
        return 0

    monkeypatch.setattr(model_api, "run_all_engines", _fake_run_all)
    monkeypatch.setattr(model_api, "run_single_engine", _fake_run_one)
    monkeypatch.setattr(cost_persistence, "persist_model_calls", _fake_persist)
    token = current_tenant_id.set(None)
    try:
        await model_api._background_run_all("deal-1", "55555555-5555-5555-5555-555555555555", "run-1", None)
        await model_api._background_run_one("deal-1", "66666666-6666-6666-6666-666666666666", "revenue", "run-2", None)
        await cost_persistence.persist_model_calls_standalone(
            deal_id="deal-1", tenant_id="77777777-7777-7777-7777-777777777777", calls=[object()]
        )
    finally:
        current_tenant_id.reset(token)
    assert seen == {
        "run_all": "55555555-5555-5555-5555-555555555555",
        "run_one": "66666666-6666-6666-6666-666666666666",
        "persist": "77777777-7777-7777-7777-777777777777",
    }


@pytest.mark.asyncio
async def test_digest_scheduler_and_batch_poller_publish_each_tenant_in_turn(monkeypatch) -> None:
    from app.agents import analyst_batch
    from app.agents.analyst_batch import PendingBatchRow
    from app.auth.context import current_tenant_id
    from app.services import digest_scheduler

    # ── digest scheduler: one schedule per tenant, tenant set before each payload build.
    seen_digest: list[str | None] = []

    async def _due(session, *, now):
        return [
            {"id": "s1", "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "cadence": "daily", "hour_utc": 13},
            {"id": "s2", "tenant_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "cadence": "daily", "hour_utc": 13},
        ]

    async def _payload(session, *, tenant_id, schedule, now):
        seen_digest.append(current_tenant_id.get())
        return {}

    async def _mark(session, **kw):
        return None

    monkeypatch.setattr(digest_scheduler, "_fetch_due_schedules", _due)
    monkeypatch.setattr(digest_scheduler, "build_digest_payload", _payload)
    monkeypatch.setattr(digest_scheduler, "dispatch_digest", lambda schedule, payload: None)
    monkeypatch.setattr(digest_scheduler, "_mark_dispatched", _mark)
    token = current_tenant_id.set(None)
    try:
        assert await digest_scheduler.tick_once() == 2
        assert current_tenant_id.get() is None  # restored after the tick
    finally:
        current_tenant_id.reset(token)
    assert seen_digest == ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]

    # ── batch poller: the tenant is set per open batch before any statement for it.
    from datetime import UTC, datetime

    seen_batch: list[tuple[str, str | None, str | None]] = []
    when = datetime.now(UTC)
    rows = [
        PendingBatchRow(id="r1", batch_id="b1", deal_id="d1", tenant_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                        agent_name="analyst", status="queued", submitted_at=when),
        PendingBatchRow(id="r2", batch_id="b2", deal_id="d2", tenant_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                        agent_name="analyst", status="in_progress", submitted_at=when),
    ]

    async def _open(session):
        return rows

    async def _status(session, **kw):
        seen_batch.append(("status", current_tenant_id.get(), kw.get("tenant_id")))

    class _Client:
        def retrieve(self, batch_id):
            seen_batch.append(("retrieve", current_tenant_id.get(), batch_id))
            return {"processing_status": "in_progress"}

    monkeypatch.setattr(analyst_batch, "_list_open_batches", _open)
    monkeypatch.setattr(analyst_batch, "_update_pending_status", _status)
    token = current_tenant_id.set(None)
    try:
        tally = await analyst_batch.poll_pending_batches(client=_Client())
        assert current_tenant_id.get() is None
    finally:
        current_tenant_id.reset(token)
    assert tally["checked"] == 2 and tally["pending"] == 2
    assert seen_batch == [
        ("retrieve", "cccccccc-cccc-cccc-cccc-cccccccccccc", "b1"),
        ("status", "cccccccc-cccc-cccc-cccc-cccccccccccc", "cccccccc-cccc-cccc-cccc-cccccccccccc"),
        ("retrieve", "dddddddd-dddd-dddd-dddd-dddddddddddd", "b2"),
    ]
