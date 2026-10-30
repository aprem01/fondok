"""``GET /methodology?level=…`` — levels and gates (FON-120, plan C.3 / C.5).

* ``conceptual`` — any signed-in caller: a JWT member reads it.
* ``detailed`` / ``engineering`` — admin of an INTERNAL tenant:
    - JWT member of an internal tenant → 403;
    - JWT admin of an EXTERNAL tenant → 403 (same code, no hint);
    - JWT admin of an internal tenant → 200 with the AST.
* ``/health`` reports ``methodology_sections`` and flags ``methodology_missing``
  when the package did not load.

The Clerk JWKS fetch is patched out exactly as ``test_tenant_registry.py``
does: a throwaway RSA key signs the tokens locally.
"""

from __future__ import annotations

import base64
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-methodology-routes.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app import methodology as methodology_pkg  # noqa: E402
from app.api.methodology import METHODOLOGY_REQUIRES_INTERNAL_ADMIN  # noqa: E402
from app.migrations import run_startup_migrations  # noqa: E402
from app.tenants import reset_tenant_cache, upsert_tenant  # noqa: E402

_TEST_KID = "test-kid-methodology"


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


def _bearer(rsa_key: Any, org: str, role: str, *, sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_sign(rsa_key, {'sub': sub, 'org_id': org, 'org_role': role})}"}


@pytest.fixture(autouse=True)
def _install_stub_jwks(_rsa_key, monkeypatch):
    from jwt.algorithms import RSAAlgorithm

    from app.auth import clerk_jwt

    numbers = _rsa_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "kid": _TEST_KID, "use": "sig", "alg": "RS256",
        "n": _b64url_uint(numbers.n), "e": _b64url_uint(numbers.e),
    }
    keys = {_TEST_KID: RSAAlgorithm.from_jwk(jwk)}
    monkeypatch.setattr(clerk_jwt, "_JWKS_KEYS", keys)
    monkeypatch.setattr(clerk_jwt, "_JWKS_FETCHED_AT", time.monotonic())
    monkeypatch.setattr(clerk_jwt, "_load_jwks", lambda *, force=False: keys)
    yield


@pytest.fixture
async def tenants():
    """An internal and an external tenant, registered and active."""
    await run_startup_migrations()
    reset_tenant_cache()
    from app.api.deals import coerce_org_tenant_id

    internal_org = f"org_int_{uuid4().hex[:10]}"
    external_org = f"org_ext_{uuid4().hex[:10]}"
    await upsert_tenant(
        tenant_id=coerce_org_tenant_id(internal_org), clerk_org_id=internal_org,
        name="Fondok Internal (test)", kind="internal", status="active",
    )
    await upsert_tenant(
        tenant_id=coerce_org_tenant_id(external_org), clerk_org_id=external_org,
        name="External Co (test)", kind="external", status="active",
    )
    return {"internal": internal_org, "external": external_org}


@pytest.fixture
def client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_conceptual_is_readable_by_a_member(client, tenants, _rsa_key) -> None:
    async with client as c:
        r = await c.get("/methodology", headers=_bearer(_rsa_key, tenants["internal"], "org:member", sub="m1"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "conceptual"
    assert body["page"]["title"] == "How Fondok underwrites"
    ids = [s["id"] for s in body["sections"]]
    assert ids[:3] == ["extraction", "projection", "sources"] and "concept-registry" not in ids
    assert [s["number"] for s in body["sections"]] == list(range(1, len(ids) + 1))
    first = body["sections"][0]
    assert first["blocks"] and first["blocks"][0]["type"] == "container"
    # The response is the AST the web renders — no dialect left over.
    import json as _json

    raw = _json.dumps(body)
    assert ":::" not in raw and "{{" not in raw and "**" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["detailed", "engineering"])
async def test_member_of_internal_tenant_gets_403_on_admin_levels(client, tenants, _rsa_key, level: str) -> None:
    async with client as c:
        r = await c.get(f"/methodology?level={level}", headers=_bearer(_rsa_key, tenants["internal"], "org:member", sub="m2"))
    assert r.status_code == 403, r.text
    assert "insufficient" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["detailed", "engineering"])
