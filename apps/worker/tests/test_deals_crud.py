"""DB-backed CRUD tests for /deals.

Verifies that the deal endpoints persist to the live SQLite DB across
requests, write audit_log rows on every mutation, and roll up the
status pill from the documents table.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deals-crud.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-deals-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so the DB is deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001 — table may not exist yet
                pass
        await session.commit()
    yield


def _build_sample_pdf() -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    _, height = LETTER
    text = c.beginText(72, height - 72)
    text.setFont("Helvetica-Bold", 14)
    text.textLine("Status Roll-Up Sample PDF")
    text.setFont("Helvetica", 10)
    text.textLine("Net Operating Income: $1,000,000")
    c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="session")
def sample_pdf_bytes() -> bytes:
    return _build_sample_pdf()


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_create_deal_persists() -> None:
    """POST /deals then GET /deals/{id} round-trips every field."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={
                "name": "Persisted Hotel",
                "city": "Miami, FL",
                "keys": 150,
                "service": "Select Service",
                "deal_stage": "Teaser",
                "return_profile": "Value Add",
                "brand": "Hilton Garden Inn",
                "positioning": "Upscale",
                "purchase_price": 25000000,
            },
        )
        assert r.status_code == 201, r.text
        created = r.json()
        deal_id = created["id"]
        UUID(deal_id)
        assert created["name"] == "Persisted Hotel"
        assert created["status"] == "Draft"
        assert created["ai_confidence"] == 0.0

        # Fresh GET — proves it actually landed in the DB.
        r = await client.get(f"/deals/{deal_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == deal_id
        assert body["name"] == "Persisted Hotel"
        assert body["city"] == "Miami, FL"
        assert body["keys"] == 150
        assert body["service"] == "Select Service"
        assert body["deal_stage"] == "Teaser"
        assert body["return_profile"] == "Value Add"
        assert body["brand"] == "Hilton Garden Inn"
        assert body["positioning"] == "Upscale"
        assert float(body["purchase_price"]) == 25000000.0

        # 404 on unknown id.
        r = await client.get("/deals/00000000-0000-0000-0000-000000000999")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_deals_returns_persisted() -> None:
    """POST 3 deals — GET /deals returns 3, newest first."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        names = ["Hotel Alpha", "Hotel Beta", "Hotel Gamma"]
        for n in names:
            r = await client.post("/deals", json={"name": n, "city": "Austin"})
            assert r.status_code == 201, r.text
            # Tiny gap so created_at orders deterministically on SQLite.
            await asyncio.sleep(0.01)

        r = await client.get("/deals")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        listed = [d["name"] for d in body]
        # Newest first → Gamma, Beta, Alpha (assumes the autouse fixture
        # purged any leftover deals from prior tests).
        assert listed[:3] == list(reversed(names))


@pytest.mark.asyncio
async def test_patch_deal_updates_and_logs_audit() -> None:
    """PATCH mutates the row, returns the updated record, writes audit_log."""
    from sqlalchemy import text

    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "PatchTarget", "city": "NYC", "keys": 100},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "name": "Patched Hotel",
                "deal_stage": "LOI",
                "ai_confidence": 0.75,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Patched Hotel"
        assert body["deal_stage"] == "LOI"
        assert body["ai_confidence"] == 0.75
        # Untouched fields preserved.
        assert body["city"] == "NYC"
        assert body["keys"] == 100

        # 404 on missing.
        r = await client.patch(
            "/deals/00000000-0000-0000-0000-000000000999",
            json={"name": "ghost"},
        )
        assert r.status_code == 404

    # Audit log: should have a 'deal.created' and 'deal.updated' row.
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT action, payload FROM audit_log
                 WHERE resource_id = :rid
                 ORDER BY created_at ASC
                """
            ),
            {"rid": deal_id},
        )
        actions = [r._mapping["action"] for r in rows.fetchall()]
    assert "deal.created" in actions
    assert "deal.updated" in actions


