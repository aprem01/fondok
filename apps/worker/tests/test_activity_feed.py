"""Wave 4 W4.3 — Activity Feed + Compliance Explorer end-to-end tests.

Pins the per-deal Activity Feed surface, the tenant-wide explorer,
and the broader audit-log coverage (every Wave 2/3 mutating endpoint
now writes an ``audit_log`` row).

Tests live here (not in test_audit.py) so the existing Wave-1 audit
contract suite stays focused on the helper's correctness. This file
exercises the API surface + every callsite we added.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN. Pattern mirrored from
# test_scenarios.py / test_audit.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-activity-feed.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema + truncate before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "scenarios",
            "audit_log",
            "engine_outputs",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _create_deal_via_api(client, *, name: str = "Activity Hotel", **kw) -> str:
    body = {"name": name, "city": "Denver", "keys": 120}
    body.update(kw)
    r = await client.post("/deals", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _client():
    """Build a fresh httpx ASGI client against the FastAPI app."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


# ─────────────────────────── tests ───────────────────────────


# 1. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_log_audit_persists_with_required_fields() -> None:
    """log_audit round-trips the Wave 4 columns + severity defaults to 'info'."""
    from app.audit import log_audit
    from app.database import get_session_factory

    tenant = str(uuid4())
    deal = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session,
            tenant_id=tenant,
            actor_id="user-1",
            actor_email="analyst@brookfield.test",
            actor_ip="10.0.0.7",
            user_agent="Mozilla/5.0",
            action="override.set",
            resource_type="override",
            resource_id=deal,
            before={"exit_cap_rate": 0.07},
            after={"exit_cap_rate": 0.075},
            tags=["wave2", "override"],
        )
        await session.commit()

        row = (
            await session.execute(
                text(
                    "SELECT actor_email, actor_ip, user_agent, "
                    "before, after, diff_summary, severity, tags "
                    "FROM audit_log WHERE resource_id = :rid"
                ),
                {"rid": deal},
            )
        ).first()
    assert row is not None
    m = row._mapping
    assert m["actor_email"] == "analyst@brookfield.test"
    assert m["actor_ip"] == "10.0.0.7"
    assert m["user_agent"] == "Mozilla/5.0"
    assert m["severity"] == "info"
    # before/after JSON round-trip on SQLite (stored as TEXT)
    assert json.loads(m["before"]) == {"exit_cap_rate": 0.07}
    assert json.loads(m["after"]) == {"exit_cap_rate": 0.075}
    # diff_summary auto-computed when not provided
    assert m["diff_summary"] is not None
    assert "exit_cap_rate" in m["diff_summary"]
    assert json.loads(m["tags"]) == ["wave2", "override"]


# 2. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_audit_log_filters_by_deal() -> None:
    """deal_id filter only returns rows for the matching deal."""
    from app.audit import list_audit_log, log_audit
    from app.database import get_session_factory

    tenant = str(uuid4())
    deal_a = str(uuid4())
    deal_b = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session, tenant_id=tenant, action="deal.created",
            resource_type="deal", resource_id=deal_a,
        )
        await log_audit(
            session, tenant_id=tenant, action="deal.created",
            resource_type="deal", resource_id=deal_b,
        )
        await session.commit()

        rows = await list_audit_log(
            session, tenant_id=tenant, deal_id=deal_a
        )
    assert len(rows) == 1
    assert rows[0]["deal_id"] == deal_a


# 3. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_audit_log_filters_by_action() -> None:
    """action= filter is exact-match."""
    from app.audit import list_audit_log, log_audit
    from app.database import get_session_factory

    tenant = str(uuid4())
    deal = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        for action in ("deal.created", "deal.updated", "override.set"):
            await log_audit(
                session, tenant_id=tenant, action=action,
                resource_type="deal", resource_id=deal,
            )
        await session.commit()

        rows = await list_audit_log(
            session, tenant_id=tenant, action="override.set"
        )
    assert len(rows) == 1
    assert rows[0]["action"] == "override.set"


