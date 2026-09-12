"""FON-75 — a run that predates an engine block must be detectable.

The defect: on 2026-09-12 the whole Stabilization section of Overview rendered
five dashes on a healthy deal. The deal's persisted ``engine_outputs`` row had
been written before ``expense.outputs.stabilization`` existed, and nothing on
screen said so.

The mechanism under test (``app/services/run_freshness.py``) reads the
engine's declared output model off its ``BaseEngine[...]`` binding and compares
its field names against the KEYS of the persisted JSON. That works because
``engine_runner._persist_complete`` serialises with ``model_dump_json()`` and
WITHOUT ``exclude_none``, so a field the run knew about is always written —
even when its value is ``None``. Hence:

    key present, value null  → the engine answered "no value"  → NOT stale
    key absent entirely      → the run predates the field      → STALE

The load-bearing test is the last one: every engine in the registry, freshly
run through the real persistence path, must report nothing. It is what makes
the mechanism self-maintaining AND what guards against the only real failure
mode here — a false positive telling an analyst to re-run a current model.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Per-test SQLite database BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-run-freshness.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


# ─────────────────────── 1. the absent-vs-null rule ───────────────────────


def test_absent_block_key_is_reported() -> None:
    """A persisted expense output with no ``stabilization`` key is stale."""
    from app.services.run_freshness import missing_block_paths

    outputs = {
        "deal_id": str(uuid4()),
        "years": [],
        "noi_cagr": 0.0,
        "sourced_from_t12": [],
        "provenance": {},
    }
    assert missing_block_paths("expense", outputs) == ["stabilization"]


def test_present_null_block_is_not_reported() -> None:
    """``"stabilization": null`` is a real refusal, not a stale run.

    This is the distinction the whole module rests on: the engine ran WITH
    the field and could not resolve a year. Re-running changes nothing, so
    telling the analyst to re-run would be a lie.
    """
    from app.services.run_freshness import missing_block_paths

    outputs = {
        "deal_id": str(uuid4()),
        "years": [],
        "noi_cagr": 0.0,
        "sourced_from_t12": [],
        "provenance": {},
        "stabilization": None,
    }
    assert missing_block_paths("expense", outputs) == []


def test_nested_field_absent_from_a_present_block_is_reported_by_path() -> None:
    """A field added later to ``StabilizedYear`` is caught one level down.

    The outer key exists, so a top-level-only check would pass this run as
    fresh while the new row inside the block renders a dash.
    """
    from app.engines.stabilization import StabilizedYear
    from app.services.run_freshness import missing_block_paths

    full = {
        name: None
        for name in StabilizedYear.model_fields
    }
    block = {k: v for k, v in full.items() if k != "stabilized_cash_noi"}
    outputs = {
        "deal_id": str(uuid4()),
        "years": [],
        "noi_cagr": 0.0,
        "sourced_from_t12": [],
        "provenance": {},
        "stabilization": block,
    }
    assert missing_block_paths("expense", outputs) == [
        "stabilization.stabilized_cash_noi"
    ]


def test_unknown_engine_reports_nothing() -> None:
    """An engine the registry does not know is never stale.

    A detection bug must not invent a warning — the module's stated bias.
    """
    from app.services.run_freshness import missing_block_paths, output_model_for

    assert output_model_for("not_an_engine") is None
    assert missing_block_paths("not_an_engine", {"anything": 1}) == []
    # Non-dict outputs (a failed / never-run row) are equally silent.
    assert missing_block_paths("expense", None) == []


def test_every_registered_engine_declares_a_readable_output_model() -> None:
    """The binding read is the one every engine already writes down."""
    from pydantic import BaseModel

    from app.services.engine_runner import ENGINE_NAMES
    from app.services.run_freshness import output_model_for

    for name in ENGINE_NAMES:
        model = output_model_for(name)
        assert model is not None, f"{name} has no readable BaseEngine binding"
        assert issubclass(model, BaseModel)


def test_stale_engines_skips_rows_that_never_completed() -> None:
    """A failed / skipped engine has no outputs to compare — and its own banner."""
    from app.services.run_freshness import stale_engines

    rows = {
        "expense": {"status": "failed", "outputs": None, "error": "boom"},
        "returns": {"status": "running", "outputs": None},
    }
    assert stale_engines(rows) == {}


# ───────────────────── 2. the self-maintaining guarantee ──────────────────


async def _seed_deal(session, *, deal_id: str, tenant_id: str) -> None:
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, field_overrides) "
            "VALUES (:id, :tenant, :name, '{}')"
        ),
        {"id": deal_id, "tenant": tenant_id, "name": "Freshness Test"},
    )
    await session.commit()


@pytest.mark.asyncio
async def test_a_fresh_run_is_stale_for_no_engine() -> None:
    """THE load-bearing assertion: run every engine now, get nothing stale.

    This runs the real chain through the real persistence path
    (``_persist_complete`` → ``engine_outputs.outputs``) and reads it back the
    way the API does, then asserts the detector is silent for EVERY engine in
    ``ENGINE_NAMES``. Two things follow:

      * the mechanism is self-maintaining — a new engine, or a new field on an
        existing one, is covered the moment it is declared, and this test goes
        red if a future engine stops declaring its output model;
      * there are no false positives, which is the only way this feature can
        do harm (a banner telling an analyst to re-run a current model).
    """
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_run_scoped_outputs,
        run_all_engines,
    )
    from app.services.run_freshness import stale_engines

    await run_startup_migrations()
    deal_id = str(uuid4())
    tenant_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )
        rows = await get_run_scoped_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )

    # The run has to have actually produced every engine, or the assertion
    # below would pass vacuously.
    assert set(rows) == set(ENGINE_NAMES), sorted(rows)
    for name in ENGINE_NAMES:
        assert results[name]["status"] == "complete", (name, results[name])

    assert stale_engines(rows) == {}


@pytest.mark.asyncio
async def test_a_doctored_row_is_reported_from_the_persisted_snapshot() -> None:
    """Deleting the ``stabilization`` key from a stored row reproduces the bug.

    This is the 2026-09-12 deal in miniature: a real, complete run whose JSON
    simply lacks the block. Nothing recomputes; only the stored document is
    edited, exactly as an older run would have been written.
    """
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import (
        get_run_scoped_outputs,
        run_all_engines,
    )
    from app.services.run_freshness import stale_engines

    await run_startup_migrations()
    deal_id = str(uuid4())
    tenant_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )

        row = (
            await session.execute(
                text(
                    "SELECT id, outputs FROM engine_outputs "
                    "WHERE deal_id = :deal AND tenant_id = :tenant "
                    "AND engine_name = 'expense'"
                ),
                {"deal": deal_id, "tenant": tenant_id},
            )
        ).first()
        assert row is not None
        stored = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        assert "stabilization" in stored, "fixture assumption: fresh run has it"
        stored.pop("stabilization")
        await session.execute(
            text(
                "UPDATE engine_outputs SET outputs = :out "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"out": json.dumps(stored), "id": row[0], "tenant": tenant_id},
        )
        await session.commit()

        rows = await get_run_scoped_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )

    assert stale_engines(rows) == {"expense": ["stabilization"]}
