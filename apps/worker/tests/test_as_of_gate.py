"""Phase 2.1 — the underwriting as-of gate.

A model run answers "what did we know, and when did we know it". A market
report published AFTER the underwriting date was not knowable then, so it
must not silently ground a number.

The rule as implemented:

* the gate reads ONLY an explicit ``underwriting_as_of`` override. It does
  NOT fall back to ``acquisition_close_date`` — that is a modelling input
  (the live QA deal e577f547 carries 2021-06-01 against 2025 STR material),
  not a knowledge horizon. With no ``underwriting_as_of`` the gate is
  completely inert;
* a document is refused only when it is after the underwriting date AT ITS
  OWN PRECISION (``documents.report_as_of_precision``: day / month /
  quarter / year). The as-of builder dates a year-precision STAR report to
  31 December, so a 2025 report under a mid-2025 as-of is ADMITTED;
* a refusal flips the source label to the ``*_unavailable`` sibling and
  attaches ``not_knowable_as_of`` — it never blanks a number silently;
* no date at all → the value LOADS and carries ``as_of_unknown``. A flag,
  never a refusal.

Comparable sales get the same anchor a different way: the as-of becomes the
comp engine's ``today``, so the 5-year lookback and the recency weights are
measured from the underwriting date rather than the wall clock.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-as-of-gate.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-000000000001"
_AS_OF = "2024-06-30"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        # The ``report_as_of`` columns are landing on a sibling branch
        # (services/as_of.py + a migration). Add them here so the gate is
        # exercised; the loaders are written to work with OR without them.
        for ddl in (
            "ALTER TABLE documents ADD COLUMN report_as_of DATE",
            "ALTER TABLE documents ADD COLUMN report_as_of_precision TEXT",
        ):
            try:
                await session.execute(text(ddl))
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()
        for tbl in ("extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _insert_deal(deal_id: UUID, *, overrides: dict | None = None) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, field_overrides, created_at, updated_at
                ) VALUES (
                    :id, :tenant, 'As-of deal', 'Underwriting', 0.0, 132,
                    36400000, :fo, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "fo": json.dumps(overrides or {}),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_doc(
    deal_id: UUID,
    *,
    doc_type: str,
    fields: list[dict],
    report_as_of: str | None = None,
    precision: str | None = None,
) -> UUID:
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    ts = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, report_as_of, report_as_of_precision
                ) VALUES (
                    :id, :deal, :tenant, :fn, :dt, 'EXTRACTED', :ts,
                    :as_of, :prec
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "fn": f"{doc_type.lower()}.pdf",
                "dt": doc_type,
                "ts": ts,
                "as_of": report_as_of,
                "prec": precision,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :f, '{}', 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "f": json.dumps(fields),
                "ts": ts,
            },
        )
        await session.commit()
    return doc_id


_STR_SUBJECT_TTM = [
    {"field_name": "ttm_performance.subject.occupancy_pct", "value": 78.5},
    {"field_name": "ttm_performance.subject.adr_usd", "value": 412.0},
]


async def _base_for(deal_id: UUID) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        return await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )


# ══════════════════════════ STR — refuse / flag / load ══════════════════


@pytest.mark.asyncio
async def test_str_report_after_the_as_of_is_refused_not_used() -> None:
    """An STR report published after the underwriting date does not seed the
    model; the flag key flips to ``str_forecast_unavailable`` and carries
    ``not_knowable_as_of``."""
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        overrides={
            "underwriting_as_of": _AS_OF,
            "revenue_seed_from_str_forecast": True,
        },
    )
    await _insert_doc(
        deal_id,
        doc_type="STR_TREND",
        fields=_STR_SUBJECT_TTM,
        report_as_of="2025-03-31",
        precision="day",
    )

    base = await _base_for(deal_id)
    sources = base["__sources__"]
    reasons = base["__reasons__"]

    assert sources["revenue_seed_from_str_forecast"] == "str_forecast_unavailable"
    assert (
        reasons["revenue_seed_from_str_forecast"]["code"]
        is ReasonCode.NOT_KNOWABLE_AS_OF
    )
    # The value was NOT loaded — Year-1 stays off the STR rates.
    assert sources["starting_adr"] != "str_forecast"
    assert base["starting_adr"] != pytest.approx(412.0)


@pytest.mark.asyncio
async def test_str_report_with_no_date_loads_and_flags_as_of_unknown() -> None:
    """No ``report_as_of`` is a FLAG, never a refusal — Sam's number stays."""
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        overrides={
            "underwriting_as_of": _AS_OF,
            "revenue_seed_from_str_forecast": True,
        },
    )
    await _insert_doc(
        deal_id, doc_type="STR_TREND", fields=_STR_SUBJECT_TTM, report_as_of=None
    )

    base = await _base_for(deal_id)
    assert base["__sources__"]["revenue_seed_from_str_forecast"] == "str_forecast"
    assert base["starting_adr"] == pytest.approx(412.0)
    assert base["starting_occupancy"] == pytest.approx(0.785)
    assert (
        base["__reasons__"]["revenue_seed_from_str_forecast"]["code"]
        is ReasonCode.AS_OF_UNKNOWN
    )


