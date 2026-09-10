"""Phase 2.1 — ``GET /deals/{id}/assumption_sources`` is byte-identical.

Phase 2.1 adds two ADDITIVE blocks to the loader (``__source_fields__`` /
``__reasons__``) and two additive blocks to the endpoint (``source_fields`` /
``reasons``). An external tester is mid-QA against the live contract, so the
two blocks Sam already reads — ``sources`` and ``values`` — must not move by
a single byte.

The golden file (``tests/fixtures/ontology/assumption_sources_fon54a.json``)
was produced from the FON-54a five-document fixture on the pre-Phase-2.1
tree (main @ 2dbdf58) and is compared here as canonicalised JSON text, not
as a dict — so a reordered key, a float that re-rendered, or a source label
that changed spelling all fail.

Regenerate ONLY with a recorded reason:

    FONDOK_WRITE_SNAPSHOT=1 pytest -q tests/test_assumption_sources_snapshot.py
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fondok_schemas.reasons import ReasonCode
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-assumption-sources-snapshot.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "54a1cff3-6f9b-57a9-8d2a-5511f3dd9f7e"
_GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "ontology"
    / "assumption_sources_fon54a.json"
)


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _seed_fon54a_deal(deal_id: UUID) -> None:
    """The five documents of ``test_variance_inputs_fon54a._seed_sams_deal``.

    Copied verbatim (not imported) so this snapshot pins a FIXED corpus:
    an edit to the variance fixture must not silently redefine what
    "byte-identical" means here.
    """
    from app.database import get_session_factory

    factory = get_session_factory()
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def ef(name: str, value: float, page: int = 1) -> dict:
        return {
            "field_name": name,
            "value": value,
            "source_page": page,
            "confidence": 0.8,
        }

    docs = [
        ("Copy of Miami Beach Anglers Offering Memorandum.pdf", "OM", 0, [
            ef("ttm_summary_per_om.rooms_revenue_usd", 9_541_537, 14),
            ef("ttm_summary_per_om.gop_usd", 5_088_268, 14),
            ef("ttm_summary_per_om.noi_usd", 3_356_709, 14),
            ef("ttm_summary_per_om.occupancy_pct", 83, 12),
            ef("ttm_performance.subject.adr_usd", 385, 12),
            ef("ttm_performance.subject.revpar_usd", 319, 12),
            ef("ttm_performance.segment.luxury_upper_upscale.occupancy_pct", 74.1, 20),
            ef("ttm_performance.segment.luxury_upper_upscale.adr_usd", 452.1, 20),
            ef("ttm_performance.segment.luxury_upper_upscale.revpar_usd", 335.0, 20),
            ef("p_and_l_usali.2021.gop_usd", 4_851_106, 16),
            ef("p_and_l_usali.2021.rooms_revenue_usd", 8_100_000, 16),
            ef("p_and_l_usali.2022.rooms_revenue_usd", 9_000_000, 16),
            ef("p_and_l_usali.2023.rooms_revenue_usd", 9_400_000, 16),
        ]),
        ("Copy of The Angler_s - March 2025 Financials.xlsx", "T12", 1, [
            ef("p_and_l_usali.monthly.apr_2024.rooms_revenue", 954_187, 2),
            ef("p_and_l_usali.monthly.apr_2024.gop", 514_931, 2),
            ef("p_and_l_usali.monthly.apr_2024.occupancy", 1, 2),
            ef("p_and_l_usali.monthly.apr_2024.adr", 300, 2),
            ef("occupancy_pct", 71.6, 1),
            ef("adr_usd", 372.4, 1),
            ef("revpar_usd", 266.6, 1),
            ef("p_and_l_usali.operating_revenue.rooms_revenue", 9_332_100, 2),
            ef("p_and_l_usali.operating_revenue.food_beverage_revenue", 3_216_620, 2),
            ef("p_and_l_usali.operating_revenue.total_revenue", 13_796_340, 2),
            ef("p_and_l_usali.gross_operating_profit", 4_970_460, 2),
            ef("p_and_l_usali.net_operating_income.noi_usd", 1_794_100, 2),
            ef("p_and_l_usali.net_operating_income.ebitda", 2_346_710, 2),
        ]),
        ("Copy of Angler_s 2024 Full Year Detailed P&L.xlsm", "PNL", 3, [
            ef("p_and_l_usali.monthly.jan.rooms_revenue", 954_187, 1),
            ef("p_and_l_usali.monthly.jan.gop", 514_931, 1),
            ef("occupancy_pct", 70.2, 1),
            ef("ttm_summary_per_om.occupancy_pct", 70.2, 1),
            ef("p_and_l_usali.operating_revenue.rooms_revenue", 9_100_000, 1),
            ef("p_and_l_usali.gross_operating_profit", 4_800_000, 1),
        ]),
        ("Copy of Angler_s 2023 P&L.xlsx", "PNL", 2, [
            ef("ttm_summary_per_om.occupancy_pct", 72.0, 1),
            ef("p_and_l_usali.gop_usd", 4_736_470, 1),
            ef("p_and_l_usali.total_revenues_usd", 12_940_200, 1),
            ef("p_and_l_usali.rooms.revenue_usd", 9_000_000, 1),
        ]),
        ("Copy of Angler_s 2019 P&L.xlsx", "PNL", 2, [
            ef("ttm_performance.subject.occupancy", 1, 1),
            ef("p_and_l_usali.gop", 1_912_060, 1),
            ef("p_and_l_usali.rooms.revenue", 6_339_940, 1),
            ef("p_and_l_usali.total_revenue", 8_385_990, 1),
        ]),
    ]

    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) "
                "VALUES (:id,:t,'Anglers (FON-54a)','Draft',0.0,:ts,:ts)"
            ),
            {"id": str(deal_id), "t": _TENANT, "ts": t0},
        )
        for fname, dtype, off, fields in docs:
            doc_id = uuid4()
            ts = t0 + timedelta(hours=off)
            await s.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                    "doc_type, status, uploaded_at) "
                    "VALUES (:id,:deal,:t,:f,:dt,'EXTRACTED',:ts)"
                ),
                {
                    "id": str(doc_id), "deal": str(deal_id), "t": _TENANT,
                    "f": fname, "dt": dtype, "ts": ts,
                },
            )
            await s.execute(
                text(
                    "INSERT INTO extraction_results (id, document_id, deal_id, "
                    "tenant_id, fields, confidence_report, agent_version, "
                    "created_at) VALUES (:id,:doc,:deal,:t,:f,'{}','v1',:ts)"
                ),
                {
                    "id": str(uuid4()), "doc": str(doc_id), "deal": str(deal_id),
                    "t": _TENANT, "f": json.dumps(fields), "ts": ts,
                },
            )
        await s.commit()


def _canonical(payload: dict) -> str:
    """Stable text form — sorted keys, no whitespace drift."""
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)


async def _sources_and_values(deal_id: UUID) -> dict:
    """Exactly what the endpoint puts in ``sources`` / ``values``."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    sources = base.pop("__sources__", {})
    values: dict = {}
    for k, v in base.items():
        if k.startswith("__"):
            continue
        if isinstance(v, (int, float, str, bool)) or v is None:
            values[k] = v
    return {
        "sources": {k: s for k, s in sources.items() if k in values},
        "values": values,
    }


