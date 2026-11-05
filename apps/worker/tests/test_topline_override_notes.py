"""Review I-6 (W5 topline fix) — topline settings that move numbers carry a note.

Since topline v2:

* ``stabilization_year`` ends the default penetration ramp (it moves Years
  1..S−1);
* ``topline_benchmark_level`` picks the level Year 1 is penetrated against
  (comp set → market can move Year 1 by double digits);
* the retired ``index_methodology`` is read as a forced benchmark level;
* ``deals.reopening_date`` (a deal column) re-anchors the default
  stabilization year, so it moves where the ramp ends.

Each now requires the analyst's justification like every other number-moving
override. API contract (unchanged shape): 422 ``{"code":
"override_note_required", "keys": [...], "message": ...}``. For the
``field_overrides`` keys the note rides in the ``{"value", "note"}`` envelope;
for the ``reopening_date`` column it is the sibling body field
``reopening_date_note`` (recorded on the ``deal.updated`` audit row). A clear
(null) moves the same numbers and needs one too; a draft deal (the wizard's autosave) is
not underwritten yet and needs none.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-topline-notes.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    async with get_session_factory()() as session:
        for tbl in ("audit_log", "scenarios", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [("stabilization_year", 4), ("topline_benchmark_level", "submarket"), ("index_methodology", "str_comp_set")],
)
async def test_topline_method_overrides_need_a_note(key: str, value) -> None:
    async with _client() as client:
        deal_id = (await client.post("/deals", json={"name": "Notes"})).json()["id"]
        r = await client.patch(f"/deals/{deal_id}", json={"field_overrides": {key: value}})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "override_note_required"
        assert detail["keys"] == [key]

        r = await client.patch(
            f"/deals/{deal_id}", json={"field_overrides": {key: {"value": value, "note": "IC memo, p.4"}}}
        )
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_the_segment_choice_moves_nothing_and_stays_exempt() -> None:
    """``index_market_segment`` no longer reaches the topline (the market
    level always reads the market-wide figures) — still a method choice."""
    async with _client() as client:
        deal_id = (await client.post("/deals", json={"name": "Seg"})).json()["id"]
        r = await client.patch(f"/deals/{deal_id}", json={"field_overrides": {"index_market_segment": "luxury"}})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_reopening_date_change_needs_a_note_and_records_it() -> None:
    from app.database import get_session_factory

    async with _client() as client:
        deal_id = (await client.post("/deals", json={"name": "Re-flag"})).json()["id"]
        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": "2027-03-15"})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "override_note_required"
        assert detail["keys"] == ["reopening_date"]
        assert "reopening_date_note" in detail["message"]
        # Nothing written.
        assert (await client.get(f"/deals/{deal_id}")).json()["reopening_date"] is None

        r = await client.patch(
            f"/deals/{deal_id}",
            json={"reopening_date": "2027-03-15", "reopening_date_note": "Re-flag per PIP schedule"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["reopening_date"] == "2027-03-15"

        # The same date again is not a change; a clear needs a note like a move.
        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": "2027-03-15"})
        assert r.status_code == 200, r.text
        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": None})
        assert r.status_code == 422, r.text
        r = await client.patch(f"/deals/{deal_id}", json={"reopening_date": None, "reopening_date_note": "Stays open"})
        assert r.status_code == 200, r.text

    async with get_session_factory()() as s:
        rows = (await s.execute(
            text("SELECT payload FROM audit_log WHERE action = 'deal.updated'")
        )).fetchall()
    payloads = [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in rows]
    assert any(((p.get("output") or {}).get("notes") or {}).get("reopening_date") == "Re-flag per PIP schedule" for p in payloads), payloads


@pytest.mark.asyncio
async def test_create_and_draft_autosave_need_no_reopening_note() -> None:
    async with _client() as client:
        r = await client.post("/deals", json={"name": "Wizard", "reopening_date": "2027-03-15"})
        assert r.status_code == 201, r.text
        r = await client.post("/deals", json={"name": "Draft", "lifecycle": "draft"})
        assert r.status_code == 201, r.text
        draft_id = r.json()["id"]
        r = await client.patch(f"/deals/{draft_id}", json={"reopening_date": "2027-06-01"})
        assert r.status_code == 200, r.text