@pytest.mark.asyncio
async def test_year_precision_report_in_the_as_of_year_is_admitted() -> None:
    """The as-of builder dates a year-precision STAR report to 31 December.
    Comparing raw dates would refuse a May-2025 report under a mid-2025
    as-of; comparing at the document's own precision admits it."""
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        overrides={
            "underwriting_as_of": "2025-05-01",
            "revenue_seed_from_str_forecast": True,
        },
    )
    await _insert_doc(
        deal_id,
        doc_type="STR_TREND",
        fields=_STR_SUBJECT_TTM,
        report_as_of="2025-12-31",
        precision="year",
    )

    base = await _base_for(deal_id)
    assert base["__sources__"]["revenue_seed_from_str_forecast"] == "str_forecast"
    assert base["starting_adr"] == pytest.approx(412.0)


@pytest.mark.asyncio
async def test_acquisition_close_date_is_not_an_as_of_date() -> None:
    """The live QA deal shape: ``acquisition_close_date`` 2021 with 2025 STR
    material. The close date must NOT gate anything — the 2025 report loads
    and no reason is attached."""
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        overrides={
            "acquisition_close_date": "2021-06-01",
            "revenue_seed_from_str_forecast": True,
        },
    )
    await _insert_doc(
        deal_id,
        doc_type="STR_TREND",
        fields=_STR_SUBJECT_TTM,
        report_as_of="2025-12-31",
        precision="year",
    )

    base = await _base_for(deal_id)
    assert base["__sources__"]["revenue_seed_from_str_forecast"] == "str_forecast"
    assert base["starting_adr"] == pytest.approx(412.0)
    assert base["__reasons__"].get("revenue_seed_from_str_forecast") is None


# ═════════════════════ OM comps + CBRE — the same rule ══════════════════


def _comp_fields(n: int = 4) -> list[dict]:
    """``n`` broker comps, each with a cap rate inside the 4-11% band."""
    out: list[dict] = []
    for i, cap in enumerate([6.0, 6.5, 7.0, 7.5][:n]):
        out.append(
            {
                "field_name": f"transaction_comps.{i}.cap_rate_pct",
                "value": cap,
                "source_page": 20 + i,
            }
        )
    return out


@pytest.mark.asyncio
async def test_om_comps_after_the_as_of_flip_to_unavailable() -> None:
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, overrides={"underwriting_as_of": _AS_OF})
    await _insert_doc(
        deal_id,
        doc_type="OM",
        fields=_comp_fields(),
        report_as_of="2025-01-15",
        precision="day",
    )

    base = await _base_for(deal_id)
    assert base["__sources__"]["exit_cap_rate"] == "om_comps_unavailable"
    assert (
        base["__reasons__"]["exit_cap_rate"]["code"]
        is ReasonCode.NOT_KNOWABLE_AS_OF
    )
    # The Kimpton seed cap rate survives — nothing was blanked.
    assert base["exit_cap_rate"] == pytest.approx(0.07)


