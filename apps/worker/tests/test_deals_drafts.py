"""Server-side deal drafts — FON-79 (go-live plan ITEM A, PR A-1).

A draft is a real ``deals`` row with ``lifecycle='draft'``: it owns documents,
appears in the tenant-scoped list, and carries the wizard's resume payload in
``draft_wizard``. Activate (``POST /deals/{id}/activate``) turns it into an
underwriting; discard (``DELETE /deals/{id}/draft``) removes it with the same
cascade hard delete uses, plus the raw-store objects. ``assert_deal_active``
keeps a draft from ever being run, exported, memo'd, asked, duplicated,
transitioned or gated (409 ``deal_is_draft``); GETs stay open so the wizard
and the Data Room can read it.

Pattern: ``tests/test_deals_crud.py`` (per-module SQLite DB, header tenancy).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deals-drafts.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-deals-drafts-storage"
)

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

TENANT_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
HDR_A = {"X-Tenant-Id": TENANT_A}
HDR_B = {"X-Tenant-Id": TENANT_B}

#: The wizard's raw resume payload — the analyst's text as typed (a price
#: range, a market with no id, UI state), not the normalized columns.
WIZARD: dict[str, Any] = {
    "v": 1,
    "step": 2,
    "fields": {
        "dealName": "Draft Hotel",
        "price": "$120-140M",
        "market": "Miami",
        "expandedFamilies": ["Marriott"],
    },
}


# ─────────────────────────── fixtures / helpers ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Migrate, then truncate every table a test here touches."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "audit_log",
            "extraction_results",
            "documents",
            "scenarios",
            "engine_outputs",
            "deals",
        ):
            with contextlib.suppress(Exception):  # table may not exist yet
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
    body.setdefault("name", "Probe Hotel")
    r = await client.post("/deals", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _create_draft(client, headers: dict[str, str] = HDR_A, **extra: Any) -> dict[str, Any]:
    extra.setdefault("name", "Draft Hotel")
    return await _create(client, headers, lifecycle="draft", draft_wizard=WIZARD, **extra)


async def _audit_rows(deal_id: str, action: str, tenant: str = TENANT_A) -> list[dict[str, Any]]:
    """Every audit row for ``action`` on ``deal_id`` (tenant-scoped), payload parsed."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT actor_id, payload FROM audit_log "
                    "WHERE resource_id = :rid AND action = :action AND tenant_id = :tenant "
                    "ORDER BY rowid"
                ),
                {"rid": deal_id, "action": action, "tenant": tenant},
            )
        ).all()
    out = []
    for r in rows:
        payload = r._mapping["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append({"actor_id": r._mapping["actor_id"], **(payload or {})})
    return out


async def _count(table: str, deal_id: str, column: str = "deal_id") -> int:
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :id AND tenant_id = :t"),
                {"id": deal_id, "t": TENANT_A},
            )
        ).first()
    return int(row[0])


def _build_sample_pdf() -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    _, height = LETTER
    t = c.beginText(72, height - 72)
    t.setFont("Helvetica", 10)
    t.textLine("Draft upload sample. Keys: 132")
    c.drawText(t)
    c.showPage()
    c.save()
    return buf.getvalue()


# ─────────────────────────── create / read / list ───────────────────────────


@pytest.mark.asyncio
async def test_create_draft_is_a_draft_row_with_the_wizard_echoed() -> None:
    async with _client() as client:
        created = await _create_draft(client)
        assert created["lifecycle"] == "draft"
        assert created["status"] == "Draft"
        assert created["activated_at"] is None
        assert created["draft_wizard"] == WIZARD

        # GET single returns the resume payload only when asked for it.
        r = await client.get(f"/deals/{created['id']}", headers=HDR_A)
        assert r.status_code == 200, r.text
        assert r.json()["lifecycle"] == "draft"
        assert r.json()["draft_wizard"] is None
        r = await client.get(
            f"/deals/{created['id']}", params={"include": "draft_wizard"}, headers=HDR_A
        )
        assert r.status_code == 200, r.text
        assert r.json()["draft_wizard"] == WIZARD

    actions = [a for a in ("deal.created", "deal.draft_created")
               if await _audit_rows(created["id"], a)]
    assert actions == ["deal.draft_created"]


@pytest.mark.asyncio
async def test_direct_active_create_is_in_review_and_unchanged_otherwise() -> None:
    """The word "Draft" means one thing from go-live: a lifecycle draft."""
    async with _client() as client:
        created = await _create(client, name="Real Hotel", city="Austin, TX", keys=120)
        assert created["lifecycle"] == "active"
        assert created["status"] == "In Review"
        assert created["draft_wizard"] is None
        assert created["activated_at"] is None
    assert await _audit_rows(created["id"], "deal.created")
    assert not await _audit_rows(created["id"], "deal.draft_created")


