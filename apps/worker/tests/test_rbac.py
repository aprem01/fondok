"""RBAC — Clerk JWT verification + role gating.

Covers the two-role model:

* ``admin`` → passes ``require_role("admin")``.
* ``member`` → 403 on admin endpoints, 200 on member endpoints.
* header-only + default paths → treated as admin-equivalent
  (backwards-compat escape hatch).
* malformed / expired JWTs → 401.
* personal Clerk user (no org) → 403 on admin, 200 on member endpoints.

The Clerk JWKS fetch is patched out — we mint our own RS256 key,
publish the corresponding JWK via the module-level cache, and sign
tokens locally. Nothing hits Clerk's real endpoint.
"""

from __future__ import annotations

import base64
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

# Force a fresh SQLite DB before any ``app.*`` import binds Settings().
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-rbac.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


# ─────────────────────── key + JWKS fixture helpers ───────────────────────

# Pinned kid so we can wire our stub key into the module-level cache and
# have :func:`verify_clerk_jwt` pick it up on the first hit.
_TEST_KID = "test-kid-rbac"


def _b64url_uint(value: int) -> str:
    """Base64url encoding of a big-endian unsigned integer (RFC 7518 §6.3)."""
    n = value.bit_length()
    length = (n + 7) // 8
    raw = value.to_bytes(length, "big") if length > 0 else b"\x00"
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def _rsa_key():
    """Generate a throwaway RSA-2048 key pair for the whole test module.

    Module scope so we sign multiple tokens against the same public key
    — one JWKS load hits the (patched) fetch, subsequent verifies use
    the cache like they would in prod.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_jwks(rsa_key: Any) -> dict[str, Any]:
    """Return the JWKS document Clerk would serve for our stub key."""
    numbers = rsa_key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": _TEST_KID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


def _sign(rsa_key: Any, claims: dict[str, Any]) -> str:
    """Sign ``claims`` with our stub key. Adds ``kid`` header + defaults
    for ``iat`` / ``exp`` when the caller doesn't set them."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization

    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    payload = {
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        **claims,
    }
    return pyjwt.encode(
        payload, pem, algorithm="RS256", headers={"kid": _TEST_KID}
    )


@pytest.fixture(autouse=True)
def _install_stub_jwks(_rsa_key, monkeypatch):
    """Prime the JWKS cache with our stub key + block real HTTP fetches.

    Autouse so every test starts with a clean, populated cache. If any
    code path *does* call ``_load_jwks`` we make it a no-op that returns
    the pre-populated cache — safer than a real HTTPX mock because it
    guarantees no test can accidentally hit Clerk's real endpoint.
    """
    from app.auth import clerk_jwt

    # Rebuild the cache dict with our stub key. Mirrors what
    # _load_jwks would produce after fetching + parsing the JWKS.
    from jwt.algorithms import RSAAlgorithm

    jwks = _make_jwks(_rsa_key)
    keys = {jwk["kid"]: RSAAlgorithm.from_jwk(jwk) for jwk in jwks["keys"]}

    monkeypatch.setattr(clerk_jwt, "_JWKS_KEYS", keys)
    monkeypatch.setattr(clerk_jwt, "_JWKS_FETCHED_AT", time.monotonic())

    def _fake_load(*, force: bool = False) -> dict[str, Any]:
        return keys

    monkeypatch.setattr(clerk_jwt, "_load_jwks", _fake_load)
    yield


# ─────────────────────── DB seed helpers ───────────────────────


async def _ensure_schema() -> None:
    """Run startup migrations so the ``deals`` / ``documents`` tables
    exist. AsyncClient + ASGITransport doesn't drive FastAPI's
    ``lifespan``, so we do it manually."""
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


async def _seed_deal(tenant_id: str) -> str:
    """Insert one deal row + return its id. Used to give the delete
    endpoints something concrete to target."""
    from sqlalchemy import text

    from app.database import get_engine

    deal_id = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Draft', 0.0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": deal_id, "tenant": tenant_id, "name": "RBAC Test Hotel"},
        )
    return deal_id


# ─────────────────────── tests ───────────────────────


@pytest.mark.asyncio
async def test_admin_jwt_passes_admin_gate(_rsa_key) -> None:
    """A verified JWT with ``org_role=org:admin`` → 200 on /admin/cost."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    token = _sign(
        _rsa_key,
        {
            "sub": "user_admin1",
            "org_id": "org_ABC",
            "org_role": "org:admin",
        },
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/admin/cost", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # Tenant id derived from the JWT's org_id (via _coerce_tenant_id).
    assert body["tenant_id"] != "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_member_jwt_gets_403_on_admin_endpoint(_rsa_key) -> None:
    """A verified JWT with ``org_role=org:member`` → 403 on /admin/cost."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    token = _sign(
        _rsa_key,
        {
            "sub": "user_member1",
            "org_id": "org_ABC",
            "org_role": "org:member",
        },
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/admin/cost", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 403, r.text
    assert "role 'member' insufficient" in r.text


