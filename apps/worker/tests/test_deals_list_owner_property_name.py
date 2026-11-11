"""FON-80 — the deal list names each deal's creator and its property name.

``created_by`` is the ``actor_id`` of the deal's ``deal.created`` /
``deal.draft_created`` audit row (``deals`` has no creator column — the audit
trail is the record); a header-only create, whose actor is ``system``, reads
NULL. ``property_name`` is the analyst's ``property_overview.name`` override,
else the OM's extracted name (then the STR subject name) — never the project
``name``. Both ride on ``GET /deals`` and ``GET /deals/{id}``.

Pattern: ``tests/test_deals_drafts.py`` (per-module SQLite DB, header
tenancy; a locally minted RS256 JWT for the signed-in case).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deals-list-owner.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

TENANT_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
HDR_A = {"X-Tenant-Id": TENANT_A}


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "extraction_results", "documents", "engine_outputs", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    from app.services.pipeline import invalidate

    invalidate()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create(client, headers: dict[str, str] = HDR_A, **body: Any) -> dict[str, Any]:
    body.setdefault("name", "Project Harbor")
    r = await client.post("/deals", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _seed_extraction(
    deal_id: str, *, doc_type: str, fields: list[dict[str, Any]], minutes_ago: int = 0,
    tenant: str = TENANT_A,
) -> None:
    """One EXTRACTED document + one extraction row carrying ``fields``."""
    from sqlalchemy import text

    from app.database import get_session_factory

    doc_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status) "
                "VALUES (:id, :deal, :tenant, :fn, :doc_type, 'EXTRACTED')"
            ),
            {"id": doc_id, "deal": deal_id, "tenant": tenant, "fn": f"{doc_type.lower()}.pdf", "doc_type": doc_type},
        )
        await session.execute(
            text(
                "INSERT INTO extraction_results (id, deal_id, document_id, tenant_id, fields, created_at) "
                "VALUES (:id, :deal, :doc, :tenant, :fields, :created)"
            ),
            {
                "id": str(uuid4()), "deal": deal_id, "doc": doc_id, "tenant": tenant,
                "fields": json.dumps(fields),
                "created": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(sep=" "),
            },
        )
        await session.commit()


def _mint_jwt(monkeypatch: pytest.MonkeyPatch, *, sub: str, org_id: str) -> str:
    """A locally signed Clerk-shaped RS256 token published through the JWKS
    cache — the same recipe as ``test_deals_drafts.py``."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    from app.auth import clerk_jwt

    def _b64url_uint(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    kid = "test-kid-fon80"
    jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
           "n": _b64url_uint(numbers.n), "e": _b64url_uint(numbers.e)}
    keys = {kid: RSAAlgorithm.from_jwk(jwk)}
    monkeypatch.setattr(clerk_jwt, "_JWKS_KEYS", keys)
    monkeypatch.setattr(clerk_jwt, "_JWKS_FETCHED_AT", time.monotonic())
    monkeypatch.setattr(clerk_jwt, "_load_jwks", lambda *, force=False: keys)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pyjwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 300, "sub": sub,
         "org_id": org_id, "org_role": "org:member"},
        pem, algorithm="RS256", headers={"kid": kid},
    )


# ─────────────────────────── created_by ───────────────────────────


@pytest.mark.asyncio
async def test_header_only_create_records_no_creator() -> None:
    """A header-tenancy create audits ``actor_id='system'`` — that is not a
    person, so ``created_by`` is NULL on create, list and get."""
    async with _client() as client:
        created = await _create(client)
        assert created["created_by"] is None
        listed = (await client.get("/deals", headers=HDR_A)).json()
        assert [d["created_by"] for d in listed] == [None]
        one = (await client.get(f"/deals/{created['id']}", headers=HDR_A)).json()
        assert one["created_by"] is None