@pytest.mark.asyncio
async def test_om_comps_before_the_as_of_load_and_name_their_row() -> None:
    """An OM that predates the as-of grounds the exit cap AND, on an odd
    comp count, names the exact comp row that IS the median."""
    deal_id = uuid4()
    await _insert_deal(deal_id, overrides={"underwriting_as_of": _AS_OF})
    doc_id = await _insert_doc(
        deal_id,
        doc_type="OM",
        fields=_comp_fields(3),  # 6.0 / 6.5 / 7.0 → median 6.5 on one row
        report_as_of="2024-01-15",
        precision="day",
    )

    base = await _base_for(deal_id)
    assert base["__sources__"]["exit_cap_rate"] == "om_comps"
    assert base["exit_cap_rate"] == pytest.approx(0.065)
    sf = base["__source_fields__"]["exit_cap_rate"]
    assert sf["field_name"] == "transaction_comps.1.cap_rate_pct"
    assert sf["source_page"] == 21
    assert sf["document_id"] == str(doc_id)
    assert sf["as_of"] == "2024-01-15"
    assert "exit_cap_rate" not in base["__reasons__"]


@pytest.mark.asyncio
async def test_cbre_report_after_the_as_of_flips_growth_to_unavailable() -> None:
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, overrides={"underwriting_as_of": _AS_OF})
    await _insert_doc(
        deal_id,
        doc_type="CBRE_HORIZONS",
        fields=[
            {"field_name": "cbre_horizons.year_1.adr_usd", "value": 300.0},
            {"field_name": "cbre_horizons.year_5.adr_usd", "value": 330.0},
            {"field_name": "cbre_horizons.year_1.revpar_usd", "value": 200.0},
            {"field_name": "cbre_horizons.year_5.revpar_usd", "value": 220.0},
        ],
        report_as_of="2025-09-30",
        precision="day",
    )

    base = await _base_for(deal_id)
    for key in ("adr_growth", "revpar_growth"):
        assert base["__sources__"][key] == "cbre_horizons_unavailable"
        assert (
            base["__reasons__"][key]["code"] is ReasonCode.NOT_KNOWABLE_AS_OF
        )
    # Seeds untouched.
    assert base["adr_growth"] == pytest.approx(0.04)
    assert base["revpar_growth"] == pytest.approx(0.045)


@pytest.mark.asyncio
async def test_no_underwriting_as_of_means_nothing_changes() -> None:
    """The whole gate is inert without an explicit ``underwriting_as_of``:
    a 2025 CBRE report still grounds growth on a deal with no as-of."""
    deal_id = uuid4()
    await _insert_deal(deal_id, overrides={})
    await _insert_doc(
        deal_id,
        doc_type="CBRE_HORIZONS",
        fields=[
            {"field_name": "cbre_horizons.year_1.adr_usd", "value": 300.0},
            {"field_name": "cbre_horizons.year_5.adr_usd", "value": 330.0},
        ],
        report_as_of="2025-09-30",
        precision="day",
    )

    base = await _base_for(deal_id)
    assert base["__sources__"]["adr_growth"] == "cbre_horizons"
    assert base["adr_growth"] != pytest.approx(0.04)
    assert "adr_growth" not in base["__reasons__"]


# ═════════════════ comp sales — the as-of is the engine's "today" ═══════