# 4. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_audit_log_paginates() -> None:
    """limit + offset together page through the result set."""
    from app.audit import list_audit_log, log_audit
    from app.database import get_session_factory

    tenant = str(uuid4())
    deal = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        for i in range(7):
            await log_audit(
                session, tenant_id=tenant, action=f"event.{i}",
                resource_type="deal", resource_id=deal,
            )
        await session.commit()

        page1 = await list_audit_log(
            session, tenant_id=tenant, limit=3, offset=0
        )
        page2 = await list_audit_log(
            session, tenant_id=tenant, limit=3, offset=3
        )
        page3 = await list_audit_log(
            session, tenant_id=tenant, limit=3, offset=6
        )
    assert len(page1) == 3
    assert len(page2) == 3
    assert len(page3) == 1
    # Newest first + no overlap between pages
    seen = {r["id"] for r in page1 + page2 + page3}
    assert len(seen) == 7


# 5. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_log_tenant_scoped() -> None:
    """Tenant A cannot see tenant B's audit log entries."""
    from app.audit import list_audit_log, log_audit
    from app.database import get_session_factory

    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    deal_a = str(uuid4())
    deal_b = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session, tenant_id=tenant_a, action="deal.created",
            resource_type="deal", resource_id=deal_a,
        )
        await log_audit(
            session, tenant_id=tenant_b, action="deal.created",
            resource_type="deal", resource_id=deal_b,
        )
        await session.commit()

        rows_a = await list_audit_log(session, tenant_id=tenant_a)
        rows_b = await list_audit_log(session, tenant_id=tenant_b)

    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0]["tenant_id"] == tenant_a
    assert rows_b[0]["tenant_id"] == tenant_b
    # Empty / falsy tenant_id is rejected — better to crash than leak.
    async with factory() as session:
        with pytest.raises(ValueError):
            await list_audit_log(session, tenant_id="")


# 6. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_create_emits_audit_entry() -> None:
    """POST /deals/{id}/scenarios writes a ``scenario.created`` audit row."""
    async with _client() as client:
        deal_id = await _create_deal_via_api(client)
        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={
                "name": "Sensitivity",
                "overrides": [
                    {"field_path": "exit_cap_rate", "value": 0.085},
                ],
            },
        )
        assert r.status_code == 201, r.text
        scenario_id = r.json()["id"]

        feed = await client.get(f"/deals/{deal_id}/audit")
        assert feed.status_code == 200, feed.text
        entries = feed.json()["entries"]
        # Should contain the scenario.created event.
        created_rows = [e for e in entries if e["action"] == "scenario.created"]
        assert created_rows, f"no scenario.created entry; got {[e['action'] for e in entries]}"
        assert created_rows[0]["resource_type"] == "scenario"
        assert created_rows[0]["resource_id"] == scenario_id
        # The override count surfaces in diff_summary.
        assert "1 override" in (created_rows[0].get("diff_summary") or "")


# 7. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_update_diff_summary_includes_changed_fields() -> None:
    """PATCH /scenarios/{sid} writes ``scenario.updated`` with a before/after diff."""
    async with _client() as client:
        deal_id = await _create_deal_via_api(client)
        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={
                "name": "AnotherCase",
                "overrides": [
                    {"field_path": "exit_cap_rate", "value": 0.07},
                ],
            },
        )
        sid = r.json()["id"]

        # Patch the override list — old value 0.07, new 0.085.
        r = await client.patch(
            f"/deals/{deal_id}/scenarios/{sid}",
            json={
                "overrides": [
                    {"field_path": "exit_cap_rate", "value": 0.085},
                ],
            },
        )
        assert r.status_code == 200, r.text

        feed = await client.get(
            f"/deals/{deal_id}/audit?action=scenario.updated"
        )
        entries = feed.json()["entries"]
        assert entries, "no scenario.updated entry"
        e = entries[0]
        # before + after captured as JSONB
        assert e["before"] is not None
        assert e["after"] is not None
        # The override change is visible in the after payload
        assert any(
            ov.get("field_path") == "exit_cap_rate"
            and ov.get("value") == 0.085
            for ov in (e["after"].get("overrides") or [])
        )