@pytest.mark.asyncio
async def test_draft_wizard_on_an_active_create_is_422() -> None:
    async with _client() as client:
        r = await client.post(
            "/deals", json={"name": "X", "draft_wizard": WIZARD}, headers=HDR_A
        )
        assert r.status_code == 422, r.text
        r = await client.post(
            "/deals", json={"name": "X", "lifecycle": "archived"}, headers=HDR_A
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_list_filter_by_lifecycle_and_omits_the_wizard_blob() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        active = await _create(client, name="Active Hotel")

        r = await client.get("/deals", headers=HDR_A)
        assert r.status_code == 200, r.text
        rows = {d["id"]: d for d in r.json()}
        assert set(rows) == {draft["id"], active["id"]}
        assert rows[draft["id"]]["lifecycle"] == "draft"
        assert rows[active["id"]]["lifecycle"] == "active"
        # The list keeps payloads small: no wizard blob on any row.
        assert all(d["draft_wizard"] is None for d in r.json())

        r = await client.get("/deals", params={"lifecycle": "draft"}, headers=HDR_A)
        assert [d["id"] for d in r.json()] == [draft["id"]]
        r = await client.get("/deals", params={"lifecycle": "active"}, headers=HDR_A)
        assert [d["id"] for d in r.json()] == [active["id"]]
        r = await client.get("/deals", params={"lifecycle": "all"}, headers=HDR_A)
        assert len(r.json()) == 2
        r = await client.get("/deals", params={"lifecycle": "bogus"}, headers=HDR_A)
        assert r.status_code == 422, r.text

        # Another tenant sees none of them, drafts included.
        r = await client.get("/deals", params={"lifecycle": "draft"}, headers=HDR_B)
        assert r.json() == []


@pytest.mark.asyncio
async def test_pipeline_excludes_drafts_and_reports_draft_count_beside() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        active = await _create(client, name="Active Hotel", keys=100)
        r = await client.get("/deals/pipeline", headers=HDR_A)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [d["deal_id"] for d in body["deals"]] == [active["id"]]
        assert body["summary"]["deal_count"] == 1
        assert body["summary"]["draft_count"] == 1
        assert body["total_count"] == 1
        assert draft["id"] not in {d["deal_id"] for d in body["deals"]}

        # Draft count is per tenant.
        r = await client.get("/deals/pipeline", headers=HDR_B)
        assert r.json()["summary"] == {**r.json()["summary"], "deal_count": 0, "draft_count": 0}


# ─────────────────────────── PATCH / activate ───────────────────────────


@pytest.mark.asyncio
async def test_patch_draft_wizard_on_an_active_deal_is_409() -> None:
    async with _client() as client:
        active = await _create(client, name="Active Hotel")
        r = await client.patch(
            f"/deals/{active['id']}", json={"draft_wizard": WIZARD}, headers=HDR_A
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_not_draft"
        # Nothing else on the row moved.
        r = await client.get(f"/deals/{active['id']}", params={"include": "draft_wizard"}, headers=HDR_A)
        assert r.json()["draft_wizard"] is None
        assert r.json()["lifecycle"] == "active"


@pytest.mark.asyncio
async def test_patch_on_a_draft_writes_columns_and_the_wizard_together() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        new_wizard = {**WIZARD, "step": 3, "fields": {**WIZARD["fields"], "keys": "140"}}
        r = await client.patch(
            f"/deals/{draft['id']}",
            json={"keys": 140, "city": "Miami, FL", "draft_wizard": new_wizard},
            headers=HDR_A,
        )
        assert r.status_code == 200, r.text
        assert r.json()["keys"] == 140
        assert r.json()["lifecycle"] == "draft"
        r = await client.get(f"/deals/{draft['id']}", params={"include": "draft_wizard"}, headers=HDR_A)
        assert r.json()["draft_wizard"] == new_wizard
        assert r.json()["city"] == "Miami, FL"


@pytest.mark.asyncio
async def test_activate_turns_the_draft_into_an_underwriting_once() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.post(f"/deals/{draft['id']}/activate", headers=HDR_A)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lifecycle"] == "active"
        assert body["status"] == "In Review"
        assert body["draft_wizard"] is None
        assert body["activated_at"] is not None

        r = await client.get(f"/deals/{draft['id']}", params={"include": "draft_wizard"}, headers=HDR_A)
        assert r.json()["draft_wizard"] is None
        assert r.json()["lifecycle"] == "active"
        assert r.json()["activated_at"] is not None

        # Twice → 409.
        r = await client.post(f"/deals/{draft['id']}/activate", headers=HDR_A)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_not_draft"

        # Now it counts in the pipeline.
        r = await client.get("/deals/pipeline", headers=HDR_A)
        assert r.json()["summary"]["deal_count"] == 1
        assert r.json()["summary"]["draft_count"] == 0

    rows = await _audit_rows(draft["id"], "deal.activated")
    assert len(rows) == 1
    assert rows[0]["output"]["steps_completed"] == WIZARD["step"]
    assert rows[0]["output"]["document_count"] == 0


@pytest.mark.asyncio
async def test_activate_with_a_blank_name_is_422() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        # ``min_length=1`` lets whitespace through; activate must not.
        r = await client.patch(f"/deals/{draft['id']}", json={"name": "   "}, headers=HDR_A)
        assert r.status_code == 200, r.text
        r = await client.post(f"/deals/{draft['id']}/activate", headers=HDR_A)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "deal_name_required"
        # Still a draft.
        r = await client.get(f"/deals/{draft['id']}", headers=HDR_A)
        assert r.json()["lifecycle"] == "draft"


# ─────────────────────────── discard ───────────────────────────


@pytest.mark.asyncio
async def test_discard_on_an_active_deal_is_409_and_deletes_nothing() -> None:
    async with _client() as client:
        active = await _create(client, name="Active Hotel")
        r = await client.delete(f"/deals/{active['id']}/draft", headers=HDR_A)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_not_draft"
        r = await client.get(f"/deals/{active['id']}", headers=HDR_A)
        assert r.status_code == 200
    assert await _count("scenarios", active["id"]) == 1


async def _seed_document_with_object(deal_id: str, tenant: str = TENANT_A) -> tuple[str, str]:
    """A ``documents`` row + an ``extraction_results`` row + a real raw-store
    object, the way an upload leaves them. Returns (doc_id, storage_key)."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.storage import get_store, reset_raw_store_cache

    reset_raw_store_cache()
    store = get_store()
    doc_id = str(uuid4())
    content_hash = uuid4().hex
    storage_key = await store.put(
        tenant_id=tenant, deal_id=deal_id, content_hash=content_hash,
        filename="om.pdf", bytes_=b"%PDF-1.4 probe",
    )
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, "
                "content_hash, storage_key, uploaded_at) VALUES (:id, :deal, :t, 'om.pdf', 'OM', "
                "'EXTRACTED', :hash, :key, CURRENT_TIMESTAMP)"
            ),
            {"id": doc_id, "deal": deal_id, "t": tenant, "hash": content_hash, "key": storage_key},
        )
        await session.execute(
            text(
                "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields, "
                "created_at) VALUES (:id, :doc, :deal, :t, '[]', CURRENT_TIMESTAMP)"
            ),
            {"id": str(uuid4()), "doc": doc_id, "deal": deal_id, "t": tenant},
        )
        await session.commit()
    return doc_id, storage_key


def _object_exists(storage_key: str) -> bool:
    from app.storage.raw_store import _file_uri_to_path

    return _file_uri_to_path(storage_key).exists()


@pytest.mark.asyncio
async def test_discard_removes_the_deal_its_children_and_its_raw_objects() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        deal_id = draft["id"]
        _doc_id, storage_key = await _seed_document_with_object(deal_id)
        assert _object_exists(storage_key)
        assert await _count("documents", deal_id) == 1
        assert await _count("extraction_results", deal_id) == 1
        assert await _count("scenarios", deal_id) == 1  # the Base scenario

        r = await client.delete(f"/deals/{deal_id}/draft", headers=HDR_A)
        assert r.status_code == 204, r.text

        r = await client.get(f"/deals/{deal_id}", headers=HDR_A)
        assert r.status_code == 404
        r = await client.get("/deals", params={"lifecycle": "draft"}, headers=HDR_A)
        assert r.json() == []

    assert await _count("documents", deal_id) == 0
    assert await _count("extraction_results", deal_id) == 0
    assert await _count("scenarios", deal_id) == 0
    assert await _count("deals", deal_id, column="id") == 0
    # The raw object is gone too (hard delete used to leave these behind).
    assert not _object_exists(storage_key)

    rows = await _audit_rows(deal_id, "deal.draft_discarded")
    assert len(rows) == 1
    assert rows[0]["metadata"]["name"] == "Draft Hotel"
    assert rows[0]["metadata"]["cascade_counts"]["documents"] == 1
    assert rows[0]["metadata"]["cascade_counts"]["extraction_results"] == 1
    assert rows[0]["metadata"]["cascade_counts"]["scenarios"] == 1
    assert rows[0]["metadata"]["cascade_counts"]["deals"] == 1
    # Objects are removed AFTER the commit (a storage failure never rolls the
    # rows back), so the row records what was scheduled; the file check above
    # proves it happened.
    assert rows[0]["metadata"]["raw_objects_to_delete"] == 1


@pytest.mark.asyncio
async def test_discard_keeps_a_raw_object_another_document_still_references() -> None:
    """A duplicated deal shares the original's objects by ``storage_key``."""
    from sqlalchemy import text

    from app.database import get_session_factory

    async with _client() as client:
        draft = await _create_draft(client)
        keeper = await _create(client, name="Keeper Hotel")
        _doc, storage_key = await _seed_document_with_object(draft["id"])
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, status, "
                    "content_hash, storage_key, uploaded_at) VALUES (:id, :deal, :t, 'om.pdf', "
                    "'EXTRACTED', 'h', :key, CURRENT_TIMESTAMP)"
                ),
                {"id": str(uuid4()), "deal": keeper["id"], "t": TENANT_A, "key": storage_key},
            )
            await session.commit()
        r = await client.delete(f"/deals/{draft['id']}/draft", headers=HDR_A)
        assert r.status_code == 204, r.text
    assert _object_exists(storage_key)
    assert await _count("documents", keeper["id"]) == 1