async def test_admin_of_external_tenant_gets_403_on_admin_levels(client, tenants, _rsa_key, level: str) -> None:
    async with client as c:
        r = await c.get(f"/methodology?level={level}", headers=_bearer(_rsa_key, tenants["external"], "org:admin", sub="a-ext"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == METHODOLOGY_REQUIRES_INTERNAL_ADMIN


@pytest.mark.asyncio
async def test_internal_admin_reads_detailed(client, tenants, _rsa_key) -> None:
    async with client as c:
        r = await c.get("/methodology?level=detailed", headers=_bearer(_rsa_key, tenants["internal"], "org:admin", sub="a-int"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "detailed"
    ids = {s["id"] for s in body["sections"]}
    assert ids == {"projection", "sources", "engines", "pricing"}
    import json as _json

    text = _json.dumps(body, ensure_ascii=False)
    # The figures the plan names are in the detailed level, nowhere else.
    for needle in ("7.0%", "1.50%", "0.5× and 2.0×", "25 to 250"):
        assert needle in text, needle
    # …and HTML source comments never leave the markdown.
    assert "<!--" not in text and "source:" not in text


@pytest.mark.asyncio
async def test_internal_admin_reads_engineering_with_its_own_numbering(client, tenants, _rsa_key) -> None:
    async with client as c:
        r = await c.get("/methodology?level=engineering", headers=_bearer(_rsa_key, tenants["internal"], "org:admin", sub="a-int2"))
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {s["id"]: s for s in body["sections"]}
    assert by_id["concept-registry"]["number"] == 8
    assert by_id["lineage"]["number"] == 9
    assert "controls" not in by_id
    assert by_id["extraction"]["title"] == "Extraction workflow"


@pytest.mark.asyncio
async def test_unknown_level_is_422(client, tenants, _rsa_key) -> None:
    async with client as c:
        r = await c.get("/methodology?level=secret", headers=_bearer(_rsa_key, tenants["internal"], "org:admin", sub="a3"))
    assert r.status_code == 422


def test_route_carries_the_auth_dependency() -> None:
    """A caller is always resolved (``get_current_auth``): on the JWT path a
    missing or bad token is 401 (``test_rbac.py``); the test environment's
    header/default fallback is the trusted-caller policy, not a hole. The
    unauthenticated-routes guard enumerates every route for the same thing."""
    from fastapi.routing import APIRoute

    from app.main import app

    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == "/methodology")
    names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
    assert "get_current_auth" in names


@pytest.mark.asyncio
async def test_missing_package_is_503_and_health_degrades(client, tenants, _rsa_key, monkeypatch) -> None:
    monkeypatch.setenv("FONDOK_METHODOLOGY_DIR", str(Path(tempfile.gettempdir()) / "no-such-methodology"))
    methodology_pkg.reset_for_tests()
    try:
        from app import main as main_mod

        n = methodology_pkg.load_at_startup()
        assert n == 0
        monkeypatch.setitem(main_mod._STARTUP_STATE, "methodology_sections", n)
        async with client as c:
            r = await c.get("/methodology", headers=_bearer(_rsa_key, tenants["internal"], "org:member", sub="m9"))
            assert r.status_code == 503
            assert r.json()["detail"]["code"] == "methodology_unavailable"
            h = await c.get("/health")
        assert h.status_code == 200
        body = h.json()
        assert body["methodology_sections"] == 0
        assert "methodology_missing" in body["degraded_reasons"]
    finally:
        monkeypatch.delenv("FONDOK_METHODOLOGY_DIR", raising=False)
        methodology_pkg.reset_for_tests()


@pytest.mark.asyncio
async def test_health_reports_the_section_count_when_loaded(client, tenants) -> None:
    from app import main as main_mod

    methodology_pkg.reset_for_tests()
    n = methodology_pkg.load_at_startup()
    assert n == 11
    main_mod._STARTUP_STATE["methodology_sections"] = n
    async with client as c:
        h = await c.get("/health")
    body = h.json()
    assert body["methodology_sections"] == 11
    assert "methodology_missing" not in body["degraded_reasons"]
