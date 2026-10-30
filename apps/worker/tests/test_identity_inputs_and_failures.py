"""Wave 0 Task 2 — engines refuse to run on identity seeds; failures are typed.

Pins the go-live contract:

* a deal with no ``keys`` / ``purchase_price`` never runs on the Kimpton
  identity seeds: the dependent engines land ``failed`` with the typed
  ``missing_identity_inputs`` failure listing the missing fields, no seed
  value reaches an output, and the deal row is never written;
* engines that do not need a missing identity input still run;
* a loader DB error is a ``load_error`` failure (with the exception type),
  never "no document", and ``report_alert`` fires at error severity;
* a malformed override blob is an error surfaced to the caller (500 with a
  clear body on the API, ``load_error`` in the engine path), never a silent
  Base case;
* ``POST /deals`` persists ``ai_confidence`` NULL and returns null;
* every run surface carries the failure list in a stable field;
* the Kimpton seed set is a test fixture injected through the
  ``assumptions=`` seam and still reproduces the golden numbers.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.fixtures.kimpton_seed import (
    KIMPTON_FIXTURE_DEAL_ID,
    KIMPTON_KEYS,
    KIMPTON_PURCHASE_PRICE,
    kimpton_seed,
)

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-identity-inputs.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

SEED_PRICE_STR = str(float(KIMPTON_PURCHASE_PRICE))


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    async with get_session_factory()() as session:
        for tbl in ("engine_outputs", "scenarios", "deals"):
            await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _insert_deal(
    session,
    *,
    tenant_id: str,
    keys: int | None = None,
    purchase_price: float | None = None,
    field_overrides: str = "{}",
) -> str:
    deal_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, keys, purchase_price,
                               field_overrides, created_at, updated_at)
            VALUES (:id, :tenant, 'Identity Hotel', 'Draft', :keys, :pp,
                    :fo, :now, :now)
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant_id,
            "keys": keys,
            "pp": purchase_price,
            "fo": field_overrides,
            "now": datetime.now(UTC),
        },
    )
    await session.commit()
    return deal_id


def _no_seed_identity_anywhere(blob: object) -> None:
    """Neither 132 keys nor $36.4M may appear in a serialized failure/result."""
    s = json.dumps(blob, default=str)
    assert "36400000" not in s, s[:400]
    assert '"keys": 132' not in s, s[:400]


# ─────────────────────── missing identity inputs ───────────────────────


@pytest.mark.asyncio
async def test_missing_identity_inputs_fail_every_dependent_engine_without_seeds() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_run_status,
        run_all_engines,
    )

    tenant_id, run_id = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        rows = await get_run_status(
            session, deal_id=deal_id, run_id=run_id, tenant_id=tenant_id
        )
        deal_row = (
            await session.execute(
                text("SELECT keys, purchase_price FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()

    assert set(results) == set(ENGINE_NAMES)
    for name, r in results.items():
        assert r["status"] == "failed", (name, r)
        assert r.get("outputs") is None
        assert r["failure"]["code"] == "missing_identity_inputs", (name, r)
        assert r["failure"]["engine"] == name
        assert r["failure"]["message"]
    # Each engine lists exactly the identity inputs it (transitively) needs.
    assert results["revenue"]["failure"]["missing"] == ["keys"]
    assert results["capital"]["failure"]["missing"] == ["keys", "purchase_price"]
    assert results["returns"]["failure"]["missing"] == ["keys", "purchase_price"]
    _no_seed_identity_anywhere(results)

    # Persisted, typed, and readable back through the row reader.
    assert {r["engine"] for r in rows} == set(ENGINE_NAMES)
    for r in rows:
        assert r["status"] == "failed"
        assert r["outputs"] is None
        assert r["failure"]["code"] == "missing_identity_inputs"
        assert r["error"] == r["failure"]["message"]
    # The deal row is never overwritten with seed values.
    assert deal_row._mapping["keys"] is None
    assert deal_row._mapping["purchase_price"] is None


@pytest.mark.asyncio
async def test_engines_that_do_not_need_a_missing_input_still_run() -> None:
    """Keys present, price absent: the operating chain runs, the capital
    stack and everything on it refuses, naming only ``purchase_price``."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id, keys=150)
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )

    for name in ("revenue", "fb", "expense"):
        assert results[name]["status"] == "complete", results[name]
    # The operating chain ran on the deal's OWN key count, not the seed's 132:
    # Year-1 rooms revenue = keys × 365 × occupancy × ADR (occupancy / ADR are
    # the labelled defaults, so the ratio to the seed run is exactly 150/132).
    seed = kimpton_seed()
    y1_rooms = results["revenue"]["outputs"]["years"][0]["rooms_revenue"]
    assert y1_rooms == pytest.approx(
        150 * 365 * seed["starting_occupancy"] * seed["starting_adr"], rel=1e-6
    )
    for name in ("capital", "debt", "returns", "sensitivity", "partnership", "cash_flow"):
        assert results[name]["status"] == "failed", results[name]
        assert results[name]["failure"]["code"] == "missing_identity_inputs"
        assert results[name]["failure"]["missing"] == ["purchase_price"]
    _no_seed_identity_anywhere(results)