@pytest.mark.asyncio
async def test_hard_delete_shares_the_cascade_and_now_removes_raw_objects() -> None:
    """The header path passes ``require_role("admin")`` (ops runbooks / tests)."""
    async with _client() as client:
        active = await _create(client, name="Doomed Hotel")
        _doc, storage_key = await _seed_document_with_object(active["id"])
        r = await client.delete(f"/deals/{active['id']}/hard", headers=HDR_A)
        assert r.status_code == 204, r.text
    assert await _count("documents", active["id"]) == 0
    assert await _count("scenarios", active["id"]) == 0
    assert not _object_exists(storage_key)
    rows = await _audit_rows(active["id"], "deal.hard_deleted")
    assert len(rows) == 1
    assert rows[0]["metadata"]["raw_objects_to_delete"] == 1


# ─────────────────────────── the guard ───────────────────────────


#: (method, path template, body) — every mutating / underwriting route a draft
#: must refuse. ``{deal_id}`` / ``{scenario_id}`` are filled per test.
GUARDED_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/deals/{deal_id}/engines/run", {}),
    ("POST", "/deals/{deal_id}/engines/revenue/run", {}),
    ("POST", "/deals/{deal_id}/engines/returns/preview", {}),
    ("GET", "/deals/{deal_id}/export/excel", None),
    ("GET", "/deals/{deal_id}/export/memo.pdf", None),
    ("GET", "/deals/{deal_id}/export/presentation.pptx", None),
    ("POST", "/deals/{deal_id}/memo/generate", {}),
    ("GET", "/deals/{deal_id}/memo/stream", None),
    ("POST", "/deals/{deal_id}/ask", {"question": "What is the going-in cap rate?"}),
    ("POST", "/deals/{deal_id}/duplicate", {}),
    ("POST", "/deals/{deal_id}/transition", {"next_state": "VALIDATING"}),
    ("POST", "/deals/{deal_id}/gate1", {"decision": "approve"}),
    ("POST", "/deals/{deal_id}/gate2", {"recommendation": "go"}),
    ("POST", "/deals/{deal_id}/scenarios/{scenario_id}/run", None),
]


