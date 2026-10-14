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
