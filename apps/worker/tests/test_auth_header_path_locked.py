"""The no-JWT tenant path is closed unless an operator opens it.

2026-10-06 — found during the Eshan/Rani feedback review, verified live and
read-only against production: with no credentials at all, a request carrying
a forged ``X-Tenant-Id`` returned the full deal list for that tenant from the
public worker URL. ``require_role`` treats that path as a trusted caller, so
it also cleared every admin gate.

The flag ``ALLOW_TENANT_HEADER_WITHOUT_JWT`` defaults to False. conftest sets
it for the rest of the suite (35 modules drive tenant scoping through the bare
header); these tests flip it per case and clear the settings cache so the
dependency re-reads it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "auth_lock.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

FORGED = "org_2abc_forged_tenant"
BEARER_GARBAGE = "Bearer not.a.real.jwt"


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _set_flag(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """``None`` means "the production posture": the flag is OFF.

    Set it to "0" explicitly rather than deleting it. ``Settings`` also reads
    ``apps/worker/.env`` (a developer's local file carries ``=1`` so the web
    app can talk to a local worker without Clerk), and a dotenv value cannot
    be removed by ``delenv`` — but a real environment variable always wins
    over the file. Railway has no ``.env``, so unset there is False too.
    """
    from app.config import get_settings

    monkeypatch.setenv("ALLOW_TENANT_HEADER_WITHOUT_JWT", "0" if value is None else value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _restore_settings_cache():
    from app.config import get_settings

    yield
    get_settings.cache_clear()


async def test_default_refuses_a_forged_tenant_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact production probe: no Authorization, forged X-Tenant-Id → 401."""
    _set_flag(monkeypatch, None)
    async with await _client() as c:
        r = await c.get("/deals", headers={"X-Tenant-Id": FORGED})
    assert r.status_code == 401, r.text
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


async def test_default_refuses_a_bare_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing at all used to fall through to DEFAULT_TENANT_ID and be served."""
    _set_flag(monkeypatch, None)
    async with await _client() as c:
        r = await c.get("/deals")
    assert r.status_code == 401, r.text


async def test_default_refuses_writes_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hole was not read-only: writes share the same dependency."""
    _set_flag(monkeypatch, None)
    async with await _client() as c:
        r = await c.patch(
            "/deals/00000000-0000-0000-0000-000000000099",
            headers={"X-Tenant-Id": FORGED},
            json={"name": "should never land"},
        )
    assert r.status_code == 401, r.text


async def test_a_malformed_bearer_is_still_401_not_a_fallthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Authorization: Bearer <garbage>`` must not quietly become the header path."""
    _set_flag(monkeypatch, None)
    async with await _client() as c:
        r = await c.get("/deals", headers={"Authorization": BEARER_GARBAGE, "X-Tenant-Id": FORGED})
    assert r.status_code == 401, r.text


async def test_public_routes_stay_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Locking the tenant path must not take /health with it."""
    _set_flag(monkeypatch, None)
    async with await _client() as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["auth"] == {"header_tenant_path_enabled": False, "jwt_required": True}


async def test_opt_in_restores_the_header_path_for_dev_and_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag on, the pre-existing behaviour the suite relies on is intact."""
    _set_flag(monkeypatch, "1")
    async with await _client() as c:
        r = await c.get("/deals", headers={"X-Tenant-Id": FORGED})
        h = await c.get("/health")
    assert r.status_code == 200, r.text
    assert h.json()["auth"]["header_tenant_path_enabled"] is True