@pytest.mark.parametrize("method,template,body", GUARDED_ROUTES, ids=lambda x: x if isinstance(x, str) and "/" in x else "")
@pytest.mark.asyncio
async def test_a_draft_cannot_be_run_exported_or_memoed(
    method: str, template: str, body: dict[str, Any] | None
) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    async with _client() as client:
        draft = await _create_draft(client, keys=120, purchase_price=25_000_000)
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT id FROM scenarios WHERE deal_id = :d AND tenant_id = :t"),
                    {"d": draft["id"], "t": TENANT_A},
                )
            ).first()
        scenario_id = str(row[0])
        path = template.format(deal_id=draft["id"], scenario_id=scenario_id)
        r = await client.request(method, path, json=body, headers=HDR_A)
        assert r.status_code == 409, (path, r.status_code, r.text[:300])
        assert r.json()["detail"]["code"] == "deal_is_draft", r.text

        # Nothing ran, nothing was copied.
        r = await client.get("/deals", headers=HDR_A)
        assert [d["id"] for d in r.json()] == [draft["id"]]
    assert await _count("engine_outputs", draft["id"]) == 0


@pytest.mark.asyncio
async def test_reads_stay_open_on_a_draft() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        for path in (
            f"/deals/{draft['id']}",
            f"/deals/{draft['id']}/documents",
            f"/deals/{draft['id']}/status",
            f"/deals/{draft['id']}/engines",
        ):
            r = await client.get(path, headers=HDR_A)
            assert r.status_code == 200, (path, r.status_code, r.text[:300])
        # And the guard does not fire on an active deal (transition is the
        # cheapest route that reaches it and writes).
        active = await _create(client, name="Active Hotel")
        r = await client.post(
            f"/deals/{active['id']}/transition", json={"next_state": "VALIDATING"}, headers=HDR_A
        )
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_guard_answers_404_for_a_foreign_deal_before_409() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.post(f"/deals/{draft['id']}/engines/run", json={}, headers=HDR_B)
        assert r.status_code == 404, r.text
        r = await client.post(f"/deals/{draft['id']}/transition", json={"next_state": "VALIDATING"}, headers=HDR_B)
        assert r.status_code == 404, r.text


# ─────────────────────────── tenancy on the new routes ───────────────────────────


@pytest.mark.asyncio
async def test_new_routes_404_a_foreign_deal_and_401_without_a_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.post(f"/deals/{draft['id']}/activate", headers=HDR_B)
        assert r.status_code == 404, r.text
        r = await client.delete(f"/deals/{draft['id']}/draft", headers=HDR_B)
        assert r.status_code == 404, r.text
        # The foreign attempts changed nothing.
        r = await client.get(f"/deals/{draft['id']}", headers=HDR_A)
        assert r.status_code == 200 and r.json()["lifecycle"] == "draft"

        monkeypatch.setenv("ALLOW_TENANT_HEADER_WITHOUT_JWT", "0")
        get_settings.cache_clear()
        try:
            r = await client.post(f"/deals/{draft['id']}/activate")
            assert r.status_code == 401, r.text
            r = await client.delete(f"/deals/{draft['id']}/draft")
            assert r.status_code == 401, r.text
            r = await client.get("/deals", params={"lifecycle": "draft"})
            assert r.status_code == 401, r.text
        finally:
            monkeypatch.setenv("ALLOW_TENANT_HEADER_WITHOUT_JWT", "1")
            get_settings.cache_clear()