@pytest.mark.asyncio
async def test_status_aggregates_from_documents(
    sample_pdf_bytes: bytes,
) -> None:
    """Status pill rolls from 'draft' → 'extracting' → 'ready' as docs progress."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    os.environ["EVALS_MOCK"] = "true"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deals", json={"name": "StatusDeal"})
        deal_id = r.json()["id"]

        # No docs → draft.
        r = await client.get(f"/deals/{deal_id}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["last_event"] == "draft"
        assert body["docs_total"] == 0

        # Upload 2 docs. The upload pipeline dedupes by
        # (deal_id, content_hash) — so we must vary the bytes per file
        # or the second upload silently collapses into the first and
        # docs_total never gets above 1.
        for fname in ("doc1.pdf", "doc2.pdf"):
            # Append a per-file tail comment so the content_hash differs
            # without breaking the PDF structure (PDF readers ignore
            # trailing bytes after %%EOF).
            varied = sample_pdf_bytes + f"\n%fondok-test-{fname}\n".encode()
            r = await client.post(
                f"/deals/{deal_id}/documents/upload",
                files={"files": (fname, varied, "application/pdf")},
            )
            assert r.status_code == 201, r.text

        # Right after upload the row sits in the PARSING → UPLOADED →
        # CLASSIFYING → EXTRACTING → EXTRACTED chain. Under EVALS_MOCK
        # every step is sub-millisecond so the background task can
        # finish before the next API call lands; in production the
        # LLM calls keep us in 'extracting' for several seconds. Both
        # are valid intermediate states.
        r = await client.get(f"/deals/{deal_id}/status")
        body = r.json()
        assert body["docs_total"] == 2
        assert body["last_event"] in ("extracting", "ready")

        # Drive both docs through extraction.
        r = await client.get(f"/deals/{deal_id}/documents")
        for d in r.json():
            er = await client.post(
                f"/deals/{deal_id}/documents/{d['id']}/extract"
            )
            assert er.status_code == 202

        # Poll until both EXTRACTED.
        for _ in range(40):
            r = await client.get(f"/deals/{deal_id}/status")
            body = r.json()
            if body["docs_extracted"] == 2:
                break
            await asyncio.sleep(0.1)

        assert body["docs_extracted"] == 2
        assert body["last_event"] == "ready"
        # Confidence rollup picks up extraction confidence after the
        # citation verifier has run. The mock extractor reports each
        # field at 0.9, but the verifier re-reads the cited numbers
        # against the parsed PDF text — and the sample PDF doesn't
        # contain "$1,234,567" or "74%", so both fields get demoted to
        # 0.50 (_VERIFY_DEMOTE_MISMATCH). The status rollup averages
        # the verified confidence, so we expect ~0.5 here. A higher
        # value would mean verification stopped running; a lower one
        # would mean the field set shrunk. Both are real regressions.
        assert body["ai_confidence"] is not None
        assert body["ai_confidence"] == pytest.approx(0.5, abs=0.05)


@pytest.mark.asyncio
async def test_archive_does_not_delete() -> None:
    """DELETE /deals/{id} flips status to 'Archived' but keeps the row."""
    from sqlalchemy import text

    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deals", json={"name": "ToArchive"})
        deal_id = r.json()["id"]

        r = await client.delete(f"/deals/{deal_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "Archived"

        # Still readable via GET.
        r = await client.get(f"/deals/{deal_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "Archived"

        # 404 archive on missing.
        r = await client.delete("/deals/00000000-0000-0000-0000-000000000999")
        assert r.status_code == 404

    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text("SELECT status FROM deals WHERE id = :id"),
            {"id": deal_id},
        )
        row = rows.first()
        assert row is not None, "deal row should still be in the DB"
        assert row._mapping["status"] == "Archived"

        # And an audit row exists for the archive.
        audit_rows = await session.execute(
            text(
                "SELECT action FROM audit_log WHERE resource_id = :rid "
                "ORDER BY created_at ASC"
            ),
            {"rid": deal_id},
        )
        actions = [r._mapping["action"] for r in audit_rows.fetchall()]
    assert "deal.archived" in actions


@pytest.mark.asyncio
async def test_unchanged_field_overrides_emit_no_override_audit_row() -> None:
    """FON-63 — a PATCH whose ``field_overrides`` blob is unchanged writes no
    ``override.set`` row, so the Activity Feed stops filling with phantom
    "exit_cap_rate: 0.07 → 0.07" entries. The legacy ``deal.updated`` trail is
    still written, and a real change still emits the override row."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deals", json={"name": "No-Op Save"})
        deal_id = r.json()["id"]

        # 1 — the first override IS a change. FON-74: an engine input, so it
        # carries the analyst's justification.
        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "exit_cap_rate": {"value": 0.075, "note": "comp set"}
                }
            },
        )
        assert r.status_code == 200, r.text

        # 2 — re-saving the same value (Sam's stray Save) changes nothing.
        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "exit_cap_rate": {"value": 0.075, "note": "comp set"}
                }
            },
        )
        assert r.status_code == 200, r.text

        # 3 — and a real edit is still audited.
        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "exit_cap_rate": {"value": 0.08, "note": "broker guidance"}
                }
            },
        )
        assert r.status_code == 200, r.text

    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT action FROM audit_log WHERE resource_id = :rid "
                "ORDER BY created_at ASC"
            ),
            {"rid": deal_id},
        )
        actions = [r._mapping["action"] for r in rows.fetchall()]

    # Two real changes → two override rows; the no-op PATCH added none.
    assert actions.count("override.set") == 2
    # Every PATCH still leaves the legacy trail.
    assert actions.count("deal.updated") == 3