@pytest.mark.asyncio
async def test_header_only_passes_escape_hatch() -> None:
    """No Authorization + valid X-Tenant-Id UUID → 200 (compat escape hatch).

    The header/default paths bypass the role gate so curl runbooks and
    server-side scripts keep working during the JWT rollout.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    tenant = "44444444-4444-4444-4444-444444444444"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/admin/cost", headers={"X-Tenant-Id": tenant})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == tenant


@pytest.mark.asyncio
async def test_malformed_jwt_returns_401() -> None:
    """Random garbage on Authorization → 401 (not 200 header fall-through).

    A bad JWT is an active auth failure — we surface 401 so the browser
    refreshes the token rather than silently degrading to whatever
    X-Tenant-Id happens to be sitting on the request.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/admin/cost",
            headers={"Authorization": "Bearer not.a.real.jwt"},
        )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_expired_jwt_returns_401(_rsa_key) -> None:
    """An RS256-signed but expired token → 401."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    # exp in the past → jwt.decode raises ExpiredSignatureError → None
    # → get_current_auth raises 401.
    token = _sign(
        _rsa_key,
        {
            "sub": "user_expired",
            "org_id": "org_ABC",
            "org_role": "org:admin",
            "iat": int(time.time()) - 600,
            "exp": int(time.time()) - 60,
        },
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/admin/cost", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_personal_clerk_user_passes_member_endpoint(_rsa_key) -> None:
    """A JWT with no org_id (personal account) → 200 on a member endpoint.

    Uses ``GET /deals`` — the everyday-analyst surface — which is
    protected by the tenant dep but not by ``require_role``. A personal
    Clerk user gets their tenant resolved from ``X-Tenant-Id`` as
    fallback, and the endpoint responds normally.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    token = _sign(
        _rsa_key,
        {
            "sub": "user_personal1",
            # No org_id / org_role — personal account token.
        },
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/deals",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": "55555555-5555-5555-5555-555555555555",
            },
        )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_personal_clerk_user_403_on_admin_endpoint(_rsa_key) -> None:
    """A personal-account JWT (no org_role) → 403 on /admin/cost.

    role normalizes to ``"unknown"``; ``source="jwt"`` means the strict
    path applies, so the role gate refuses.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    token = _sign(
        _rsa_key,
        {"sub": "user_personal2"},
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/admin/cost",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": "66666666-6666-6666-6666-666666666666",
            },
        )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_hard_delete_admin_jwt_succeeds(_rsa_key) -> None:
    """Admin JWT → 204 on DELETE /deals/{id}/hard; audit row carries actor_id."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_engine
    from app.main import app

    await _ensure_schema()
    # Same UUID coercion the auth path uses, so the deal we insert
    # lands under the same tenant the JWT resolves to.
    from app.api.deals import _coerce_tenant_id

    org_id = "org_HARDDEL"
    tenant_uuid = _coerce_tenant_id(org_id)
    assert tenant_uuid is not None
    deal_id = await _seed_deal(str(tenant_uuid))

    token = _sign(
        _rsa_key,
        {
            "sub": "user_admin_hd",
            "org_id": org_id,
            "org_role": "org:admin",
        },
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.delete(
            f"/deals/{deal_id}/hard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 204, r.text

    # Audit row exists + carries the JWT user_id.
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT actor_id, action FROM audit_log "
                    "WHERE resource_id = :rid AND action = 'deal.hard_deleted'"
                ),
                {"rid": deal_id},
            )
        ).first()
    assert row is not None, "expected an audit row for hard delete"
    assert row._mapping["actor_id"] == "user_admin_hd"


@pytest.mark.asyncio
async def test_hard_delete_member_jwt_forbidden(_rsa_key) -> None:
    """Member JWT → 403 on DELETE /deals/{id}/hard; deal survives."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.api.deals import _coerce_tenant_id
    from app.database import get_engine
    from app.main import app

    await _ensure_schema()
    org_id = "org_MEMBER"
    tenant_uuid = _coerce_tenant_id(org_id)
    assert tenant_uuid is not None
    deal_id = await _seed_deal(str(tenant_uuid))

    token = _sign(
        _rsa_key,
        {
            "sub": "user_member_hd",
            "org_id": org_id,
            "org_role": "org:member",
        },
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.delete(
            f"/deals/{deal_id}/hard",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 403, r.text

    # Deal must still exist.
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
    assert row is not None, "member should not have been able to delete deal"


@pytest.mark.asyncio
async def test_normalize_clerk_role() -> None:
    """Unit-test the role normalizer — no HTTP round trip needed."""
    from app.auth.context import normalize_clerk_role

    assert normalize_clerk_role("org:admin") == "admin"
    assert normalize_clerk_role("org:member") == "member"
    assert normalize_clerk_role("admin") == "admin"
    assert normalize_clerk_role(None) == "unknown"
    assert normalize_clerk_role("") == "unknown"
    # Custom role slug passes through bare.
    assert normalize_clerk_role("org:analyst") == "analyst"