@pytest.mark.asyncio
async def test_assumption_sources_payload_is_byte_identical() -> None:
    deal_id = uuid4()
    await _seed_fon54a_deal(deal_id)
    payload = await _sources_and_values(deal_id)
    text_now = _canonical(payload)

    if os.environ.get("FONDOK_WRITE_SNAPSHOT") == "1":
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(text_now + "\n", encoding="utf-8")
        pytest.skip(f"snapshot written to {_GOLDEN}")

    assert _GOLDEN.exists(), f"missing golden snapshot {_GOLDEN}"
    expected = _GOLDEN.read_text(encoding="utf-8").rstrip("\n")
    assert text_now == expected, (
        "assumption_sources.sources / .values moved. Phase 2.1 is additive "
        "only — every new block lives on a NEW key. Diff the golden at "
        f"{_GOLDEN}."
    )


@pytest.mark.asyncio
async def test_endpoint_serves_the_same_two_blocks_plus_the_new_ones() -> None:
    """Through the HTTP route: ``sources`` / ``values`` match the golden
    byte-for-byte, ``source_documents`` is untouched, and the two Phase 2.1
    blocks arrive as plain JSON (``code`` is the enum VALUE, not
    ``ReasonCode.NO_DOCUMENT``)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _seed_fon54a_deal(deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/assumption_sources",
            headers={"X-Tenant-Id": _TENANT},
        )
    assert r.status_code == 200, r.text
    body = r.json()

    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert body["sources"] == expected["sources"]
    assert body["values"] == expected["values"]

    # Additive blocks are present and JSON-clean.
    assert isinstance(body["source_fields"], dict)
    assert isinstance(body["reasons"], dict)
    # The wire carries the BARE ReasonCode string, matching what the web's
    # ProvenanceLedger and lineage drawer read (`Record<key, ReasonCode>`).
    # The prose behind a reason travels on GET /deals/{id}/lineage as
    # `unresolved[].detail`, so flattening here loses nothing.
    for key, code in body["reasons"].items():
        assert isinstance(code, str), (key, code)
        assert code in {c.value for c in ReasonCode}, (key, code)
    # This fixture has no CBRE report, so growth is a seed with a reason.
    assert body["reasons"]["adr_growth"] == "no_document"


@pytest.mark.asyncio
async def test_additive_blocks_are_invisible_to_sources_and_values() -> None:
    """``__source_fields__`` / ``__reasons__`` never leak into the two
    blocks the web app reads (they are ``__``-prefixed, and the endpoint
    filters those)."""
    deal_id = uuid4()
    await _seed_fon54a_deal(deal_id)
    payload = await _sources_and_values(deal_id)
    for block in ("sources", "values"):
        assert not [k for k in payload[block] if k.startswith("__")], (
            f"{block} leaked a dunder key"
        )