@pytest.mark.asyncio
async def test_comp_lookback_is_measured_from_the_underwriting_date() -> None:
    """``today`` reaches ``build_comp_set``: with the as-of as the anchor a
    comp older than the 5-year lookback measured FROM THAT DATE is dropped by
    the engine's own lookback, and one inside the window survives.

    (Note: the engine's lookback drops comps that are too OLD relative to
    ``today``; it does not drop a sale dated after ``today``. Anchoring
    ``today`` to the underwriting date is what makes the window honest.)
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _build_comp_sales_set

    as_of = date(2024, 6, 30)
    old_sale = (as_of - timedelta(days=int(365.25 * 7))).isoformat()
    recent_sale = (as_of - timedelta(days=365)).isoformat()

    deal_id = uuid4()
    await _insert_deal(deal_id, overrides={"underwriting_as_of": as_of.isoformat()})
    await _insert_doc(
        deal_id,
        doc_type="OM",
        fields=[
            {"field_name": "transaction_comps.0.property_name", "value": "Too old"},
            {"field_name": "transaction_comps.0.cap_rate_pct", "value": 6.0},
            {"field_name": "transaction_comps.0.sale_date", "value": old_sale},
            {"field_name": "transaction_comps.1.property_name", "value": "In window"},
            {"field_name": "transaction_comps.1.cap_rate_pct", "value": 7.0},
            {"field_name": "transaction_comps.1.sale_date", "value": recent_sale},
        ],
        report_as_of="2024-01-15",
        precision="day",
    )

    factory = get_session_factory()
    async with factory() as session:
        comp_set = await _build_comp_sales_set(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    assert comp_set.total_count == 2
    notes = " | ".join(comp_set.weighting_notes)
    assert "sale > 5 yrs old" in notes
    # Only the in-window comp survives to the derivation (the comp engine
    # reports cap rates as published percents, not fractions).
    assert comp_set.derived_cap_rate_median == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_comp_set_without_an_as_of_uses_today() -> None:
    """No ``underwriting_as_of`` → ``today=None`` → the engine's own
    ``date.today()``: unchanged behaviour."""
    from app.database import get_session_factory
    from app.services.engine_runner import _build_comp_sales_set

    recent = (date.today() - timedelta(days=200)).isoformat()
    deal_id = uuid4()
    await _insert_deal(deal_id, overrides={})
    await _insert_doc(
        deal_id,
        doc_type="OM",
        fields=[
            {"field_name": "transaction_comps.0.property_name", "value": "Recent"},
            {"field_name": "transaction_comps.0.cap_rate_pct", "value": 7.0},
            {"field_name": "transaction_comps.0.sale_date", "value": recent},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        comp_set = await _build_comp_sales_set(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    assert comp_set.total_count == 1
    assert "sale > 5 yrs old" not in " | ".join(comp_set.weighting_notes)


# ═══════════════════════ degrading without the column ═══════════════════


@pytest.mark.asyncio
async def test_missing_report_as_of_column_degrades_to_unknown() -> None:
    """The ``report_as_of`` columns are landing on a sibling branch. Until
    they do, every read is ``None`` and the gate refuses nothing."""
    from app.services.engine_runner import _is_after_as_of

    assert _is_after_as_of(None, None, date(2024, 6, 30)) is False
    assert _is_after_as_of("", "day", date(2024, 6, 30)) is False
    assert _is_after_as_of("not-a-date", "day", date(2024, 6, 30)) is False
    # And with no underwriting date, nothing is ever after it.
    assert _is_after_as_of("2030-01-01", "day", None) is False


@pytest.mark.asyncio
async def test_precision_comparisons() -> None:
    from app.services.engine_runner import _is_after_as_of

    as_of = date(2025, 5, 1)
    # year precision: only a LATER year is refused.
    assert _is_after_as_of("2025-12-31", "year", as_of) is False
    assert _is_after_as_of("2026-12-31", "year", as_of) is True
    # quarter precision: Q2 2025 admitted, Q3 2025 refused.
    assert _is_after_as_of("2025-06-30", "quarter", as_of) is False
    assert _is_after_as_of("2025-09-30", "quarter", as_of) is True
    # month precision: May admitted, June refused.
    assert _is_after_as_of("2025-05-31", "month", as_of) is False
    assert _is_after_as_of("2025-06-30", "month", as_of) is True
    # day precision (and a NULL precision) compare exactly.
    assert _is_after_as_of("2025-05-01", "day", as_of) is False
    assert _is_after_as_of("2025-05-02", "day", as_of) is True
    assert _is_after_as_of("2025-05-02", None, as_of) is True
