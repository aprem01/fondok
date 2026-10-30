"""FON-116 / FON-96 — tenant data policies (the CoStar licensing gate).

The policy is a per-tenant row (``tenants.data_policies``), not a code fork:
a CoStar-derived market default may be offered to a workspace only while its
policy says ``allow``. The topline resolvers that will consult it are the
next PR; this module pins the mechanism they consume:

* migration: additive, idempotent, SQLite twin;
* typed model: one key, default ``allow``, unknown keys rejected;
* ``load_tenant_data_policy`` — default for an unregistered tenant, the row's
  value otherwise, cache cleared on write;
* ``GET/PATCH /admin/tenants/{id}/data_policies`` — internal admins only,
  audited ``tenant.data_policy_changed`` with before/after;
* the read-cache fingerprint moves when the policy moves;
* the engine-input loader carries the policy object into the loader context
  (``base["__data_policy__"]``) without changing any number.

Clerk's JWKS fetch is patched out exactly as in ``test_tenant_registry.py``.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "fondok-tests-tenant-data-policies.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TEST_KID = "test-kid-fon116"

INTERNAL_ORG = "org_FondokInternalPolicy"
CUSTOMER_ORG = "org_HarborPointPolicy"


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


def _admin(rsa_key: Any, org: str) -> dict[str, str]:
    tok = _sign(rsa_key, {"sub": "user_admin", "org_id": org, "org_role": "org:admin"})
    return {"Authorization": f"Bearer {tok}"}


def _member(rsa_key: Any, org: str) -> dict[str, str]:
    tok = _sign(rsa_key, {"sub": "user_member", "org_id": org, "org_role": "org:member"})
    return {"Authorization": f"Bearer {tok}"}


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
async def _fresh_registry(monkeypatch):
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_engine
    from app.migrations import run_startup_migrations
    from app.services.read_cache import reset_read_cache
    from app.tenants import reset_tenant_cache

    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "0")
    monkeypatch.setenv("DB_SET_TENANT_GUC", "0")
    get_settings.cache_clear()
    reset_tenant_cache()
    reset_read_cache()
    await run_startup_migrations()
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM tenants"))
        await conn.execute(text("DELETE FROM audit_log"))
    yield
    get_settings.cache_clear()
    reset_tenant_cache()
    reset_read_cache()


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register(org: str, name: str, kind: str):
    from app.api.deals import coerce_org_tenant_id
    from app.tenants import upsert_tenant

    tid = coerce_org_tenant_id(org)
    assert tid is not None
    return await upsert_tenant(tenant_id=tid, clerk_org_id=org, name=name, kind=kind, status="active")


# ─────────────────────────── migration ───────────────────────────


@pytest.mark.asyncio
async def test_migration_adds_data_policies_once_and_is_idempotent() -> None:
    from sqlalchemy import text

    from app.database import get_engine
    from app.migrations import MIGRATIONS, SQLITE_MIGRATIONS, run_startup_migrations

    # Both dialects carry the entry; the Postgres form is the design's verbatim DDL.
    pg = dict(MIGRATIONS)["tenants.add_data_policies"]
    assert "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS data_policies JSONB NOT NULL DEFAULT '{}'" in pg
    assert "tenants.add_data_policies_sqlite" in dict(SQLITE_MIGRATIONS)

    await run_startup_migrations()  # second run on the same file — a no-op
    async with get_engine().connect() as conn:
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(tenants)"))).fetchall()]
    assert cols.count("data_policies") == 1


# ─────────────────────────── typed model ───────────────────────────


def test_typed_model_defaults_to_allow_and_rejects_unknown_keys() -> None:
    from pydantic import ValidationError

    from app.tenants import TenantDataPolicies

    assert TenantDataPolicies().costar_derived_defaults == "allow"
    assert TenantDataPolicies.model_validate({}).costar_derived_defaults == "allow"
    assert TenantDataPolicies.model_validate({"costar_derived_defaults": "withhold"}).withholds_costar
    with pytest.raises(ValidationError):
        TenantDataPolicies.model_validate({"costar_derived_default": "withhold"})  # typo
    with pytest.raises(ValidationError):
        TenantDataPolicies.model_validate({"costar_derived_defaults": "deny"})


@pytest.mark.asyncio
async def test_load_policy_default_for_unregistered_row_value_when_set_cache_cleared() -> None:
    from app.tenants import (
        TenantDataPolicies,
        get_tenant,
        load_tenant_data_policy,
        set_tenant_data_policies,
    )

    # Unregistered tenant (enforcement off): the default policy, no error.
    assert (await load_tenant_data_policy(uuid4())) == TenantDataPolicies()

    stored = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    assert (await load_tenant_data_policy(stored.id)).costar_derived_defaults == "allow"
    # Warm the registry cache, then write: the next read must see the write.
    assert (await get_tenant(stored.id)) is not None
    updated = await set_tenant_data_policies(
        stored.id, TenantDataPolicies(costar_derived_defaults="withhold")
    )
    assert updated is not None and updated.data_policies.costar_derived_defaults == "withhold"
    assert (await load_tenant_data_policy(stored.id)).costar_derived_defaults == "withhold"
    assert updated.as_dict()["data_policies"] == {"costar_derived_defaults": "withhold"}

    # No row → nothing to update (the API maps this to 404).
    assert (await set_tenant_data_policies(uuid4(), TenantDataPolicies())) is None


@pytest.mark.asyncio
async def test_load_policy_raises_when_registry_unreadable(monkeypatch) -> None:
    """An outage never reads as ``allow``: a ``withhold`` tenant must not be
    offered a CoStar default because the registry was down."""
    from app import tenants as tenants_mod

    class _Broken:
        def connect(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(tenants_mod, "get_engine", lambda: _Broken())
    with pytest.raises(tenants_mod.TenantRegistryUnavailable):
        await tenants_mod.load_tenant_data_policy(uuid4())


# ─────────────────────────── admin API ───────────────────────────


@pytest.mark.asyncio
async def test_get_and_patch_data_policies_gates_and_audit(_rsa_key) -> None:
    from sqlalchemy import text

    from app.database import get_engine

    internal = await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    customer = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    path = f"/admin/tenants/{customer.id}/data_policies"

    async with await _client() as c:
        # A member of the internal org is not an admin.
        r = await c.get(path, headers=_member(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 403, r.text
        # An external admin — even of the target tenant — is refused and told nothing.
        r = await c.get(path, headers=_admin(_rsa_key, CUSTOMER_ORG))
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "tenant_data_policies_require_internal_tenant"
        r = await c.patch(path, json={"costar_derived_defaults": "withhold"}, headers=_admin(_rsa_key, CUSTOMER_ORG))
        assert r.status_code == 403, r.text

        # Internal admin reads the default.
        r = await c.get(path, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 200, r.text
        assert r.json() == {
            "tenant_id": str(customer.id),
            "data_policies": {"costar_derived_defaults": "allow"},
        }

        # A typo is a 422, never a silent allow.
        r = await c.patch(path, json={"costar_derived_default": "withhold"}, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 422, r.text
        r = await c.patch(path, json={"costar_derived_defaults": "deny"}, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 422, r.text

        # An unregistered target has no row to carry a policy.
        r = await c.patch(
            f"/admin/tenants/{uuid4()}/data_policies",
            json={"costar_derived_defaults": "withhold"},
            headers=_admin(_rsa_key, INTERNAL_ORG),
        )
        assert r.status_code == 404, r.text

        # Flip it.
        r = await c.patch(path, json={"costar_derived_defaults": "withhold"}, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 200, r.text
        assert r.json()["data_policies"] == {"costar_derived_defaults": "withhold"}
        r = await c.get(path, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.json()["data_policies"] == {"costar_derived_defaults": "withhold"}

        # The list carries it too (the Settings card reads the list).
        r = await c.get("/admin/tenants", headers=_admin(_rsa_key, INTERNAL_ORG))
        by_org = {t["clerk_org_id"]: t for t in r.json()["tenants"]}
        assert by_org[CUSTOMER_ORG]["data_policies"] == {"costar_derived_defaults": "withhold"}
        assert by_org[INTERNAL_ORG]["data_policies"] == {"costar_derived_defaults": "allow"}

        # A no-op PATCH (same value) changes nothing and writes no audit row.
        r = await c.patch(path, json={"costar_derived_defaults": "withhold"}, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 200, r.text

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tenant_id, actor_id, resource_type, resource_id, before, after, severity "
                    "FROM audit_log WHERE action = 'tenant.data_policy_changed'"
                )
            )
        ).fetchall()
    assert len(rows) == 1, rows
    row = rows[0]._mapping
    # Written under the ACTING (internal) tenant — under RLS that is the
    # session's tenant — and pointed at the target tenant by resource id.
    assert row["tenant_id"] == str(internal.id)
    assert row["actor_id"] == "user_admin"
    assert row["resource_type"] == "tenant"
    assert row["resource_id"] == str(customer.id)
    assert json.loads(row["before"]) == {"costar_derived_defaults": "allow"}
    assert json.loads(row["after"]) == {"costar_derived_defaults": "withhold"}
    assert row["severity"] == "warning"


@pytest.mark.asyncio
async def test_admin_health_carries_data_policies_posture(_rsa_key) -> None:
    from app.tenants import TenantDataPolicies, set_tenant_data_policies

    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    customer = await _register(CUSTOMER_ORG, "Harbor Point", "external")

    async with await _client() as c:
        r = await c.get("/admin/health", headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 200, r.text
        assert r.json()["data_policies"] == {
            "tenants_total": 2,
            "tenants_with_withhold": 0,
            "invalid_rows": 0,
            "keys": ["costar_derived_defaults"],
        }
        await set_tenant_data_policies(customer.id, TenantDataPolicies(costar_derived_defaults="withhold"))
        r = await c.get("/admin/health", headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.json()["data_policies"]["tenants_with_withhold"] == 1
        # The public probe never carries it.
        assert "data_policies" not in (await c.get("/health")).json()


@pytest.mark.asyncio
async def test_admin_health_reports_a_registry_outage_as_error_not_zero(_rsa_key, monkeypatch) -> None:
    """``{tenants_total: 0}`` must never be what an outage looks like."""
    from app import tenants as tenants_mod

    await _register(INTERNAL_ORG, "Fondok Internal", "internal")

    # Direct: a dead engine surfaces the underlying exception type.
    real_engine = tenants_mod.get_engine

    class _Broken:
        def connect(self):
            raise ConnectionRefusedError("db down")

    monkeypatch.setattr(tenants_mod, "get_engine", lambda: _Broken())
    assert await tenants_mod.data_policies_posture() == {
        "error": "ConnectionRefusedError",
        "keys": ["costar_derived_defaults"],
    }
    monkeypatch.setattr(tenants_mod, "get_engine", real_engine)

    # Through the route: the gate still passes (the admin's own row is read
    # before the list dies), and the block says "error", not zeros.
    async def _dead_list(*, raise_on_error: bool = False):
        raise tenants_mod.TenantRegistryUnavailable("OperationalError: table gone")

    monkeypatch.setattr(tenants_mod, "list_tenants", _dead_list)
    async with await _client() as c:
        r = await c.get("/admin/health", headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 200, r.text
        block = r.json()["data_policies"]
        assert block["error"] == "TenantRegistryUnavailable"
        assert "tenants_total" not in block and "tenants_with_withhold" not in block


# ─────────────────────────── fail closed ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        '{"costar_derived_defaults": "withold"}',  # misspelt value
        '{"costar_derived_default": "allow"}',  # misspelt key
        "not json at all",
        "[]",  # not an object
        "42",
    ],
)
async def test_invalid_stored_policy_reads_as_withhold_and_is_counted(raw: str, caplog) -> None:
    """A manual edit or corruption of the column must never serve a CoStar
    default: every key reads as ``withhold``, the tenant is named in the
    warning, and ``/admin/health`` counts the row."""
    import logging

    from sqlalchemy import text

    from app.database import get_engine
    from app.tenants import (
        TenantDataPolicies,
        data_policies_posture,
        get_tenant,
        load_tenant_data_policy,
        reset_tenant_cache,
    )

    customer = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    assert (await load_tenant_data_policy(customer.id)).costar_derived_defaults == "allow"

    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET data_policies = :raw WHERE id = :id"),
            {"raw": raw, "id": str(customer.id)},
        )
    reset_tenant_cache()

    with caplog.at_level(logging.WARNING, logger="app.tenants"):
        policy = await load_tenant_data_policy(customer.id)
    assert policy == TenantDataPolicies.withheld()
    assert policy.costar_derived_defaults == "withhold" and policy.withholds_anything
    warning = next(r for r in caplog.records if "data_policies row" in r.getMessage())
    assert str(customer.id) in warning.getMessage()
    assert "withhold for every key" in warning.getMessage()

    row = await get_tenant(customer.id, use_cache=False)
    assert row is not None and row.data_policies_error
    assert row.as_dict()["data_policies"] == {"costar_derived_defaults": "withhold"}
    assert row.as_dict()["data_policies_error"] == row.data_policies_error

    posture = await data_policies_posture()
    assert posture["invalid_rows"] == 1
    assert posture["tenants_with_withhold"] == 1

    # A valid write through the typed path heals it.
    from app.tenants import set_tenant_data_policies

    healed = await set_tenant_data_policies(customer.id, TenantDataPolicies())
    assert healed is not None and healed.data_policies_error is None
    assert (await data_policies_posture())["invalid_rows"] == 0


def test_absent_or_empty_row_is_the_default_not_invalid() -> None:
    from app.tenants import TenantDataPolicies, _parse_data_policies

    for raw in (None, "", {}, "{}"):
        assert _parse_data_policies(raw, tenant_id="t") == (TenantDataPolicies(), None)
    assert TenantDataPolicies.withheld().as_dict() == {"costar_derived_defaults": "withhold"}


# ─────────────────────────── atomic PATCH ───────────────────────────


@pytest.mark.asyncio
async def test_patch_rolls_the_flip_back_when_the_audit_write_fails(_rsa_key, monkeypatch) -> None:
    from sqlalchemy import text

    from app.api import admin_tenants as admin_mod
    from app.database import get_engine
    from app.tenants import load_tenant_data_policy

    await _register(INTERNAL_ORG, "Fondok Internal", "internal")
    customer = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    path = f"/admin/tenants/{customer.id}/data_policies"

    async def _audit_down(session, **kwargs):
        if kwargs.get("raise_on_error"):
            raise RuntimeError("audit_log unavailable")

    monkeypatch.setattr(admin_mod, "log_audit", _audit_down)
    async with await _client() as c:
        r = await c.patch(path, json={"costar_derived_defaults": "withhold"}, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == "data_policy_audit_unavailable"
        # Nothing landed: not the policy, not the audit row, not the cache.
        r = await c.get(path, headers=_admin(_rsa_key, INTERNAL_ORG))
        assert r.json()["data_policies"] == {"costar_derived_defaults": "allow"}
    assert (await load_tenant_data_policy(customer.id)).costar_derived_defaults == "allow"
    async with get_engine().connect() as conn:
        n = (
            await conn.execute(
                text("SELECT COUNT(*) FROM audit_log WHERE action = 'tenant.data_policy_changed'")
            )
        ).scalar()
    assert n == 0


@pytest.mark.asyncio
async def test_log_audit_raises_only_when_asked() -> None:
    from app.audit import log_audit

    class _DeadSession:
        bind = None

        async def execute(self, *a, **k):
            raise RuntimeError("no audit_log")

        async def flush(self):  # pragma: no cover — execute raises first
            pass

    kwargs = dict(tenant_id=uuid4(), action="x.y", resource_type="tenant")
    await log_audit(_DeadSession(), **kwargs)  # default: swallowed, logged
    with pytest.raises(RuntimeError):
        await log_audit(_DeadSession(), raise_on_error=True, **kwargs)


# ─────────────────────────── read cache ───────────────────────────


async def _seed_deal(tenant_id: UUID) -> str:
    from sqlalchemy import text

    from app.database import get_session_factory

    deal = str(uuid4())
    async with get_session_factory()() as s:
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Hotel', 'Draft', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            ),
            {"id": deal, "tenant": str(tenant_id)},
        )
        await s.commit()
    return deal


@pytest.mark.asyncio
async def test_read_cache_fingerprint_moves_with_the_policy() -> None:
    from app.database import get_session_factory
    from app.services.read_cache import deal_state_fingerprint
    from app.tenants import TenantDataPolicies, set_tenant_data_policies

    customer = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    deal = await _seed_deal(customer.id)

    async with get_session_factory()() as s:
        before = await deal_state_fingerprint(s, deal_id=deal, tenant_id=customer.id)
        again = await deal_state_fingerprint(s, deal_id=deal, tenant_id=customer.id)
    assert before is not None and before == again

    await set_tenant_data_policies(customer.id, TenantDataPolicies(costar_derived_defaults="withhold"))
    async with get_session_factory()() as s:
        after = await deal_state_fingerprint(s, deal_id=deal, tenant_id=customer.id)
    assert after is not None and after != before

    # An unregistered tenant still fingerprints (no row is a stable part).
    stranger = uuid4()
    other_deal = await _seed_deal(stranger)
    async with get_session_factory()() as s:
        assert (await deal_state_fingerprint(s, deal_id=other_deal, tenant_id=stranger)) is not None


# ─────────────────────────── loader seam ───────────────────────────


@pytest.mark.asyncio
async def test_loader_context_carries_the_policy_object() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from app.tenants import TenantDataPolicies, set_tenant_data_policies

    customer = await _register(CUSTOMER_ORG, "Harbor Point", "external")
    deal = await _seed_deal(customer.id)

    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, deal, tenant_id=str(customer.id))
    policy = base["__data_policy__"]
    assert isinstance(policy, TenantDataPolicies)
    assert policy.costar_derived_defaults == "allow"

    await set_tenant_data_policies(customer.id, TenantDataPolicies(costar_derived_defaults="withhold"))
    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, deal, tenant_id=str(customer.id))
    assert base["__data_policy__"].costar_derived_defaults == "withhold"
    # Metadata only: not an assumption, not badged, never reaches an engine.
    assert "__data_policy__" not in base["__sources__"]


@pytest.mark.asyncio
async def test_explicit_assumptions_seam_carries_the_default_policy_without_a_read() -> None:
    """The golden path (``assumptions=...``) reads nothing from the database;
    its loader context carries the default policy object by construction."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from app.tenants import TenantDataPolicies
    from tests.fixtures.kimpton_seed import kimpton_seed

    async with get_session_factory()() as s:
        base = await _load_engine_inputs(
            s, str(uuid4()), tenant_id=str(uuid4()), assumptions=kimpton_seed()
        )
    assert base["__data_policy__"] == TenantDataPolicies()
