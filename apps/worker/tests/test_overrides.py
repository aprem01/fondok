"""Tests for analyst overrides (Roadmap item #6, June 2026).

Sam QA: "Override save is a no-op." Toast says "Override saved —
re-running engines," but after refresh the Y1 Occ badge still reads
'T-12', IRR is unchanged, and the badge tooltip shows the original
source text, not the justification note. Root cause was that
``_load_engine_inputs`` never read the persisted ``field_overrides``
column — so overrides written by the OverridePanel ``PATCH /deals/{id}``
landed in the DB but never reached the engines.

These tests pin the contract end-to-end:

* PATCH ``/deals/{id}`` with the structured ``{value, note}`` shape
  round-trips through the DB.
* ``_normalize_override_shape`` flattens both legacy + structured
  shapes to the scalar engines need.
* ``_load_engine_inputs`` consumes the persisted override and flips
  the source label to ``analyst_override``.
* Engine output reflects the override (Y1 occupancy moves from the
  Kimpton 0.762 seed to 0.55 after an override).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-overrides.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema + truncate before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "engine_outputs", "extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001 — table may not exist yet
                pass
        await session.commit()
    yield


# ─────────────────────────── unit tests ───────────────────────────


def test_normalize_override_shape_legacy() -> None:
    """Pre-2026-06 ``{path: value}`` shape passes through unchanged."""
    from app.services.engine_runner import _normalize_override_shape

    raw = {"starting_occupancy": 0.55, "exit_cap_rate": 0.075}
    assert _normalize_override_shape(raw) == raw


def test_normalize_override_shape_structured() -> None:
    """New ``{path: {value, note, …}}`` shape collapses to scalar."""
    from app.services.engine_runner import _normalize_override_shape

    raw = {
        "starting_occupancy": {
            "value": 0.55,
            "note": "PIP displacement Y1 — back-of-envelope per Sam",
            "overridden_by": "user:prem",
            "overridden_at": "2026-06-28T12:00:00Z",
        },
        # Mixed shapes coexist (legacy rows + new rows on same deal).
        "starting_adr": 380.0,
    }
    out = _normalize_override_shape(raw)
    assert out == {"starting_occupancy": 0.55, "starting_adr": 380.0}


def test_apply_overrides_alias_map_routing() -> None:
    """``_apply_overrides`` routes both dotted + bare keys via the alias map."""
    from app.services.engine_runner import _apply_overrides

    actuals: dict[str, float] = {}
    aliases = {
        "property_overview.year_built": "year_built",
        "year_built": "year_built",
    }
    _apply_overrides(actuals, {"property_overview.year_built": 2005}, aliases)
    assert actuals["year_built"] == 2005


def test_apply_overrides_percent_normalization() -> None:
    """Percent-style keys collapse ``75`` → ``0.75``."""
    from app.services.engine_runner import _apply_overrides

    actuals: dict[str, float] = {}
    aliases = {"starting_occupancy": "starting_occupancy"}
    pct = frozenset({"starting_occupancy"})
    _apply_overrides(
        actuals,
        {"starting_occupancy": 75.0},
        aliases,
        percentage_keys=pct,
    )
    assert actuals["starting_occupancy"] == 0.75


# ─────────────────────────── e2e via API + engine ───────────────────────────


@pytest.mark.asyncio
async def test_patch_overrides_round_trip_structured_shape() -> None:
    """PATCH /deals/{id} with ``{value, note}`` persists and reads back."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Override Hotel", "city": "Denver", "keys": 120},
        )
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]

        override_body = {
            "field_overrides": {
                "starting_occupancy": {
                    "value": 0.55,
                    "note": "Test override — PIP displacement",
                }
            }
        }
        r = await client.patch(f"/deals/{deal_id}", json=override_body)
        assert r.status_code == 200, r.text

        # Read back via GET — the structured shape must survive the round-trip.
        r = await client.get(f"/deals/{deal_id}")
        assert r.status_code == 200
        got = r.json()["field_overrides"]
        assert got["starting_occupancy"]["value"] == 0.55
        assert got["starting_occupancy"]["note"] == "Test override — PIP displacement"


@pytest.mark.asyncio
async def test_load_engine_inputs_consumes_persisted_override() -> None:
    """The big one: persisted override flips engine ``starting_occupancy``
    away from the Kimpton 0.762 seed AND tags it as ``analyst_override``."""
    from datetime import UTC, datetime

    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_ANALYST_OVERRIDE,
        _load_engine_inputs,
    )

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        # Insert a minimal deal row with a structured override.
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, field_overrides, created_at, updated_at)
                VALUES (:id, :tenant, :name, 'Draft', :overrides, :now, :now)
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "name": "Override Engine Hotel",
                "overrides": json.dumps(
                    {
                        "starting_occupancy": {
                            "value": 0.55,
                            "note": "Aggressive PIP Y1 displacement",
                        },
                        "exit_cap_rate": {
                            "value": 0.085,
                            "note": "Market shift — primary submarket",
                        },
                    }
                ),
                "now": datetime.now(UTC),
            },
        )
        await session.commit()

        base = await _load_engine_inputs(session, deal_id)

    # The engine sees the OVERRIDE, not the 0.762 Kimpton seed.
    assert base["starting_occupancy"] == 0.55, (
        f"override not applied: starting_occupancy={base['starting_occupancy']}"
    )
    assert base["exit_cap_rate"] == 0.085, (
        f"override not applied: exit_cap_rate={base['exit_cap_rate']}"
    )

    # Source labels flip to analyst_override so the badge surfaces it.
    sources = base["__sources__"]
    assert sources["starting_occupancy"] == SOURCE_ANALYST_OVERRIDE
    assert sources["exit_cap_rate"] == SOURCE_ANALYST_OVERRIDE


@pytest.mark.asyncio
async def test_assumption_sources_endpoint_shows_override() -> None:
    """``GET /deals/{id}/assumption_sources`` returns the override value
    AND the ``analyst_override`` source label — what the UI badges read."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Sources Hotel", "city": "Austin", "keys": 100},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "starting_occupancy": {
                        "value": 0.55,
                        "note": "Test override",
                    }
                }
            },
        )
        assert r.status_code == 200

        r = await client.get(f"/deals/{deal_id}/assumption_sources")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["values"]["starting_occupancy"] == 0.55
        assert body["sources"]["starting_occupancy"] == "analyst_override"
