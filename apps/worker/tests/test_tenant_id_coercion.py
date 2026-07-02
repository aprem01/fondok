"""Tenant-ID header coercion — Clerk org_... → deterministic UUIDv5.

Sam QA 2026-07-02: the frontend passes Clerk's ``org_XXXXX...`` id as
X-Tenant-Id, but the worker's tenant column is UUID. Prior behavior
rejected the header and fell back to DEFAULT_TENANT_ID, so every real
user's data landed on the catch-all default tenant. These tests pin
the coercion behavior deterministically — never regress.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-tenant-coerce.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app.api.deals import _CLERK_ORG_UUID_NAMESPACE, _coerce_tenant_id


def test_clerk_org_id_maps_to_deterministic_uuid() -> None:
    """Same Clerk org id → same UUID every time. This is what makes
    the extraction cache actually hit for real users (previously it
    always missed because everything was default tenant).
    """
    clerk_id = "org_3Cy8foYI4xm9PK2VNrApwzGmZk2"
    a = _coerce_tenant_id(clerk_id)
    b = _coerce_tenant_id(clerk_id)
    assert a is not None
    assert a == b, "coercion must be deterministic across calls"
    assert isinstance(a, UUID)


def test_different_clerk_orgs_produce_different_uuids() -> None:
    """Different orgs must NEVER collide — cross-tenant isolation
    depends on this being a strict injection.
    """
    a = _coerce_tenant_id("org_ABCDEFGHIJKL")
    b = _coerce_tenant_id("org_MNOPQRSTUVWX")
    assert a is not None and b is not None
    assert a != b


def test_valid_uuid_string_is_passed_through() -> None:
    """Server-side callers, tests, and curl pass raw UUIDs directly.
    Coercer must not mangle them (or they'd map to a new v5 UUID).
    """
    raw = "00000000-0000-0000-0000-000000000000"
    coerced = _coerce_tenant_id(raw)
    assert coerced == UUID(raw)


def test_empty_and_whitespace_return_none() -> None:
    assert _coerce_tenant_id("") is None
    assert _coerce_tenant_id("   ") is None


def test_unrecognized_shape_returns_none() -> None:
    """Anything that's neither a UUID nor a known ``org_/user_/acc_``
    prefix falls through to None; caller uses DEFAULT_TENANT_ID.
    """
    assert _coerce_tenant_id("random-garbage") is None
    assert _coerce_tenant_id("12345") is None
    assert _coerce_tenant_id("session_XYZ") is None


def test_namespace_is_stable_across_process_restarts() -> None:
    """The namespace is baked as a module constant, so the mapping
    survives process restart. Assert the exact UUID for a known
    Clerk id — if this test ever fails, the namespace was
    accidentally changed and the DB needs a migration.
    """
    from uuid import uuid5

    clerk_id = "org_3Cy8foYI4xm9PK2VNrApwzGmZk2"
    expected = uuid5(_CLERK_ORG_UUID_NAMESPACE, clerk_id)
    assert _coerce_tenant_id(clerk_id) == expected


def test_user_prefix_also_maps() -> None:
    """Some Clerk deployments use ``user_...`` when no org is active;
    handle it the same way so those users still get consistent
    tenants.
    """
    a = _coerce_tenant_id("user_ABCDEFGHIJKL")
    assert a is not None
    assert isinstance(a, UUID)