@pytest.mark.asyncio
async def test_signed_in_create_names_the_caller_on_list_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JWT caller is the creator — for a direct create and for a draft —
    read back from the create audit row, not from a column the schema lacks."""
    from app.api.deals import _coerce_tenant_id

    org_id = "org_FON80"
    token = _mint_jwt(monkeypatch, sub="user_fon80_author", org_id=org_id)
    auth = {"Authorization": f"Bearer {token}"}
    tenant = str(_coerce_tenant_id(org_id))

    async with _client() as client:
        active = await _create(client, auth, name="Signed Active")
        assert active["tenant_id"] == tenant
        assert active["created_by"] == "user_fon80_author"
        draft = await _create(client, auth, name="Signed Draft", lifecycle="draft",
                              draft_wizard={"v": 1, "step": 1, "fields": {}})
        assert draft["created_by"] == "user_fon80_author"

        listed = (await client.get("/deals", headers=auth)).json()
        assert {d["name"]: d["created_by"] for d in listed} == {
            "Signed Active": "user_fon80_author",
            "Signed Draft": "user_fon80_author",
        }
        one = (await client.get(f"/deals/{active['id']}", headers=auth)).json()
        assert one["created_by"] == "user_fon80_author"
        # A later audit row by someone else does not change who started it.
        from app.audit import log_audit
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await log_audit(
                session, tenant_id=tenant, actor_id="user_someone_else",
                action="deal.updated", resource_type="deal", resource_id=active["id"],
            )
            await session.commit()
        one = (await client.get(f"/deals/{active['id']}", headers=auth)).json()
        assert one["created_by"] == "user_fon80_author"


# ─────────────────────────── property_name ───────────────────────────


@pytest.mark.asyncio
async def test_property_name_is_extracted_om_first_then_overridden_never_the_project_name() -> None:
    async with _client() as client:
        harbor = await _create(client, name="Project Harbor")
        other = await _create(client, name="Project Two")
        bare = await _create(client, name="Project Bare")

        # Nothing extracted yet: NULL, not "Project Harbor".
        listed = {d["name"]: d["property_name"] for d in (await client.get("/deals", headers=HDR_A)).json()}
        assert listed == {"Project Harbor": None, "Project Two": None, "Project Bare": None}

        # An older STR subject name and a newer OM name: the OM wins.
        await _seed_extraction(
            harbor["id"], doc_type="STR_TREND", minutes_ago=30,
            fields=[{"field_name": "ttm_performance.subject.name", "value": "Harbor STR Subject"}],
        )
        await _seed_extraction(
            harbor["id"], doc_type="OM", minutes_ago=5,
            fields=[
                {"field_name": "property_overview.year_built", "value": 1998},
                {"field_name": "property_overview.name", "value": "  The Liberty Hotel  "},
            ],
        )
        # The other deal only has the STR subject name — that is its name.
        await _seed_extraction(
            other["id"], doc_type="STR_TREND",
            fields=[{"field_name": "ttm_performance.subject.name", "value": "Two Subject"}],
        )
        listed = {d["name"]: d["property_name"] for d in (await client.get("/deals", headers=HDR_A)).json()}
        assert listed == {
            "Project Harbor": "The Liberty Hotel",
            "Project Two": "Two Subject",
            "Project Bare": None,
        }
        one = (await client.get(f"/deals/{harbor['id']}", headers=HDR_A)).json()
        assert one["property_name"] == "The Liberty Hotel"
        # The list agrees with the Overview's own resolution.
        overview = (await client.get(f"/market/{harbor['id']}/overview", headers=HDR_A)).json()
        assert overview["property_name"] == "The Liberty Hotel"

        # The analyst's override (the structured shape) beats the extraction;
        # a blank override does not blank the extracted name.
        from sqlalchemy import text

        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("UPDATE deals SET field_overrides = :fo WHERE id = :id AND tenant_id = :t"),
                {
                    "fo": json.dumps({"property_overview.name": {"value": "Liberty (renamed)", "note": "per PSA"}}),
                    "id": harbor["id"], "t": TENANT_A,
                },
            )
            await session.execute(
                text("UPDATE deals SET field_overrides = :fo WHERE id = :id AND tenant_id = :t"),
                {"fo": json.dumps({"property_overview.name": "   "}), "id": other["id"], "t": TENANT_A},
            )
            await session.commit()
        listed = {d["name"]: d["property_name"] for d in (await client.get("/deals", headers=HDR_A)).json()}
        assert listed["Project Harbor"] == "Liberty (renamed)"
        assert listed["Project Two"] == "Two Subject"
        assert listed["Project Bare"] is None


@pytest.mark.asyncio
async def test_property_name_is_tenant_scoped() -> None:
    """Another tenant's extraction never names this tenant's deal."""
    tenant_b = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    async with _client() as client:
        mine = await _create(client, name="Mine")
        # A foreign-tenant extraction row pointing at my deal id (defence in depth).
        await _seed_extraction(
            mine["id"], doc_type="OM", tenant=tenant_b,
            fields=[{"field_name": "property_overview.name", "value": "Leaked Name"}],
        )
        listed = (await client.get("/deals", headers=HDR_A)).json()
        assert [d["property_name"] for d in listed] == [None]