# ─────────────────────────────────────────────────────────────────────────
# FON-74 — the analyst-justification gate on PATCH /deals/{id}
#
# The founder's June 2026 rule: an analyst who overrides a value must attach a
# justification. It cannot live only in the browser — an API that can be talked
# into an unjustified override is an API whose audit trail cannot be trusted.
#
# The rule: a note is required IFF the key routes into ENGINE INPUT, and only
# for keys this PATCH actually CHANGES.
# ─────────────────────────────────────────────────────────────────────────


def _patch_client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_engine_input_override_without_a_note_is_422() -> None:
    """A changed engine input with no justification is refused, by name."""
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Gate"})).json()["id"]

        r = await client.patch(
            f"/deals/{deal_id}", json={"field_overrides": {"exit_cap_rate": 0.075}}
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "override_note_required"
        assert detail["keys"] == ["exit_cap_rate"]

        # A structured entry whose note is absent / blank is the same refusal —
        # whitespace is not a justification.
        for entry in ({"value": 0.075}, {"value": 0.075, "note": "   "}):
            r = await client.patch(
                f"/deals/{deal_id}", json={"field_overrides": {"exit_cap_rate": entry}}
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "override_note_required"

        # Nothing was written: the refusal is not a partial save.
        got = (await client.get(f"/deals/{deal_id}")).json()["field_overrides"]
        assert got == {}


@pytest.mark.asyncio
async def test_engine_input_override_with_a_note_is_200_and_stores_it() -> None:
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Gate OK"})).json()["id"]

        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "exit_cap_rate": {"value": 0.075, "note": "Comp set, Q3 trades"}
                }
            },
        )
        assert r.status_code == 200, r.text
        stored = r.json()["field_overrides"]["exit_cap_rate"]
        assert stored == {"value": 0.075, "note": "Comp set, Q3 trades"}