# ─────────────────────────── uploads on a draft ───────────────────────────


@pytest.mark.asyncio
async def test_upload_onto_a_draft_works_and_metadata_sync_fills_keys() -> None:
    """Extractions run on draft uploads exactly as today (A.2 recommendation):
    the upload is accepted, the row is listed, and the OM → deal metadata sync
    fills ``keys`` on the draft row."""
    from app.api.documents import _sync_deal_metadata_from_extraction
    from app.database import get_session_factory
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.post(
            f"/deals/{draft['id']}/documents/upload",
            files={"files": ("om.pdf", _build_sample_pdf(), "application/pdf")},
            data={"user_doc_types": ["OM"]},
            headers=HDR_A,
        )
        assert r.status_code == 201, r.text
        r = await client.get(f"/deals/{draft['id']}/documents", headers=HDR_A)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["filename"] == "om.pdf"

        factory = get_session_factory()
        async with factory() as session:
            await _sync_deal_metadata_from_extraction(
                session,
                deal_id=draft["id"],
                tenant_id=TENANT_A,
                fields=[{"field_name": "property_overview.keys", "value": 132}],
                doc_type="OM",
            )
            await session.commit()

        r = await client.get(f"/deals/{draft['id']}", params={"include": "draft_wizard"}, headers=HDR_A)
        assert r.status_code == 200, r.text
        assert r.json()["keys"] == 132
        assert r.json()["lifecycle"] == "draft"
        assert r.json()["draft_wizard"] == WIZARD  # the sync never touches the wizard
        # The list (the only endpoint that joins ``documents``) sees the upload.
        r = await client.get("/deals", params={"lifecycle": "draft"}, headers=HDR_A)
        assert [d["document_count"] for d in r.json()] == [1]


# ─────────────────────────── review fixes (coordinator, 2026-10-30) ───────────────────────────


def _deal_id_tables_in(entries: list[tuple[str, str]]) -> set[str]:
    """Every table a migration list creates with a ``deal_id`` or a
    ``document_id`` column (parsed from the CREATE TABLE statements)."""
    import re

    found: set[str] = set()
    for _name, sql in entries:
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*)\)", sql, re.S)
        if not m:
            continue
        cols = {
            line.strip().split()[0]
            for line in m.group(2).splitlines()
            if line.strip() and not line.strip().startswith(("--", "UNIQUE", "PRIMARY", "CHECK", "FOREIGN", "CONSTRAINT"))
        }
        if {"deal_id", "document_id"} & cols:
            found.add(m.group(1))
    return found


def test_the_cascade_names_every_deal_id_table_in_both_migration_sets() -> None:
    """A new table with a ``deal_id`` must be added to ``_DEAL_CASCADE`` (or
    deliberately to ``_DEAL_CASCADE_KEPT``) — otherwise discard / hard delete
    would leave its rows behind on SQLite and on every Postgres table without
    ``ON DELETE CASCADE``."""
    from app.api.deals import _DEAL_CASCADE, _DEAL_CASCADE_KEPT
    from app.migrations import MIGRATIONS, SQLITE_MIGRATIONS

    cascade = {table for table, _ in _DEAL_CASCADE}
    schema = (_deal_id_tables_in(MIGRATIONS) | _deal_id_tables_in(SQLITE_MIGRATIONS)) - {"deals"}
    missing = schema - cascade - _DEAL_CASCADE_KEPT
    assert not missing, f"deal_id table(s) missing from the cascade: {sorted(missing)}"
    unknown = cascade - schema
    assert not unknown, f"cascade names table(s) no migration creates: {sorted(unknown)}"
    assert {"audit_log", "memo_edits"} == _DEAL_CASCADE_KEPT  # both append-only (Postgres triggers)
    # Children of documents go before documents; the deal row goes last.
    order = [t for t, _ in _DEAL_CASCADE]
    assert order.index("extraction_results") < order.index("documents")
    assert order.index("document_chunks") < order.index("documents")
    assert order.index("broker_qa_pairs") < order.index("broker_questions")
    assert order[-1] == "documents"