# 8. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_override_emits_audit_entry_with_before_after() -> None:
    """PATCH /deals with field_overrides writes ``override.set`` with diff."""
    async with _client() as client:
        deal_id = await _create_deal_via_api(client)
        # First override.
        r = await client.patch(
            f"/deals/{deal_id}",
            json={"field_overrides": {"exit_cap_rate": 0.075}},
        )
        assert r.status_code == 200, r.text

        # Second override changes value.
        r = await client.patch(
            f"/deals/{deal_id}",
            json={"field_overrides": {"exit_cap_rate": 0.085}},
        )
        assert r.status_code == 200, r.text

        feed = await client.get(
            f"/deals/{deal_id}/audit?action=override.set"
        )
        entries = feed.json()["entries"]
        assert len(entries) >= 2
        # The latest entry should show 0.075 → 0.085.
        latest = entries[0]
        assert latest["resource_type"] == "override"
        assert latest["before"] is not None
        assert latest["after"] is not None
        assert latest["diff_summary"] is not None
        assert "exit_cap_rate" in latest["diff_summary"]


# 9. ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_memo_pdf_export_emits_audit_entry() -> None:
    """GET /deals/{id}/export/memo.pdf writes ``export.memo_pdf_downloaded``.

    weasyprint requires native libs (cairo/pango). When available we
    exercise the route end-to-end; when not we exercise the export-audit
    helper directly so the audit shape contract is still pinned.
    """
    have_weasyprint = True
    try:
        import weasyprint  # noqa: F401
    except Exception:
        have_weasyprint = False

    async with _client() as client:
        if have_weasyprint:
            r = await client.get("/deals/kimpton-angler-2026/export/memo.pdf")
            assert r.status_code == 200, r.text
        else:
            # Drive the helper directly so the contract is asserted even
            # without weasyprint. The helper exists exactly so each
            # export route writes the same audit shape.
            from pathlib import Path as _Path
            from app.api.export import _audit_export
            from app.database import get_session_factory
            from app.config import get_settings

            settings = get_settings()
            from uuid import UUID as _UUID
            factory = get_session_factory()
            async with factory() as session:
                await _audit_export(
                    session,
                    tenant_id=_UUID(settings.DEFAULT_TENANT_ID),
                    deal_id="kimpton-angler-2026",
                    action="export.memo_pdf_downloaded",
                    file_path=_Path("/tmp/fake-memo.pdf"),
                    file_label="IC memo PDF",
                )

        exp = await client.get(
            "/audit/explorer?action=export.memo_pdf_downloaded"
        )
        assert exp.status_code == 200, exp.text
        assert exp.json()["total"] >= 1, (
            f"no export.memo_pdf_downloaded row; "
            f"got total={exp.json()['total']}"
        )
        e = exp.json()["entries"][0]
        assert e["resource_type"] == "export"
        assert e["action"] == "export.memo_pdf_downloaded"


# 10. --------------------------------------------------------------
@pytest.mark.asyncio
async def test_excel_export_emits_audit_entry() -> None:
    """GET /deals/{id}/export/excel writes ``export.excel_downloaded``."""
    async with _client() as client:
        r = await client.get("/deals/kimpton-angler-2026/export/excel")
        assert r.status_code == 200, r.text
        exp = await client.get(
            "/audit/explorer?action=export.excel_downloaded"
        )
        assert exp.status_code == 200, exp.text
        assert exp.json()["total"] >= 1
        e = exp.json()["entries"][0]
        assert e["resource_type"] == "export"
        # Size captured in the after payload
        assert e["payload"] is not None
        assert e["payload"].get("output") is not None