@pytest.mark.asyncio
async def test_run_single_engine_refuses_on_missing_identity_inputs() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import get_latest_output, run_single_engine

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
        result = await run_single_engine(
            session, deal_id=deal_id, tenant_id=tenant_id, engine_name="returns"
        )
        row = await get_latest_output(
            session, deal_id=deal_id, engine_name="returns", tenant_id=tenant_id
        )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "missing_identity_inputs"
    assert result["failure"]["missing"] == ["keys", "purchase_price"]
    assert row is not None and row["failure"]["code"] == "missing_identity_inputs"


@pytest.mark.asyncio
async def test_loader_reports_missing_identity_inputs_as_reasons() -> None:
    """The assumption map carries no identity value and says why."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
        base = await _load_engine_inputs(session, deal_id, tenant_id=tenant_id)

    assert "keys" not in base
    assert "purchase_price" not in base
    assert "keys" not in base["__sources__"]
    assert base["__reasons__"]["keys"]["code"] == "missing_identity_inputs"
    assert base["__reasons__"]["purchase_price"]["code"] == "missing_identity_inputs"


@pytest.mark.asyncio
async def test_returns_preview_refuses_on_missing_identity_inputs() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={}, headers={"X-Tenant-Id": tenant_id},
        )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "missing_identity_inputs"
    assert detail["missing"] == ["keys", "purchase_price"]


# ─────────────────────────── load_error ───────────────────────────


def _alert_recorder(monkeypatch):
    from app.services import engine_runner

    calls: list[dict] = []

    def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(engine_runner, "report_alert", _record)
    return calls


@pytest.mark.asyncio
async def test_loader_db_error_is_a_typed_load_error_and_alerts(monkeypatch) -> None:
    """A mid-loader table missing (the portfolio library) used to seed with
    reason "no document". It is a ``load_error`` on every engine."""
    from app.database import get_session_factory
    from app.services.engine_runner import ENGINE_NAMES, get_run_status, run_all_engines

    calls = _alert_recorder(monkeypatch)
    tenant_id, run_id = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(
            session, tenant_id=tenant_id, keys=KIMPTON_KEYS,
            purchase_price=KIMPTON_PURCHASE_PRICE,
        )
        await session.execute(text("DROP TABLE portfolio_library"))
        await session.commit()
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        rows = await get_run_status(
            session, deal_id=deal_id, run_id=run_id, tenant_id=tenant_id
        )

    assert set(results) == set(ENGINE_NAMES)
    for name, r in results.items():
        assert r["status"] == "failed", (name, r)
        assert r["failure"]["code"] == "load_error", (name, r)
        assert r["failure"]["exception_type"], (name, r)
        assert "no document" not in r["failure"]["message"].lower()
    assert {r["engine"] for r in rows} == set(ENGINE_NAMES)
    assert all(r["failure"]["code"] == "load_error" for r in rows)
    assert len(calls) == 1
    assert calls[0]["severity"] == "error"
    assert calls[0]["deal_id"] == deal_id
    assert calls[0]["exc"] is not None


@pytest.mark.asyncio
async def test_deal_row_read_error_is_a_load_error_not_a_seed_run(monkeypatch) -> None:
    """A4 / B7 — the deal-row and as-of override reads failing must not fall
    through to defaults (and the as-of gate must not silently switch off)."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    calls = _alert_recorder(monkeypatch)
    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(
            session, tenant_id=tenant_id, keys=KIMPTON_KEYS,
            purchase_price=KIMPTON_PURCHASE_PRICE,
        )
        await session.execute(text("ALTER TABLE deals RENAME TO deals_unreadable"))
        await session.commit()
        try:
            results = await run_all_engines(
                session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
            )
        finally:
            await session.execute(text("ALTER TABLE deals_unreadable RENAME TO deals"))
            await session.commit()

    assert all(r["status"] == "failed" for r in results.values())
    assert all(r["failure"]["code"] == "load_error" for r in results.values())
    assert results["revenue"]["failure"]["exception_type"]
    assert len(calls) == 1 and calls[0]["severity"] == "error"


@pytest.mark.asyncio
async def test_malformed_override_blob_is_a_load_error_in_the_engine_path(monkeypatch) -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    calls = _alert_recorder(monkeypatch)
    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(
            session, tenant_id=tenant_id, keys=KIMPTON_KEYS,
            purchase_price=KIMPTON_PURCHASE_PRICE, field_overrides="not json {",
        )
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )
    assert all(r["failure"]["code"] == "load_error" for r in results.values())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_malformed_override_blob_is_a_500_with_a_clear_body_on_the_api() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(
            session, tenant_id=tenant_id, keys=KIMPTON_KEYS,
            purchase_price=KIMPTON_PURCHASE_PRICE, field_overrides="not json {",
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/deals/{deal_id}", headers={"X-Tenant-Id": tenant_id})
        assert r.status_code == 500, r.text
        assert "field_overrides" in r.json()["detail"]
        # The memo envelope reads the same column — same answer, never Base.
        r = await c.get(f"/deals/{deal_id}/memo", headers={"X-Tenant-Id": tenant_id})
        assert r.status_code == 500, r.text
        assert "field_overrides" in r.json()["detail"]


# ─────────────────────────── ai_confidence ───────────────────────────


@pytest.mark.asyncio
async def test_create_deal_persists_null_ai_confidence_and_returns_null() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    tenant_id = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/deals", json={"name": "Confidence Hotel", "keys": 100},
            headers={"X-Tenant-Id": tenant_id},
        )
        assert r.status_code == 201, r.text
        assert r.json()["ai_confidence"] is None
        deal_id = r.json()["id"]
        r = await c.get(f"/deals/{deal_id}", headers={"X-Tenant-Id": tenant_id})
        assert r.json()["ai_confidence"] is None
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                text("SELECT ai_confidence FROM deals WHERE id = :id"), {"id": deal_id}
            )
        ).first()
    assert row._mapping["ai_confidence"] is None