async def _seed_every_child_table(deal_id: str, doc_id: str, tenant: str = TENANT_A) -> list[str]:
    """One row per live table that carries ``deal_id``, filled from
    ``PRAGMA table_info`` (every NOT NULL column without a default gets a
    type-appropriate probe value). Returns the tables seeded."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    seeded: list[str] = []
    async with factory() as session:
        tables = [
            str(r[0])
            for r in (
                await session.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
            ).fetchall()
        ]
        for table in tables:
            info = (await session.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            names = {str(r[1]) for r in info}
            if table == "deals" or "deal_id" not in names:
                continue
            row: dict[str, Any] = {}
            for _cid, name, ctype, notnull, dflt, pk in info:
                name, ctype = str(name), str(ctype or "").upper()
                if name == "deal_id":
                    row[name] = deal_id
                elif name == "tenant_id":
                    row[name] = tenant
                elif name == "document_id":
                    row[name] = doc_id
                elif pk or name == "id" or name.endswith("_id"):
                    row[name] = str(uuid4())
                elif notnull and dflt is None:
                    row[name] = 1 if "INT" in ctype else 1.0 if "REAL" in ctype else "probe"
            if "created_at" in names and "created_at" not in row:
                row["created_at"] = "2026-10-30T00:00:00+00:00"
            if "status" in names and "status" not in row and table == "documents":
                row["status"] = "EXTRACTED"
            cols = ", ".join(row)
            binds = ", ".join(f":{c}" for c in row)
            await session.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({binds})"), row)
            seeded.append(table)
        await session.commit()
    return seeded


async def _rows_by_table(deal_id: str, tables: list[str], tenant: str = TENANT_A) -> dict[str, int]:
    return {t: await _count(t, deal_id) for t in tables}


@pytest.mark.asyncio
async def test_discard_empties_every_child_table_and_spares_the_sibling_deal() -> None:
    """Rows in EVERY live table with a ``deal_id`` — including the ones with
    no FK (``model_calls``, ``lineage_records``, ``verification_reports``,
    ``critic_*``, ``cell_comments``, ``pending_batches``, ``due_diligence_questions``)
    — are gone after discard, except the two append-only trails; a sibling
    deal's rows in the same tables are untouched."""
    from app.api.deals import _DEAL_CASCADE_KEPT

    async with _client() as client:
        draft = await _create_draft(client)
        sibling = await _create_draft(client, name="Sibling Draft")
        doc_draft = str(uuid4())
        doc_sibling = str(uuid4())
        tables = await _seed_every_child_table(draft["id"], doc_draft)
        assert await _seed_every_child_table(sibling["id"], doc_sibling) == tables
        expected_tables = {"documents", "extraction_results", "model_calls", "lineage_records",
                           "verification_reports", "critic_findings", "critic_reports",
                           "cell_comments", "pending_batches", "due_diligence_questions",
                           "engine_outputs", "scenarios", "broker_questions", "broker_qa_pairs",
                           "memo_edits", "audit_log"}
        assert not (expected_tables - set(tables)), expected_tables - set(tables)
        before = await _rows_by_table(draft["id"], tables)
        assert all(n >= 1 for n in before.values()), before

        r = await client.delete(f"/deals/{draft['id']}/draft", headers=HDR_A)
        assert r.status_code == 204, r.text

    after = await _rows_by_table(draft["id"], tables)
    gone = {t: n for t, n in after.items() if t not in _DEAL_CASCADE_KEPT}
    assert all(n == 0 for n in gone.values()), {t: n for t, n in gone.items() if n}
    # The append-only trails stay (Postgres refuses their DELETE by trigger):
    # the seeded audit row plus the discard's own row; the memo edit.
    assert after["memo_edits"] == 1
    assert after["audit_log"] >= 2
    # The sibling is whole.
    sibling_after = await _rows_by_table(sibling["id"], tables)
    assert all(n >= 1 for n in sibling_after.values()), sibling_after
    assert await _count("deals", sibling["id"], column="id") == 1

    rows = await _audit_rows(draft["id"], "deal.draft_discarded")
    counts = rows[0]["metadata"]["cascade_counts"]
    for t in tables:
        if t not in _DEAL_CASCADE_KEPT:
            assert counts[t] == before[t], (t, counts.get(t), before[t])
    assert counts["deals"] == 1


@pytest.mark.asyncio
async def test_discard_refuses_while_a_document_is_in_flight() -> None:
    """A background parse / extract owns the row; deleting the deal under it
    would make the task fail (Postgres) or re-insert orphans (SQLite)."""
    from sqlalchemy import text

    from app.api import documents as documents_api
    from app.api.deals import _in_flight_statuses
    from app.database import get_session_factory

    assert _in_flight_statuses() == documents_api.DOC_PROCESSING_STATUSES  # the pipeline's own set
    async with _client() as client:
        draft = await _create_draft(client)
        _doc, storage_key = await _seed_document_with_object(draft["id"])
        factory = get_session_factory()
        for in_flight_status in sorted(_in_flight_statuses()):
            async with factory() as session:
                await session.execute(
                    text("UPDATE documents SET status = :s WHERE deal_id = :d AND tenant_id = :t"),
                    {"s": in_flight_status, "d": draft["id"], "t": TENANT_A},
                )
                await session.commit()
            r = await client.delete(f"/deals/{draft['id']}/draft", headers=HDR_A)
            assert r.status_code == 409, (in_flight_status, r.text)
            assert r.json()["detail"]["code"] == "documents_in_flight"
            assert r.json()["detail"]["count"] == 1
        # Nothing was deleted meanwhile.
        assert await _count("documents", draft["id"]) == 1
        assert _object_exists(storage_key)
        assert await _audit_rows(draft["id"], "deal.draft_discarded") == []

        async with factory() as session:
            await session.execute(
                text("UPDATE documents SET status = 'EXTRACTED' WHERE deal_id = :d AND tenant_id = :t"),
                {"d": draft["id"], "t": TENANT_A},
            )
            await session.commit()
        r = await client.delete(f"/deals/{draft['id']}/draft", headers=HDR_A)
        assert r.status_code == 204, r.text
    assert not _object_exists(storage_key)