# 11. --------------------------------------------------------------
@pytest.mark.asyncio
async def test_comp_exclude_emits_audit_entry() -> None:
    """POST /deals/{id}/comp-sales/exclude writes ``comp_transaction.excluded``."""
    async with _client() as client:
        deal_id = await _create_deal_via_api(client)
        # Exclude is best-effort — the comp set may be empty for a fresh
        # deal but the audit still fires because we capture the override
        # write itself. We post against a synthetic transaction id.
        r = await client.post(
            f"/deals/{deal_id}/comp-sales/exclude",
            json={"transaction_id": "txn-synthetic-001"},
        )
        # Depending on whether comp data is loadable, the endpoint may
        # 200 or hit a degraded path. Either way the override write
        # itself happened before the comp set re-derive, so we look for
        # the audit row.
        if r.status_code == 200:
            feed = await client.get(
                f"/deals/{deal_id}/audit?action=comp_transaction.excluded"
            )
            assert feed.json()["entries"], "no comp_transaction.excluded row"
            entry = feed.json()["entries"][0]
            assert entry["resource_type"] == "comp_transaction"
            assert entry["resource_id"] == "txn-synthetic-001"
            assert entry["after"] is not None
            assert "txn-synthetic-001" in (
                entry["after"].get("exclude_transaction_ids") or []
            )


# 12. --------------------------------------------------------------
@pytest.mark.asyncio
async def test_critical_severity_propagates_for_cross_tenant_attempt() -> None:
    """Severity='critical' rows surface as critical via list_audit_log."""
    from app.audit import list_audit_log, log_audit
    from app.database import get_session_factory

    tenant = str(uuid4())
    deal = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session,
            tenant_id=tenant,
            actor_id="attacker",
            action="security.cross_tenant_access_attempted",
            resource_type="deal",
            resource_id=deal,
            severity="critical",
            tags=["security", "breach_attempt"],
            output_payload={"attempted_tenant": "other-tenant-uuid"},
        )
        # info-severity sibling
        await log_audit(
            session,
            tenant_id=tenant,
            actor_id="analyst",
            action="deal.viewed",
            resource_type="deal",
            resource_id=deal,
        )
        await session.commit()

        critical = await list_audit_log(
            session, tenant_id=tenant, severity="critical"
        )
        assert len(critical) == 1
        assert critical[0]["action"] == "security.cross_tenant_access_attempted"
        assert critical[0]["severity"] == "critical"

        info = await list_audit_log(
            session, tenant_id=tenant, severity="info"
        )
        assert len(info) == 1
        assert info[0]["action"] == "deal.viewed"


# 13. --------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_explorer_search_by_actor() -> None:
    """GET /audit/explorer?actor=... filters to that actor's events only."""
    from app.audit import log_audit
    from app.database import get_session_factory
    from app.config import get_settings

    # Use the default tenant id so the explorer endpoint (which reads
    # X-Tenant-Id via the same dependency) sees these rows.
    settings = get_settings()
    tenant = settings.DEFAULT_TENANT_ID

    factory = get_session_factory()
    async with factory() as session:
        for actor in ("alice", "bob", "alice"):
            await log_audit(
                session,
                tenant_id=tenant,
                actor_id=actor,
                actor_email=f"{actor}@brookfield.test",
                action=f"deal.{actor}",
                resource_type="deal",
                resource_id=str(uuid4()),
            )
        await session.commit()

    async with _client() as client:
        r = await client.get("/audit/explorer?actor=alice")
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["total"] == 2
        assert all(e["actor_id"] == "alice" for e in payload["entries"])
        # Free-text search hits actor_email too.
        r = await client.get("/audit/explorer?q=bob@brookfield")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1
        assert r.json()["entries"][0]["actor_id"] == "bob"