@pytest.mark.asyncio
async def test_every_changed_engine_key_is_named_in_the_refusal() -> None:
    """The analyst is told WHICH keys need a reason, not just that one does."""
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Multi"})).json()["id"]
        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "gp_equity_pct": 0.12,
                    "lp_equity_pct": 0.88,
                    # ...and one that is fine, which must NOT be named.
                    "stabilization_year": {"value": 3},
                }
            },
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["keys"] == ["gp_equity_pct", "lp_equity_pct"]


@pytest.mark.asyncio
async def test_worksheet_layout_alone_is_200_with_no_note() -> None:
    """The one key in the worker's ``_OVERRIDE_NON_ENGINE_KEYS``.

    Presentation only — the worksheet's numbers come from the engines however
    its rows are arranged. Without this exclusion every drag-to-reorder would
    422, which is exactly the kind of collateral damage a rule like this dies of.
    """
    layout = {"rows": [{"id": "rooms", "label": "Rooms Revenue"}]}
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Layout"})).json()["id"]
        r = await client.patch(
            f"/deals/{deal_id}", json={"field_overrides": {"worksheet_layout": layout}}
        )
        assert r.status_code == 200, r.text
        assert r.json()["field_overrides"]["worksheet_layout"] == layout


@pytest.mark.asyncio
async def test_keys_that_move_no_number_need_no_note() -> None:
    """Exempt by name, each for a stated reason (see ``_NOTE_EXEMPT_KEYS``)."""
    exempt = {
        "stabilization_year": 3,
        "property_overview.name": "The Angler's",
        "debt.completion_guarantee": "in_place",
        "partnership.waterfall.tier_count": 4,
        "partnership.waterfall.2.removed": True,
        "memo_thesis": "A long prose thesis the analyst wrote.",
    }
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Exempt"})).json()["id"]
        r = await client.patch(f"/deals/{deal_id}", json={"field_overrides": exempt})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_shadow_override_is_not_a_change_and_needs_no_note() -> None:
    """FON-63 — re-saving the value already stored is not an override.

    This is what lets the gate ship against live deals: a legacy bare scalar
    sitting on a deal keeps round-tripping, note or no note, until something
    actually changes it.
    """
    from sqlalchemy import text as sql_text

    from app.database import get_session_factory

    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Shadow"})).json()["id"]

        # Seed a LEGACY bare scalar straight into the column — a deal that
        # predates the rule, exactly as production has them.
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                sql_text("UPDATE deals SET field_overrides = :ov WHERE id = :id"),
                {"id": deal_id, "ov": json.dumps({"exit_cap_rate": 0.07})},
            )
            await session.commit()

        # Re-sending it unchanged is not a change, so it is not refused.
        r = await client.patch(
            f"/deals/{deal_id}", json={"field_overrides": {"exit_cap_rate": 0.07}}
        )
        assert r.status_code == 200, r.text

        # The same value spelled as a string still reads as unchanged.
        r = await client.patch(
            f"/deals/{deal_id}", json={"field_overrides": {"exit_cap_rate": "0.07"}}
        )
        assert r.status_code == 200, r.text

        # Changing it now DOES need a reason.
        r = await client.patch(
            f"/deals/{deal_id}", json={"field_overrides": {"exit_cap_rate": 0.08}}
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_clearing_an_override_is_a_revert_not_an_override() -> None:
    """Dropping the key is a return to source — there is nothing to justify."""
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Revert"})).json()["id"]
        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "exit_cap_rate": {"value": 0.075, "note": "comp set"}
                }
            },
        )
        assert r.status_code == 200, r.text

        r = await client.patch(f"/deals/{deal_id}", json={"field_overrides": {}})
        assert r.status_code == 200, r.text
        assert r.json()["field_overrides"] == {}


@pytest.mark.asyncio
async def test_a_patch_without_field_overrides_is_untouched_by_the_gate() -> None:
    async with _patch_client() as client:
        deal_id = (await client.post("/deals", json={"name": "Rename"})).json()["id"]
        r = await client.patch(f"/deals/{deal_id}", json={"name": "Project Pelican"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Project Pelican"