@pytest.mark.asyncio
async def test_archive_on_a_draft_is_409_so_a_draft_is_never_archived() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.delete(f"/deals/{draft['id']}", headers=HDR_A)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_is_draft"
        r = await client.get(f"/deals/{draft['id']}", headers=HDR_A)
        assert r.json()["status"] == "Draft" and r.json()["lifecycle"] == "draft"
        # Archive still works on an underwriting.
        active = await _create(client, name="Active Hotel")
        r = await client.delete(f"/deals/{active['id']}", headers=HDR_A)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Archived"


@pytest.mark.asyncio
async def test_include_of_an_unknown_token_is_422() -> None:
    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.get(f"/deals/{draft['id']}", params={"include": "bogus"}, headers=HDR_A)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "unknown_include"
        assert r.json()["detail"]["include"] == ["bogus"]
        r = await client.get(
            f"/deals/{draft['id']}", params={"include": "draft_wizard,bogus"}, headers=HDR_A
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_draft_created_audit_actor_is_the_jwt_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """A.6.4 — the Projects card shows who started a draft, read from the
    ``deal.draft_created`` actor. Minted like ``tests/test_rbac.py``: a local
    RS256 key published through the JWKS cache, no Clerk call."""
    import base64
    import time

    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    from app.api.deals import _coerce_tenant_id
    from app.auth import clerk_jwt

    def _b64url_uint(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": "test-kid-drafts", "use": "sig", "alg": "RS256",
           "n": _b64url_uint(numbers.n), "e": _b64url_uint(numbers.e)}
    keys = {"test-kid-drafts": RSAAlgorithm.from_jwk(jwk)}
    monkeypatch.setattr(clerk_jwt, "_JWKS_KEYS", keys)
    monkeypatch.setattr(clerk_jwt, "_JWKS_FETCHED_AT", time.monotonic())
    monkeypatch.setattr(clerk_jwt, "_load_jwks", lambda *, force=False: keys)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    org_id = "org_DRAFTS"
    token = pyjwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 300, "sub": "user_draft_author",
         "org_id": org_id, "org_role": "org:member"},
        pem, algorithm="RS256", headers={"kid": "test-kid-drafts"},
    )
    tenant = str(_coerce_tenant_id(org_id))

    async with _client() as client:
        r = await client.post(
            "/deals", json={"name": "Authored Draft", "lifecycle": "draft", "draft_wizard": WIZARD},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]
        assert r.json()["tenant_id"] == tenant
        # A member can discard their own draft (no require_role on the route).
        r = await client.delete(f"/deals/{deal_id}/draft", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 204, r.text

    created = await _audit_rows(deal_id, "deal.draft_created", tenant=tenant)
    assert [row["actor_id"] for row in created] == ["user_draft_author"]
    discarded = await _audit_rows(deal_id, "deal.draft_discarded", tenant=tenant)
    assert [row["actor_id"] for row in discarded] == ["user_draft_author"]


@pytest.fixture
def _lifecycle_columns_absent(monkeypatch: pytest.MonkeyPatch):
    """Simulate a database the startup migration has not reached: the probe
    reports the three FON-79 columns absent (every other column as it is), so
    every read takes the ``'active'`` / NULL stand-in path."""
    from app.services import schema_probe

    real = schema_probe.table_has_column

    async def _probe(session, table, column, *, cache_key=None):
        if table == "deals" and column in {"lifecycle", "draft_wizard", "activated_at"}:
            return False
        return await real(session, table, column, cache_key=cache_key)

    monkeypatch.setattr(schema_probe, "table_has_column", _probe)
    yield


@pytest.mark.asyncio
async def test_un_migrated_schema_reads_every_deal_as_active_and_refuses_drafts(
    _lifecycle_columns_absent,
) -> None:
    """The fallback contract the market columns use: no 500s. Every row reads
    ``active`` / NULL, ``?lifecycle=draft`` is empty, the pipeline counts no
    drafts, and a draft CREATE is refused (503) rather than silently stored
    as an underwriting."""
    async with _client() as client:
        active = await _create(client, name="Active Hotel")
        assert active["lifecycle"] == "active"

        r = await client.post(
            "/deals", json={"name": "X", "lifecycle": "draft", "draft_wizard": WIZARD}, headers=HDR_A
        )
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "drafts_unavailable"
        r = await client.get("/deals", headers=HDR_A)
        assert [d["id"] for d in r.json()] == [active["id"]]  # nothing half-stored

        r = await client.get("/deals", params={"lifecycle": "draft"}, headers=HDR_A)
        assert r.status_code == 200 and r.json() == []
        r = await client.get("/deals", params={"lifecycle": "active"}, headers=HDR_A)
        assert [d["id"] for d in r.json()] == [active["id"]]
        r = await client.get(f"/deals/{active['id']}", params={"include": "draft_wizard"}, headers=HDR_A)
        assert r.status_code == 200, r.text
        assert r.json()["lifecycle"] == "active"
        assert r.json()["draft_wizard"] is None and r.json()["activated_at"] is None

        r = await client.patch(f"/deals/{active['id']}", json={"draft_wizard": WIZARD}, headers=HDR_A)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "deal_not_draft"
        r = await client.post(f"/deals/{active['id']}/activate", headers=HDR_A)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "deal_not_draft"
        r = await client.delete(f"/deals/{active['id']}/draft", headers=HDR_A)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "deal_not_draft"

        r = await client.get("/deals/pipeline", headers=HDR_A)
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["deal_count"] == 1
        assert r.json()["summary"]["draft_count"] == 0
        # The guard reads 'active' — an underwriting route still works.
        r = await client.post(
            f"/deals/{active['id']}/transition", json={"next_state": "VALIDATING"}, headers=HDR_A
        )
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_digest_kpi_block_carries_the_live_draft_count() -> None:
    """``pipeline_digest.build_digest_payload`` counts drafts the same way the
    pipeline endpoint does (live, beside the filtered rows — the snapshot it
    filters never contains a draft)."""
    from app.database import get_session_factory
    from app.services.pipeline_digest import build_digest_payload

    async with _client() as client:
        await _create_draft(client)
        await _create_draft(client, name="Second Draft")
        active = await _create(client, name="Active Hotel")
    schedule = {
        "id": str(uuid4()),
        "tenant_id": TENANT_A,
        "cadence": "daily",
        "include_kpi_summary": True,
        "include_recently_mutated": False,
        "include_deals_meeting_target": False,
        "include_full_table": False,
    }
    factory = get_session_factory()
    async with factory() as session:
        payload = await build_digest_payload(session, tenant_id=TENANT_A, schedule=schedule)
    assert payload.kpi_block is not None
    assert payload.kpi_block["deal_count"] == 1
    assert payload.kpi_block["draft_count"] == 2
    assert payload.deal_count == 1
    assert active["id"]  # the one counted deal


# ─────────────────────────── re-review additions ───────────────────────────


@pytest.mark.asyncio
async def test_memo_edit_on_a_draft_is_409_and_works_on_an_underwriting() -> None:
    """A draft has no memo to edit, so ``memo_edits`` — the one child table
    the cascade keeps — is empty for every draft by construction."""
    body = {"new_body": "Revised thesis.", "original_body": "Thesis.", "comment": "tighten"}
    async with _client() as client:
        draft = await _create_draft(client)
        r = await client.post(f"/deals/{draft['id']}/memo/thesis/edits", json=body, headers=HDR_A)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_is_draft"
        assert await _count("memo_edits", draft["id"]) == 0

        active = await _create(client, name="Active Hotel")
        r = await client.post(f"/deals/{active['id']}/memo/thesis/edits", json=body, headers=HDR_A)
        assert r.status_code == 201, r.text
        assert r.json()["new_body"] == "Revised thesis."
        # A stranger still gets 404, never the 409.
        r = await client.post(f"/deals/{draft['id']}/memo/thesis/edits", json=body, headers=HDR_B)
        assert r.status_code == 404, r.text
    assert await _count("memo_edits", active["id"]) == 1


@pytest.mark.asyncio
async def test_hard_delete_refuses_while_a_document_is_in_flight() -> None:
    """Same guard as discard: 409 ``documents_in_flight`` with the count, and
    nothing is deleted until the pipeline has let go of the row."""
    from sqlalchemy import text

    from app.database import get_session_factory

    async with _client() as client:
        active = await _create(client, name="Doomed Hotel")
        _doc, storage_key = await _seed_document_with_object(active["id"])
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("UPDATE documents SET status = 'PARSING' WHERE deal_id = :d AND tenant_id = :t"),
                {"d": active["id"], "t": TENANT_A},
            )
            await session.commit()
        r = await client.delete(f"/deals/{active['id']}/hard", headers=HDR_A)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "documents_in_flight"
        assert r.json()["detail"]["count"] == 1
        assert await _count("documents", active["id"]) == 1
        assert await _count("deals", active["id"], column="id") == 1
        assert _object_exists(storage_key)
        assert await _audit_rows(active["id"], "deal.hard_deleted") == []

        async with factory() as session:
            await session.execute(
                text("UPDATE documents SET status = 'EXTRACTED' WHERE deal_id = :d AND tenant_id = :t"),
                {"d": active["id"], "t": TENANT_A},
            )
            await session.commit()
        r = await client.delete(f"/deals/{active['id']}/hard", headers=HDR_A)
        assert r.status_code == 204, r.text
    assert await _count("deals", active["id"], column="id") == 0
    assert not _object_exists(storage_key)
