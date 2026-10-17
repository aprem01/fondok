"""POST /deals/{id}/duplicate — copy a deal for safe re-testing.

Sam's QA plan: "Since re-extraction resets reviewed fields, I'll use a
duplicate Kimpton deal or preserve the existing assumptions before testing."

Pins:
* the deal row, field_overrides (with notes), scenarios (re-keyed), documents
  (new ids, SAME storage key / content hash) and each document's LATEST
  extraction_results row (analyst reviews included) are copied;
* cell comments are copied ONLY when the body says
  ``include_cell_comments: true`` (Sam chooses per copy) — then marked
  ``copied_from_deal_id`` + "[Copied from …]" with keys re-pointed; by default
  they are listed under ``skipped`` and the copy has none; an unknown body key
  is a 422; another tenant's comment on the same deal id is never copied;
* the OM→STR positioning provenance (``positioning_source_detail``) on the
  copy names the COPIED document, never a document id the copy does not own;
* the "still processing" refusal gates on the pipeline's own status set
  (``documents.DOC_PROCESSING_STATUSES``), so a new stage cannot drift past it;
* engine_outputs and audit history are not;
* the original is byte-for-byte untouched;
* another tenant gets 404;
* deleting the copy (one document, then the whole deal) leaves the
  original's documents downloadable and never deletes a shared object —
  proven against the REAL ``LocalRawStore`` file through the real
  ``app.storage.get_store`` symbol — and the object IS removed once the last
  row referencing it goes.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "deal_duplicate.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("EVALS_MOCK", "true")

PDF_BYTES = b"%PDF-1.4\n% fondok duplicate-deal fixture\n%%EOF\n"
FILENAME = "Kimpton OM.pdf"


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _rows(session, sql: str, params: dict) -> list[dict]:
    from sqlalchemy import text

    return [dict(r._mapping) for r in (await session.execute(text(sql), params)).fetchall()]


async def _snapshot(deal_id: str, tenant: str) -> dict[str, list[dict]]:
    from app.database import get_session_factory

    p = {"deal": deal_id, "tenant": tenant}
    async with get_session_factory()() as s:
        return {
            "deals": await _rows(
                s, "SELECT * FROM deals WHERE id = :deal AND tenant_id = :tenant", p
            ),
            "documents": await _rows(
                s,
                "SELECT * FROM documents WHERE deal_id = :deal AND tenant_id = :tenant "
                "ORDER BY id",
                p,
            ),
            "extraction_results": await _rows(
                s,
                "SELECT * FROM extraction_results WHERE deal_id = :deal "
                "AND tenant_id = :tenant ORDER BY id",
                p,
            ),
            "scenarios": await _rows(
                s,
                "SELECT * FROM scenarios WHERE deal_id = :deal AND tenant_id = :tenant "
                "ORDER BY id",
                p,
            ),
            "cell_comments": await _rows(
                s,
                "SELECT * FROM cell_comments WHERE deal_id = :deal "
                "AND tenant_id = :tenant ORDER BY id",
                p,
            ),
            "engine_outputs": await _rows(
                s,
                "SELECT * FROM engine_outputs WHERE deal_id = :deal "
                "AND tenant_id = :tenant ORDER BY id",
                p,
            ),
        }


@pytest.fixture
async def seeded() -> dict[str, str]:
    """One fully-worked deal: a stored document with two extraction rows
    (the latest carries analyst reviews), a noted override, a second
    scenario, a cell comment and an engine run."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.storage import LocalRawStore, get_raw_store

    await run_startup_migrations()
    tenant = str(uuid4())
    headers = {"X-Tenant-Id": tenant}

    async with await _client() as c:
        r = await c.post(
            "/deals",
            json={
                "name": "Kimpton Dup Test",
                "city": "Miami Beach, FL",
                "keys": 132,
                "brand": "Kimpton",
                "purchase_price": 36_400_000,
                "target_irr": 0.18,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]

    store = get_raw_store(get_settings())
    assert isinstance(store, LocalRawStore)
    content_hash = uuid4().hex
    storage_key = await store.put(
        tenant_id=tenant,
        deal_id=deal_id,
        content_hash=content_hash,
        filename=FILENAME,
        bytes_=PDF_BYTES,
    )
    doc_id = str(uuid4())
    old_fields = [{"field_name": "rooms_revenue", "value": 1.0, "confidence": 0.5}]
    reviewed_fields = [
        {
            "field_name": "rooms_revenue",
            "value": 12_345_678.0,
            "confidence": 1.0,
            "reviewed": "edited",
        },
        {"field_name": "keys", "value": 132, "confidence": 1.0, "reviewed": "accepted"},
    ]
    overrides = {
        "revenue.adr_growth": {"value": 0.035, "note": "Per CBRE forecast"},
        "worksheet_layout": {"rows": [f"hist:{doc_id}::rooms_revenue"]},
    }
    async with get_session_factory()() as s:
        await s.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, content_hash, storage_key, size_bytes,
                    page_count, parser, user_provided_doc_type, fiscal_year,
                    doc_subtype, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, :fn, 'T12', 'EXTRACTED',
                    '2026-10-01 00:00:00', :hash, :key, :size,
                    3, 'pymupdf', 'PNL', 2024, 'T12_MONTHLY', :ed
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant,
                "fn": FILENAME,
                "hash": content_hash,
                "key": storage_key,
                "size": len(PDF_BYTES),
                "ed": json.dumps({"doc": doc_id}),
            },
        )
        for created, fields in (
            ("2026-10-01 00:00:01", old_fields),
            ("2026-10-02 00:00:00", reviewed_fields),
        ):
            await s.execute(
                text(
                    "INSERT INTO extraction_results (id, document_id, deal_id, "
                    "tenant_id, fields, confidence_report, agent_version, created_at) "
                    "VALUES (:id, :doc, :deal, :tenant, :f, :cr, 'v6', :ts)"
                ),
                {
                    "id": str(uuid4()),
                    "doc": doc_id,
                    "deal": deal_id,
                    "tenant": tenant,
                    "f": json.dumps(fields),
                    "cr": json.dumps({"overall": 1.0}),
                    "ts": created,
                },
            )
        # OM→STR positioning suggestion, as documents.py records it: the
        # detail JSON names the OM document that carried the brand.
        positioning_detail = {
            "document_id": doc_id,
            "document_filename": FILENAME,
            "page": 3,
            "doc_type": "OM",
            "om_brand": "Kimpton",
            "catalog_brand": "Kimpton Hotels & Restaurants",
            "catalog_family": "IHG",
            "str_scale": "Upper Upscale",
            "match": "exact",
        }
        await s.execute(
            text(
                "UPDATE deals SET field_overrides = :fo, state = 'READY', "
                "positioning = 'Upper Upscale', "
                "positioning_source = 'om_brand_str_scale', "
                "positioning_source_detail = :psd "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {
                "fo": json.dumps(overrides),
                "psd": json.dumps(positioning_detail),
                "id": deal_id,
                "tenant": tenant,
            },
        )
        run_id = str(uuid4())
        await s.execute(
            text(
                "UPDATE scenarios SET last_run_id = :run "
                "WHERE deal_id = :deal AND tenant_id = :tenant"
            ),
            {"run": run_id, "deal": deal_id, "tenant": tenant},
        )
        await s.execute(
            text(
                "INSERT INTO scenarios (id, deal_id, tenant_id, name, is_base, "
                "overrides, last_run_id, created_at, updated_at) VALUES "
                "(:id, :deal, :tenant, 'Downside', 0, :ov, :run, "
                "'2026-10-03 00:00:00', '2026-10-03 00:00:00')"
            ),
            {
                "id": str(uuid4()),
                "deal": deal_id,
                "tenant": tenant,
                "ov": json.dumps([{"key": "revenue.adr_growth", "value": 0.01}]),
                "run": run_id,
            },
        )
        await s.execute(
            text(
                "INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, "
                "engine_name, status, inputs, outputs, started_at, completed_at) "
                "VALUES (:id, :deal, :tenant, :run, 'returns', 'complete', '{}', "
                "'{\"levered_irr\": 0.2}', '2026-10-04 00:00:00', '2026-10-04 00:00:01')"
            ),
            {"id": str(uuid4()), "deal": deal_id, "tenant": tenant, "run": run_id},
        )
        await s.commit()

    async with await _client() as c:
        r = await c.post(
            f"/deals/{deal_id}/comments",
            json={
                "cell_key": f"hist:{doc_id}::rooms_revenue",
                "body": "Ties to the STR report",
                "cell_label": "Rooms Revenue · FY2024",
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), r.text

    return {
        "tenant": tenant,
        "deal_id": deal_id,
        "doc_id": doc_id,
        "storage_key": storage_key,
        "content_hash": content_hash,
    }


@pytest.mark.asyncio
async def test_duplicate_copies_everything_specified(seeded: dict[str, str]) -> None:
    tenant, src, doc_id = seeded["tenant"], seeded["deal_id"], seeded["doc_id"]
    before = await _snapshot(src, tenant)

    async with await _client() as c:
        r = await c.post(
            f"/deals/{src}/duplicate",
            json={"include_cell_comments": True},
            headers={"X-Tenant-Id": tenant},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    new = body["deal"]["id"]
    assert new != src
    assert body["source_deal_id"] == src
    today = datetime.now(UTC).date().isoformat()
    assert body["deal"]["name"] == f"Kimpton Dup Test (copy {today})"
    assert "engine_outputs" in body["skipped"] and "audit_log" in body["skipped"]
    assert "cell_comments" not in body["skipped"]
    assert body["include_cell_comments"] is True
    assert body["copied"] == {
        "deals": 1,
        "documents": 1,
        "extraction_results": 1,
        "scenarios": 2,
        "cell_comments": 1,
    }

    after_src = await _snapshot(src, tenant)
    assert after_src == before, "the original deal must be untouched"

    copy = await _snapshot(new, tenant)

    # deal row — every column equal except id / name / timestamps; overrides
    # keep their notes, document references re-pointed at the copied doc.
    (orig_deal,) = before["deals"]
    (copy_deal,) = copy["deals"]
    new_doc = copy["documents"][0]["id"]
    for col, val in orig_deal.items():
        if col in (
            "id", "name", "created_at", "updated_at", "field_overrides",
            "positioning_source_detail",
        ):
            continue
        assert copy_deal[col] == val, col
    fo = json.loads(copy_deal["field_overrides"])
    assert fo["revenue.adr_growth"] == {"value": 0.035, "note": "Per CBRE forecast"}
    assert fo["worksheet_layout"] == {"rows": [f"hist:{new_doc}::rooms_revenue"]}

    # OM→STR positioning provenance: the copy's "where did this tier come
    # from" names the COPIED OM, never a document id the copy does not own;
    # every other key of the detail is verbatim.
    assert copy_deal["positioning"] == "Upper Upscale"
    assert copy_deal["positioning_source"] == "om_brand_str_scale"
    src_detail = json.loads(orig_deal["positioning_source_detail"])
    copy_detail = json.loads(copy_deal["positioning_source_detail"])
    assert src_detail["document_id"] == doc_id
    assert copy_detail["document_id"] == new_doc
    assert {k: v for k, v in copy_detail.items() if k != "document_id"} == {
        k: v for k, v in src_detail.items() if k != "document_id"
    }
    psd = body["deal"]["positioning_source_detail"]
    assert psd["document_id"] == new_doc
    assert psd["document_filename"] == FILENAME and psd["page"] == 3

    # documents — new id, SAME stored object, metadata preserved.
    (od,) = before["documents"]
    (cd,) = copy["documents"]
    assert cd["id"] != od["id"]
    assert cd["deal_id"] == new
    assert cd["storage_key"] == od["storage_key"] == seeded["storage_key"]
    assert cd["content_hash"] == od["content_hash"]
    for col in (
        "filename", "doc_type", "status", "user_provided_doc_type",
        "fiscal_year", "doc_subtype", "size_bytes", "page_count", "parser",
        "uploaded_at",
    ):
        assert cd[col] == od[col], col
    assert json.loads(cd["extraction_data"]) == {"doc": new_doc}

    # extraction_results — only the LATEST row, with the analyst reviews.
    (ce,) = copy["extraction_results"]
    assert ce["document_id"] == new_doc
    fields = json.loads(ce["fields"])
    assert {f["field_name"]: f.get("reviewed") for f in fields} == {
        "rooms_revenue": "edited",
        "keys": "accepted",
    }
    assert fields[0]["value"] == 12_345_678.0
    latest_src = max(before["extraction_results"], key=lambda e: e["created_at"])
    assert ce["created_at"] == latest_src["created_at"]
    assert ce["id"] not in {e["id"] for e in before["extraction_results"]}

    # scenarios — re-keyed, no run link.
    assert sorted(s["name"] for s in copy["scenarios"]) == sorted(
        s["name"] for s in before["scenarios"]
    )
    assert not {s["id"] for s in copy["scenarios"]} & {s["id"] for s in before["scenarios"]}
    assert all(s["last_run_id"] is None for s in copy["scenarios"])
    down = next(s for s in copy["scenarios"] if s["name"] == "Downside")
    assert json.loads(down["overrides"]) == [{"key": "revenue.adr_growth", "value": 0.01}]
    assert sum(1 for s in copy["scenarios"] if s["is_base"]) == 1

    # cell comments — marked as copied (column + body prefix), key re-pointed.
    (cc,) = copy["cell_comments"]
    assert cc["body"] == "[Copied from Kimpton Dup Test] Ties to the STR report"
    assert cc["cell_key"] == f"hist:{new_doc}::rooms_revenue"
    assert cc["author_id"] == before["cell_comments"][0]["author_id"]
    assert cc["copied_from_deal_id"] == src
    assert before["cell_comments"][0]["copied_from_deal_id"] is None

    # NOT copied: engine outputs (model must be re-run) and audit history.
    assert before["engine_outputs"], "fixture should have a run on the original"
    assert copy["engine_outputs"] == []
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        audit = await _rows(
            s,
            "SELECT action, payload FROM audit_log WHERE deal_id = :d AND tenant_id = :t",
            {"d": new, "t": tenant},
        )
    assert [a["action"] for a in audit] == ["deal.duplicated"]
    meta = json.loads(audit[0]["payload"])["metadata"]
    assert meta["source_deal_id"] == src
    assert meta["document_id_map"] == {doc_id: new_doc}
    assert meta["include_cell_comments"] is True
    assert "cell_comments" not in meta["skipped"]

    # Storage reads on the copy resolve the original object.
    async with await _client() as c:
        dl = await c.get(
            f"/deals/{new}/documents/{new_doc}/download",
            headers={"X-Tenant-Id": tenant},
        )
        assert dl.status_code == 200, dl.text
        assert dl.content == PDF_BYTES
        listed = await c.get(f"/deals/{new}/documents", headers={"X-Tenant-Id": tenant})
        assert listed.status_code == 200
        assert [d["id"] for d in listed.json()] == [new_doc]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, {}, {"include_cell_comments": False}])
async def test_duplicate_skips_cell_comments_unless_asked(
    seeded: dict[str, str], body: dict | None
) -> None:
    """Sam chooses per copy. An empty POST (the pre-option client), an empty
    body and an explicit ``false`` all mean: no comments on the copy, and the
    response + audit entry say so under ``skipped``."""
    tenant, src = seeded["tenant"], seeded["deal_id"]
    before = await _snapshot(src, tenant)
    assert len(before["cell_comments"]) == 1

    async with await _client() as c:
        kwargs = {} if body is None else {"json": body}
        r = await c.post(f"/deals/{src}/duplicate", headers={"X-Tenant-Id": tenant}, **kwargs)
    assert r.status_code == 201, r.text
    res = r.json()
    new = res["deal"]["id"]
    assert res["include_cell_comments"] is False
    assert "cell_comments" not in res["copied"]
    assert "cell_comments" in res["skipped"]
    # Everything else still travels.
    assert res["copied"] == {
        "deals": 1,
        "documents": 1,
        "extraction_results": 1,
        "scenarios": 2,
    }

    copy = await _snapshot(new, tenant)
    assert copy["cell_comments"] == []
    assert len(copy["documents"]) == 1 and len(copy["scenarios"]) == 2
    assert await _snapshot(src, tenant) == before, "the original deal must be untouched"

    from app.database import get_session_factory

    async with get_session_factory()() as s:
        audit = await _rows(
            s,
            "SELECT action, payload FROM audit_log WHERE deal_id = :d AND tenant_id = :t",
            {"d": new, "t": tenant},
        )
    meta = json.loads(audit[0]["payload"])["metadata"]
    assert meta["include_cell_comments"] is False
    assert "cell_comments" in meta["skipped"]

    # The comments API on the copy is empty; on the original, the comment is
    # not marked as copied.
    async with await _client() as c:
        h = {"X-Tenant-Id": tenant}
        assert (await c.get(f"/deals/{new}/comments", headers=h)).json() == []
        (orig,) = (await c.get(f"/deals/{src}/comments", headers=h)).json()
        assert orig["copied_from_deal_id"] is None


@pytest.mark.asyncio
async def test_duplicate_rejects_unknown_body_keys(seeded: dict[str, str]) -> None:
    tenant, src = seeded["tenant"], seeded["deal_id"]
    async with await _client() as c:
        r = await c.post(
            f"/deals/{src}/duplicate",
            json={"include_comments": True},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 422, r.text
        r = await c.post(
            f"/deals/{src}/duplicate",
            json={"include_cell_comments": "yes please"},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 422, r.text
    snap = await _snapshot(src, tenant)
    assert len(snap["deals"]) == 1


@pytest.mark.asyncio
async def test_copied_comments_are_tenant_scoped_and_surface_their_source(
    seeded: dict[str, str],
) -> None:
    """A ``cell_comments`` row that names the same deal id but another tenant
    (defence in depth — the deal lookup already refuses foreign callers) is
    never copied, and the copy's comments API reports the source deal."""
    from sqlalchemy import text

    from app.database import get_session_factory

    tenant, src, doc_id = seeded["tenant"], seeded["deal_id"], seeded["doc_id"]
    other_tenant = str(uuid4())
    async with get_session_factory()() as s:
        await s.execute(
            text(
                "INSERT INTO cell_comments (id, tenant_id, deal_id, cell_key, body, "
                "created_at) VALUES (:id, :t, :d, :k, 'foreign tenant row', "
                "'2026-10-01 00:00:00')"
            ),
            {
                "id": str(uuid4()),
                "t": other_tenant,
                "d": src,
                "k": f"hist:{doc_id}::rooms_revenue",
            },
        )
        await s.commit()

    async with await _client() as c:
        h = {"X-Tenant-Id": tenant}
        r = await c.post(
            f"/deals/{src}/duplicate", json={"include_cell_comments": True}, headers=h
        )
        assert r.status_code == 201, r.text
        assert r.json()["copied"]["cell_comments"] == 1
        new = r.json()["deal"]["id"]
        listed = (await c.get(f"/deals/{new}/comments", headers=h)).json()
    assert len(listed) == 1
    (cm,) = listed
    assert cm["copied_from_deal_id"] == src
    assert cm["body"].startswith("[Copied from Kimpton Dup Test] ")
    assert "foreign tenant row" not in cm["body"]

    copy = await _snapshot(new, tenant)
    assert [c["body"] for c in copy["cell_comments"]] == [cm["body"]]
    async with get_session_factory()() as s:
        foreign = await _rows(
            s,
            "SELECT * FROM cell_comments WHERE deal_id = :d AND tenant_id = :t",
            {"d": new, "t": other_tenant},
        )
    assert foreign == []


@pytest.mark.asyncio
async def test_duplicate_is_404_for_another_tenant(seeded: dict[str, str]) -> None:
    async with await _client() as c:
        r = await c.post(
            f"/deals/{seeded['deal_id']}/duplicate",
            headers={"X-Tenant-Id": str(uuid4())},
        )
    assert r.status_code == 404
    snap = await _snapshot(seeded["deal_id"], seeded["tenant"])
    assert len(snap["deals"]) == 1


@pytest.mark.asyncio
async def test_duplicate_refuses_while_a_document_is_processing(
    seeded: dict[str, str],
) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    async with get_session_factory()() as s:
        await s.execute(
            text(
                "UPDATE documents SET status = 'EXTRACTING' "
                "WHERE id = :id AND tenant_id = :t"
            ),
            {"id": seeded["doc_id"], "t": seeded["tenant"]},
        )
        await s.commit()
    async with await _client() as c:
        r = await c.post(
            f"/deals/{seeded['deal_id']}/duplicate",
            headers={"X-Tenant-Id": seeded["tenant"]},
        )
    assert r.status_code == 409
    assert "still being processed" in r.json()["detail"]


def test_in_flight_statuses_are_the_pipelines_own() -> None:
    """The 409 guard gates on the SAME set the pipeline and the watchdog use,
    so a stage added to ``DOC_PROCESSING_STATUSES`` can never slip past it."""
    from app.api import deal_duplicate, documents

    assert deal_duplicate._in_flight_statuses() == documents.DOC_PROCESSING_STATUSES
    assert {"PARSING", "CLASSIFYING", "EXTRACTING"} <= deal_duplicate._in_flight_statuses()


@pytest.mark.asyncio
async def test_duplicate_refuses_on_a_stage_added_to_the_pipeline(
    seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drift test: a stage registered on the pipeline's status set is honoured
    by the duplicate guard with no second edit."""
    from sqlalchemy import text

    from app.api import documents
    from app.database import get_session_factory

    monkeypatch.setattr(
        documents,
        "DOC_PROCESSING_STATUSES",
        documents.DOC_PROCESSING_STATUSES | {"VERIFYING"},
    )
    async with get_session_factory()() as s:
        await s.execute(
            text(
                "UPDATE documents SET status = 'VERIFYING' "
                "WHERE id = :id AND tenant_id = :t"
            ),
            {"id": seeded["doc_id"], "t": seeded["tenant"]},
        )
        await s.commit()
    async with await _client() as c:
        r = await c.post(
            f"/deals/{seeded['deal_id']}/duplicate",
            headers={"X-Tenant-Id": seeded["tenant"]},
        )
    assert r.status_code == 409
    assert "still being processed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_deleting_the_copy_keeps_the_original_downloadable(
    seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.storage as storage_pkg
    from app.storage import LocalRawStore
    from app.storage.raw_store import _file_uri_to_path

    # The per-document delete resolves ``app.storage.get_store`` lazily and
    # runs ``store.delete(key)`` in a thread. Spy on the REAL symbol (default
    # ``raising=True`` — a missing export fails here, not silently in prod)
    # and let the real LocalRawStore do the work so the file on disk is the
    # evidence.
    real_get_store = storage_pkg.get_store
    deleted: list[str] = []

    def _spying_get_store(settings=None):
        store = real_get_store(settings)
        assert isinstance(store, LocalRawStore)
        real_delete = store.delete

        def _delete(key: str) -> bool:
            deleted.append(key)
            return real_delete(key)

        monkeypatch.setattr(store, "delete", _delete)
        return store

    monkeypatch.setattr(storage_pkg, "get_store", _spying_get_store)

    blob = _file_uri_to_path(seeded["storage_key"])
    assert blob.exists()

    tenant, src, doc_id = seeded["tenant"], seeded["deal_id"], seeded["doc_id"]
    h = {"X-Tenant-Id": tenant}
    async with await _client() as c:
        r = await c.post(f"/deals/{src}/duplicate", headers=h)
        assert r.status_code == 201, r.text
        new = r.json()["deal"]["id"]
        new_doc = (await c.get(f"/deals/{new}/documents", headers=h)).json()[0]["id"]

        # Delete the copy's document: the shared object must survive.
        d = await c.delete(f"/deals/{new}/documents/{new_doc}", headers=h)
        assert d.status_code == 204, d.text
        assert deleted == [], "a shared raw object was scheduled for deletion"
        assert blob.exists(), "a shared raw object was removed from disk"

        # Hard-delete the whole copy.
        d = await c.delete(f"/deals/{new}/hard", headers=h)
        assert d.status_code == 204, d.text
        assert (await c.get(f"/deals/{new}", headers=h)).status_code == 404
        assert blob.exists()

        dl = await c.get(f"/deals/{src}/documents/{doc_id}/download", headers=h)
        assert dl.status_code == 200, dl.text
        assert dl.content == PDF_BYTES
        assert (await c.post(f"/deals/{src}/duplicate", headers={"X-Tenant-Id": str(uuid4())})).status_code == 404

    assert blob.exists()

    # Once nothing else references the key, deleting the original document
    # really removes the object (the guard is not a blanket skip).
    async with await _client() as c:
        d = await c.delete(f"/deals/{src}/documents/{doc_id}", headers=h)
        assert d.status_code == 204, d.text
    assert deleted == [seeded["storage_key"]]
    assert not blob.exists(), "the last reference went but the object stayed"


def test_duplicate_route_is_tenant_guarded() -> None:
    from fastapi.routing import APIRoute

    from app.main import app

    (route,) = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == "/deals/{deal_id}/duplicate"
    ]
    assert route.methods == {"POST"}
    names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
    assert "get_tenant_id" in names