# ───────────────────── run surfaces carry the failures ─────────────────────


@pytest.mark.asyncio
async def test_run_status_and_engine_list_carry_the_failure_list() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app
    from app.services.engine_runner import ENGINE_NAMES, run_all_engines

    tenant_id, run_id = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
    headers = {"X-Tenant-Id": tenant_id}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/deals/{deal_id}/engines/run/{run_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert {f["engine"] for f in body["failures"]} == set(ENGINE_NAMES)
        for f in body["failures"]:
            assert f["code"] == "missing_identity_inputs"
            assert f["message"]
            assert f["missing"]
        assert all(e["failure"]["code"] == "missing_identity_inputs" for e in body["engines"])

        r = await c.get(f"/deals/{deal_id}/engines", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert {f["engine"] for f in body["failures"]} == set(ENGINE_NAMES)
        assert body["engines"]["returns"]["failure"]["missing"] == ["keys", "purchase_price"]

        r = await c.post(
            f"/deals/{deal_id}/engines/returns/run", json={}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "failed"
        assert r.json()["failure"]["code"] == "missing_identity_inputs"


@pytest.mark.asyncio
async def test_an_engine_exception_is_a_typed_engine_error_and_upstream_skips_are_typed() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    async with get_session_factory()() as session:
        results = await run_all_engines(
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=str(uuid4()),
            run_id=str(uuid4()), overrides={"ltv": 1.5}, assumptions=kimpton_seed(),
        )
    cap = results["capital"]
    assert cap["status"] == "failed"
    assert cap["failure"]["code"] == "engine_error"
    assert cap["failure"]["exception_type"] == "ValidationError"
    assert cap["failure"]["message"] == cap["error"]
    assert results["debt"]["failure"]["code"] == "engine_skipped"
    assert "capital" in results["debt"]["failure"]["message"]
    assert results["revenue"]["status"] == "complete"
    assert "failure" not in results["revenue"]


# ───────────────────────── the Kimpton fixture seam ─────────────────────────


@pytest.mark.asyncio
async def test_kimpton_fixture_runs_through_the_assumptions_seam() -> None:
    """The golden path: nothing is read from the deal; the numbers are the
    pre-fixture Kimpton numbers; outputs carry the baseline's deal_id."""
    from app.database import get_session_factory
    from app.services.engine_runner import ENGINE_NAMES, run_all_engines

    async with get_session_factory()() as session:
        results = await run_all_engines(
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=str(uuid4()),
            run_id=str(uuid4()), assumptions=kimpton_seed(),
        )
    assert all(results[n]["status"] == "complete" for n in ENGINE_NAMES)
    cap = results["capital"]["outputs"]
    assert cap["deal_id"] == KIMPTON_FIXTURE_DEAL_ID
    assert cap["total_capital"] == pytest.approx(43_262_900.0)
    assert cap["debt_amount"] == pytest.approx(23_660_000.0)


def test_kimpton_fixture_defaults_match_production_defaults() -> None:
    """The fixture is self-contained, and its non-identity values equal the
    production labelled defaults. A production default change therefore
    fails here first and becomes a conscious golden re-baseline."""
    from app.services.engine_runner import IDENTITY_INPUT_KEYS, _default_assumptions

    fixture = kimpton_seed()
    assert set(IDENTITY_INPUT_KEYS) == {"keys", "purchase_price"}
    for key in IDENTITY_INPUT_KEYS:
        assert key in fixture
        assert key not in _default_assumptions()
    assert {k: v for k, v in fixture.items() if k not in IDENTITY_INPUT_KEYS} == (
        _default_assumptions()
    )


def test_identity_needs_are_transitive_over_engine_deps() -> None:
    from app.services.engine_runner import ENGINE_NAMES, identity_inputs_needed

    assert identity_inputs_needed("revenue") == frozenset({"keys"})
    assert identity_inputs_needed("fb") == frozenset({"keys"})
    assert identity_inputs_needed("capital") == frozenset({"keys", "purchase_price"})
    for name in ("debt", "returns", "sensitivity", "partnership", "cash_flow"):
        assert identity_inputs_needed(name) == frozenset({"keys", "purchase_price"}), name
    assert set(ENGINE_NAMES) >= {"revenue", "capital"}
