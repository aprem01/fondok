"""RBAC — Clerk JWT verification + role gating.

Covers the two-role model:

* ``admin`` → passes ``require_role("admin")``.
* ``member`` → 403 on admin endpoints, 200 on member endpoints.
* header-only + default paths → treated as admin-equivalent
  (backwards-compat escape hatch).
* malformed / expired JWTs → 401.
* personal Clerk user (no org) → 409 ``no_active_organization`` everywhere,
  even with an ``X-Tenant-Id`` header (FON-93: the header is not a tenant
  source on the JWT path).

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
async def test_personal_jwt_with_tenant_header_is_409_not_served(_rsa_key) -> None:
    """FON-93: a verified JWT with no org_id + an arbitrary ``X-Tenant-Id``
    must 409 — the header is NOT a tenant source once a JWT is present.

    Before 2026-10-14 this exact request returned 200 with the named
    tenant's deal list: any signed-in Clerk user (personal account, no
    org) could read any institution's workspace by naming its UUID.
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
        for header in (
            "55555555-5555-5555-5555-555555555555",  # raw tenant UUID
            "org_SOMEONE_ELSES_ORG",  # a Clerk org id
        ):
            r = await client.get(
                "/deals",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-Id": header,
                },
            )
            assert r.status_code == 409, (header, r.text)
            assert r.json()["detail"] == "no_active_organization"


@pytest.mark.asyncio
async def test_org_jwt_ignores_tenant_header(_rsa_key) -> None:
    """FON-93: with an org JWT, ``X-Tenant-Id`` naming ANOTHER tenant is
    ignored — the request is scoped to the JWT's org, never the header."""
    from httpx import ASGITransport, AsyncClient

    from app.api.deals import coerce_org_tenant_id
    from app.main import app

    await _ensure_schema()
    own_org = "org_OWN_WORKSPACE"
    own_tenant = coerce_org_tenant_id(own_org)
    assert own_tenant is not None
    own_deal = await _seed_deal(str(own_tenant))
    other_tenant = "77777777-7777-7777-7777-777777777777"
    other_deal = await _seed_deal(other_tenant)

    token = _sign(
        _rsa_key, {"sub": "user_member_x", "org_id": own_org, "org_role": "org:member"}
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/deals",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": other_tenant},
        )
        assert r.status_code == 200, r.text
        ids = {d["id"] for d in r.json()}
        assert own_deal in ids
        assert other_deal not in ids
        # And the other tenant's deal is a 404, not a 200, even when named.
        r2 = await client.get(
            f"/deals/{other_deal}",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": other_tenant},
        )
        assert r2.status_code == 404, r2.text


@pytest.mark.asyncio
async def test_jwt_path_rejects_non_org_identifiers(_rsa_key) -> None:
    """FON-93: ``org_id`` claims that are not Clerk org ids never become a
    tenant. ``user_…`` / ``acc_…`` / raw UUIDs used to hash to one."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for bogus in ("user_abc", "acc_abc", "55555555-5555-5555-5555-555555555555", "org_"):
            token = _sign(_rsa_key, {"sub": "user_x", "org_id": bogus, "org_role": "org:admin"})
            r = await client.get("/deals", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 409, (bogus, r.text)


def test_coerce_org_tenant_id_is_org_only() -> None:
    """Unit: the JWT-path coercer accepts ``org_…`` and nothing else; the
    header-path coercer still takes a literal UUID but no ``user_``/``acc_``."""
    from uuid import UUID

    from app.api.deals import _coerce_tenant_id, coerce_org_tenant_id

    org = coerce_org_tenant_id("org_2abcDEF")
    assert isinstance(org, UUID)
    assert coerce_org_tenant_id("org_2abcDEF") == org  # deterministic
    assert coerce_org_tenant_id(" org_2abcDEF ") == org  # whitespace-tolerant
    for bad in (None, "", "org_", "user_123", "acc_123", "55555555-5555-5555-5555-555555555555"):
        assert coerce_org_tenant_id(bad) is None, bad

    assert _coerce_tenant_id("55555555-5555-5555-5555-555555555555") == UUID(
        "55555555-5555-5555-5555-555555555555"
    )
    assert _coerce_tenant_id("org_2abcDEF") == org
    assert _coerce_tenant_id("user_123") is None
    assert _coerce_tenant_id("acc_123") is None


@pytest.mark.asyncio
async def test_authed_no_org_no_header_returns_409(_rsa_key) -> None:
    """FON-20: a verified JWT with NO org_id AND NO X-Tenant-Id must 409
    ``no_active_organization`` — NOT silently fall to DEFAULT_TENANT_ID.

    This is the exact failure that made a user's own extracted document
    404 ("document not found on deal"): the active org wasn't set, so the
    request resolved the wrong tenant. Failing loud lets the client set an
    active org and retry, instead of silently reading the wrong tenant.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    token = _sign(_rsa_key, {"sub": "user_noorg"})  # no org_id, no header
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/deals", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "no_active_organization"


@pytest.mark.asyncio
async def test_personal_clerk_user_409_on_admin_endpoint(_rsa_key) -> None:
    """A personal-account JWT (no org) → 409 on /admin/cost, header or not.

    FON-93: tenant resolution runs before the role gate and a token with
    no active organization has no tenant, so the request never reaches
    ``require_role`` — the header it carries is ignored. (Before FON-93
    this was a 403 from the role gate, i.e. the header had been accepted
    as the tenant.)
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
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "no_active_organization"


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


# ─────────────────────── Clerk session-token v2 claim shape ───────────────────────


@pytest.mark.asyncio
async def test_v2_session_token_org_claims_resolve_tenant_and_role(_rsa_key) -> None:
    """Clerk v2 session tokens carry the org under ``o`` (``o.id`` /
    ``o.rol``) instead of top-level ``org_id`` / ``org_role``. The worker
    must read either shape — a Clerk instance upgrade must not turn every
    session into 409 ``no_active_organization``."""
    from httpx import ASGITransport, AsyncClient

    from app.api.deals import coerce_org_tenant_id
    from app.main import app

    await _ensure_schema()
    admin_v2 = _sign(
        _rsa_key,
        {"sub": "user_v2admin", "v": 2, "o": {"id": "org_V2ORG", "rol": "admin", "slg": "v2"}},
    )
    member_v2 = _sign(
        _rsa_key,
        {"sub": "user_v2member", "v": 2, "o": {"id": "org_V2ORG", "rol": "member", "slg": "v2"}},
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/admin/cost", headers={"Authorization": f"Bearer {admin_v2}"})
        assert r.status_code == 200, r.text
        assert r.json()["tenant_id"] == str(coerce_org_tenant_id("org_V2ORG"))
        r = await client.get("/admin/cost", headers={"Authorization": f"Bearer {member_v2}"})
        assert r.status_code == 403, r.text
        r = await client.get("/deals", headers={"Authorization": f"Bearer {member_v2}"})
        assert r.status_code == 200, r.text
        # ``o`` present but not an org → still refused, never guessed.
        bogus = _sign(_rsa_key, {"sub": "user_x", "v": 2, "o": {"id": "user_abc", "rol": "admin"}})
        r = await client.get("/deals", headers={"Authorization": f"Bearer {bogus}"})
        assert r.status_code == 409, r.text
