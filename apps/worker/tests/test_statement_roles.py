"""FON-22 — one primary-statement choice for every surface.

The engine's per-period choice (``engine_runner.choose_statements_by_period``:
the shared ranking key with field-count completeness, then the footing and
outlier override) is the ONLY "primary statement" rule. The Data Room's
"Primary source" badge, the per-document statement role the Historical P&L
groups on, and the ledger's link fallback all read it.

Real deals (read-only probe, 2026-11-03):

* b67bfdee — the FY2024 Detailed P&L (1,031 numeric fields, uploaded first)
  and the FY2024 Summary CSV (17 fields, uploaded a day later). Both score
  ``structural_pnl_score`` 1.0, so the old badge key tied on completeness and
  the newer CSV took the badge, while the model ran on the Detailed P&L.
* 7a9928e0 — the T-12 ranks first but its revenue lines do not foot; the
  December 2024 Financials of the same year foot and take the whole period.
  The old badge sat on the top-ranked T-12.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-statement-roles.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-000000000001"

_DETAILED_PNL = "Copy of Angler_s 2024 Full Year Detailed P&L.xlsm"
_SUMMARY_CSV = "Angler_FY2024_Summary_PL.csv"


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


async def _insert_deal(deal_id: UUID) -> None:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, field_overrides, created_at, updated_at
                ) VALUES (
                    :id, :tenant, 'FON-22', 'In Progress', 0.0, 132,
                    36400000, '{}', :ts, :ts
                )
                """
            ),
            {"id": str(deal_id), "tenant": _TENANT, "ts": datetime.now(UTC)},
        )
        await session.commit()


async def _insert_statement(
    deal_id: UUID,
    *,
    doc_type: str,
    filename: str,
    fields: list[dict[str, object]],
    ts: datetime,
    fiscal_year: int | None = None,
    extracted_period_year: int | None = None,
    report_as_of: str | None = None,
    structural_pnl_score: float | None = None,
) -> UUID:
    """An EXTRACTED financial document plus its extraction row. ``ts`` is both
    ``uploaded_at`` and the extraction's ``created_at``."""
    from app.database import get_session_factory

    doc_id = uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, fiscal_year, extracted_period_year,
                    report_as_of, structural_pnl_score
                ) VALUES (
                    :id, :deal, :tenant, :filename, :doc_type, 'EXTRACTED',
                    :ts, 1, :fy, :epy, :rao, :sps
                )
                """
            ),
            {
                "id": str(doc_id), "deal": str(deal_id), "tenant": _TENANT,
                "filename": filename, "doc_type": doc_type, "ts": ts,
                "fy": fiscal_year, "epy": extracted_period_year,
                "rao": report_as_of, "sps": structural_pnl_score,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (:id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts)
                """
            ),
            {
                "id": str(uuid4()), "doc": str(doc_id), "deal": str(deal_id),
                "tenant": _TENANT, "fields": json.dumps(fields), "ts": ts,
            },
        )
        await session.commit()
    return doc_id


async def _insert_document_only(deal_id: UUID, *, doc_type: str, filename: str, ts: datetime) -> UUID:
    from app.database import get_session_factory

    doc_id = uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count
                ) VALUES (:id, :deal, :tenant, :filename, :doc_type, 'EXTRACTED', :ts, 1)
                """
            ),
            {"id": str(doc_id), "deal": str(deal_id), "tenant": _TENANT,
             "filename": filename, "doc_type": doc_type, "ts": ts},
        )
        await session.commit()
    return doc_id


def detailed_pnl_fields(detail_lines: int = 1_000) -> list[dict[str, object]]:
    """The FY2024 Detailed P&L of b67bfdee: the real headline lines plus the
    ~1,000 itemised numeric lines that make it the more complete statement."""
    head: list[dict[str, object]] = [
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.832567},
        {"field_name": "ttm_performance.subject.adr_usd", "value": 236.094},
        {"field_name": "ttm_performance.subject.revpar_usd", "value": 196.564},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_496_407.22},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_739_040.71},
        {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 17_806.42},
        {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_228_475.94},
        {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 13_481_730.29},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_160_000.0},
    ]
    detail = [
        {"field_name": f"p_and_l_usali.detail.gl_{i:04d}", "value": float(1_000 + i)}
        for i in range(detail_lines)
    ]
    return head + detail


def summary_csv_fields() -> list[dict[str, object]]:
    """The FY2024 Summary CSV of b67bfdee: states ``period_type: annual`` and
    a handful of totals; no occupancy, ADR or RevPAR."""
    return [
        {"field_name": "p_and_l_usali.period_type", "value": "annual"},
        {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_900_000.0},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_300_000.0},
        {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 320_000.0},
        {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_520_000.0},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 900_000.0},
    ]


async def _insert_b67bfdee(deal_id: UUID) -> tuple[UUID, UUID]:
    base = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    # The ``documents`` columns as prod holds them: the Detailed P&L dates
    # itself by ``extracted_period_year``; the CSV also by ``report_as_of``.
    detailed = await _insert_statement(
        deal_id, doc_type="PNL", filename=_DETAILED_PNL, extracted_period_year=2024, ts=base,
        fields=detailed_pnl_fields(), structural_pnl_score=1.0,
    )
    summary = await _insert_statement(
        deal_id, doc_type="PNL", filename=_SUMMARY_CSV, ts=base + timedelta(days=1),
        extracted_period_year=2024, report_as_of="2024-12-31",
        fields=summary_csv_fields(), structural_pnl_score=1.0,
    )
    return detailed, summary


async def _insert_7a9928e0(deal_id: UUID) -> tuple[UUID, UUID]:
    base = datetime(2026, 9, 20, tzinfo=UTC)
    t12 = await _insert_statement(
        deal_id, doc_type="T12", filename="Angler_s 2024 Full Year Detailed P&L.xlsm",
        fiscal_year=2024, ts=base, structural_pnl_score=1.0,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.8326},
            {"field_name": "adr_usd", "value": 236.09},
            {"field_name": "p_and_l_usali.prior_year.rooms_revenue", "value": 9_807_990.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 96_528.1},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_839_200.0},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_429_573.0},
        ],
    )
    dec = await _insert_statement(
        deal_id, doc_type="PNL", filename="The Angler_s - December 2024 Financials.xlsx",
        fiscal_year=2024, ts=base + timedelta(minutes=1), structural_pnl_score=1.0,
        fields=[
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_496_410.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_737_410.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 18_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_328_280.0},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 13_580_100.0},
        ],
    )
    return t12, dec


async def _list(deal_id: UUID):
    from app.api.documents import list_documents
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        return await list_documents(deal_id, session, UUID(_TENANT))


async def _loader_primary(deal_id: UUID) -> str | None:
    """The document the engine's revenue loader grounds Year-1 on."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_revenue_actuals

    async with get_session_factory()() as session:
        _actuals, prov = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
    rooms = prov.get("rooms_revenue")
    return str(rooms.document_id) if rooms is not None else None


def _badged(records) -> list[str]:
    return [str(r.id) for r in records if r.primary_financial_source]


# (1) ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_badge_is_the_detailed_pnl_the_loader_uses_on_b67bfdee() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    detailed, summary = await _insert_b67bfdee(deal_id)

    records = await _list(deal_id)
    assert _badged(records) == [str(detailed)]
    assert await _loader_primary(deal_id) == str(detailed)
    by_id = {str(r.id): r for r in records}
    assert by_id[str(detailed)].statement_role == "year_one_primary"
    assert by_id[str(summary)].statement_role == "supplemental"
    assert by_id[str(summary)].period_primary_document_id == detailed
    assert by_id[str(summary)].statement_period == by_id[str(detailed)].statement_period


# (2) ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_badge_follows_the_footing_switch_on_7a9928e0() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    t12, dec = await _insert_7a9928e0(deal_id)

    records = await _list(deal_id)
    assert _badged(records) == [str(dec)], "the footing statement, not the top-ranked T-12"
    assert await _loader_primary(deal_id) == str(dec)
    by_id = {str(r.id): r for r in records}
    assert by_id[str(t12)].statement_role == "supplemental"
    assert by_id[str(t12)].period_primary_document_id == dec


# (3) ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ledger_link_fallback_names_the_year_one_primary() -> None:
    """A t12_actual key with no ``source_fields`` row links to the statement
    the model uses — the Detailed P&L — not the CSV that states "annual"."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_source_documents
    from app.services.engine_runner import SOURCE_T12_ACTUAL

    deal_id = uuid4()
    await _insert_deal(deal_id)
    detailed, _summary = await _insert_b67bfdee(deal_id)
    async with get_session_factory()() as session:
        links = await _load_source_documents(
            session,
            deal_id=str(deal_id),
            sources={"keyless_t12_line": SOURCE_T12_ACTUAL},
            tenant_id=_TENANT,
        )
    assert links == {"keyless_t12_line": str(detailed)}


# (4) ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_every_financial_document_carries_its_role_and_period_primary() -> None:
    """FY2024 (two statements) is the Year-1 basis; FY2023 also has two, and
    its own chosen statement is the ``period_primary``. A P&L-typed document
    with no extraction is not a statement; a non-financial one has no role."""
    deal_id = uuid4()
    await _insert_deal(deal_id)
    detailed, summary = await _insert_b67bfdee(deal_id)
    base = datetime(2026, 10, 1, tzinfo=UTC)
    fy23_full = await _insert_statement(
        deal_id, doc_type="PNL", filename="Angler_s 2023 P&L.xlsx", fiscal_year=2023, ts=base,
        fields=detailed_pnl_fields(detail_lines=100), structural_pnl_score=1.0,
    )
    fy23_summary = await _insert_statement(
        deal_id, doc_type="PNL", filename="Angler 2023 summary.csv", fiscal_year=2023,
        ts=base + timedelta(minutes=5), fields=summary_csv_fields()[2:], structural_pnl_score=1.0,
    )
    pending = await _insert_document_only(deal_id, doc_type="PNL", filename="pending.xlsx", ts=base)
    om = await _insert_document_only(deal_id, doc_type="OM", filename="OM.pdf", ts=base)

    by_id = {str(r.id): r for r in await _list(deal_id)}
    assert by_id[str(detailed)].statement_role == "year_one_primary"
    assert by_id[str(summary)].statement_role == "supplemental"
    assert by_id[str(fy23_full)].statement_role == "period_primary"
    assert by_id[str(fy23_full)].primary_financial_source is False
    assert by_id[str(fy23_summary)].statement_role == "supplemental"
    assert by_id[str(fy23_summary)].period_primary_document_id == fy23_full
    assert by_id[str(fy23_full)].period_primary_document_id == fy23_full
    assert by_id[str(fy23_full)].statement_period_words == "FY2023"
    assert by_id[str(fy23_full)].statement_period != by_id[str(detailed)].statement_period
    assert by_id[str(fy23_full)].statement_period_rank == 0
    assert by_id[str(fy23_summary)].statement_period_rank == 1
    for doc in (pending, om):
        assert by_id[str(doc)].statement_role is None
        assert by_id[str(doc)].primary_financial_source is False
    assert sum(r.primary_financial_source for r in by_id.values()) == 1


_LOAN_FIELDS: list[dict[str, object]] = [
    {"field_name": "debt.loan_amount", "value": 25_000_000.0},
    {"field_name": "debt.interest_rate", "value": 0.065},
    {"field_name": "debt.amortization_years", "value": 30},
    {"field_name": "debt.term_years", "value": 5},
]


@pytest.mark.asyncio
async def test_a_debt_document_typed_as_a_pnl_is_not_a_statement() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    detailed, _summary = await _insert_b67bfdee(deal_id)
    loan = await _insert_statement(
        deal_id, doc_type="T12", filename="Loan terms.pdf",
        ts=datetime(2026, 10, 25, tzinfo=UTC),
        fields=_LOAN_FIELDS,
    )
    from app.extraction.debt_terms import is_debt_terms_extraction

    assert is_debt_terms_extraction(_LOAN_FIELDS)
    by_id = {str(r.id): r for r in await _list(deal_id)}
    assert by_id[str(loan)].statement_role == "not_a_statement"
    assert _badged(by_id.values()) == [str(detailed)]


@pytest.mark.asyncio
async def test_badge_falls_back_to_the_shared_key_when_rows_cannot_be_read(monkeypatch) -> None:
    """The badge never disappears: when the statement rows cannot be read the
    Data Room ranks on the shared key with the ``documents`` columns only —
    never on ``structural_pnl_score``."""
    import app.services.engine_runner as er

    deal_id = uuid4()
    await _insert_deal(deal_id)
    detailed, summary = await _insert_b67bfdee(deal_id)

    async def _boom(*_a, **_k):
        raise RuntimeError("extraction rows unavailable")

    monkeypatch.setattr(er, "_read_pnl_shims", _boom)
    records = await _list(deal_id)
    assert len(_badged(records)) == 1
    assert all(r.statement_role is None for r in records)


# ── Round 2 / I2 — variance, IC memo and dossier actuals use the Year-1 basis ──
#
# Live (read-only, 2026-11-03): on 2e1db521 the broker-vs-actual NOI read the
# 2019 P&L's before-reserve NOI, on 76d10a64 the 2023 P&L's — while the model
# grounds Year-1 on the FY2024 Detailed P&L. The actuals side is the engine's
# Year-1 basis statement and nothing else.


async def _insert_variance_deal(deal_id: UUID) -> tuple[UUID, UUID]:
    """FY2024 Detailed P&L (Year-1 basis, uploaded first) and a 2019 P&L
    uploaded later; both state rooms revenue and a before-reserve NOI."""
    base = datetime(2026, 8, 1, tzinfo=UTC)
    fy24 = await _insert_statement(
        deal_id, doc_type="PNL", filename=_DETAILED_PNL, extracted_period_year=2024, ts=base,
        fields=[
            # The real Detailed P&L lists a cost-of-services sub-line named
            # like rooms revenue ($552.77) before its operating-revenue line.
            {
                "field_name": "p_and_l_usali.minor_operated_departments.cost_of_services.rooms_revenue_usd",
                "value": 552.774,
            },
            *detailed_pnl_fields(detail_lines=50),
            {"field_name": "p_and_l_usali.net_operating_income.ebitda", "value": 2_500_000.0},
        ],
    )
    fy19 = await _insert_statement(
        deal_id, doc_type="PNL", filename="Copy of Angler_s 2019 P&L.xlsx", extracted_period_year=2019,
        ts=base + timedelta(days=10),
        fields=[
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 7_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.net_operating_income.ebitda", "value": 1_166_000.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 250_000.0},
        ],
    )
    return fy24, fy19


@pytest.mark.asyncio
async def test_broker_comparison_noi_is_the_year_one_basis_statements() -> None:
    from app.api.analysis import load_actuals_before_reserve_noi
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_variance_deal(deal_id)
    async with get_session_factory()() as session:
        noi = await load_actuals_before_reserve_noi(session, deal_id=deal_id, tenant_id=_TENANT)
    assert noi == 2_500_000.0


@pytest.mark.asyncio
async def test_critic_actuals_come_from_the_year_one_basis_statement_only() -> None:
    """No line is blended in from another statement: the 2019 P&L's rooms
    revenue and utilities never reach the actual side. And, as in the loader,
    a revenue line is never read out of a cost section."""
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_variance_deal(deal_id)
    async with get_session_factory()() as session:
        _broker, actuals, _ctx, _keys = await _load_critic_inputs(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )
    assert actuals is not None
    assert actuals.rooms_revenue == pytest.approx(9_496_407.22)
    assert actuals.total_revenue == pytest.approx(13_481_730.29)
    assert actuals.undistributed.utilities in (None, 0, 0.0)


@pytest.mark.asyncio
async def test_no_actuals_side_without_a_statement() -> None:
    from app.api.analysis import load_actuals_before_reserve_noi
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)
    async with get_session_factory()() as session:
        assert await load_actuals_before_reserve_noi(session, deal_id=deal_id, tenant_id=_TENANT) is None
        _b, actuals, _c, _k = await _load_critic_inputs(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert actuals is None


def test_period_words_name_a_december_year_end_as_its_fiscal_year() -> None:
    """M5: a full year stated to end Dec 31 reads "FY2023" — the same words as
    one dated only by its fiscal year — while any other year end is spelled
    out (never an invented FY)."""
    from app.services.engine_runner import statement_roles_from_shims

    rooms = {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0}
    shims = [
        {"document_id": "dec", "doc_type": "PNL", "filename": "dec.xlsx",
         "fields": [rooms, {"field_name": "p_and_l_usali.period_ending", "value": "2023-12-31"}]},
        {"document_id": "jun", "doc_type": "T12", "filename": "jun.xlsx",
         "fields": [rooms, {"field_name": "p_and_l_usali.period_ending", "value": "2025-06-30"}]},
        {"document_id": "fy", "doc_type": "PNL", "filename": "fy.xlsx", "fields": [rooms], "fiscal_year": 2019},
    ]
    roles = statement_roles_from_shims(shims)
    assert roles["dec"].period_words == "FY2023"
    assert roles["jun"].period_words == "the year ending 2025-06-30"
    assert roles["fy"].period_words == "FY2019"


@pytest.mark.asyncio
async def test_list_endpoint_reads_the_statement_rows_once_while_the_deal_is_unchanged(monkeypatch) -> None:
    """M2: the Data Room polls the list every 2 s while documents process; the
    statement roles are served from the deal-state cache until an extraction,
    document or override changes — and a new statement re-ranks at once."""
    import app.services.engine_runner as er
    from app.services.read_cache import reset_read_cache

    reset_read_cache()
    calls = {"n": 0}
    real = er._read_pnl_shims

    async def _counting(*a, **k):
        calls["n"] += 1
        return await real(*a, **k)

    monkeypatch.setattr(er, "_read_pnl_shims", _counting)
    deal_id = uuid4()
    await _insert_deal(deal_id)
    _detailed, summary = await _insert_b67bfdee(deal_id)
    await _list(deal_id)
    await _list(deal_id)
    assert calls["n"] == 1
    # A newer, more complete FY2024 statement: the badge moves on the next list.
    newer = await _insert_statement(
        deal_id, doc_type="PNL", filename="FY2024 restated.xlsx", extracted_period_year=2024,
        ts=datetime(2026, 10, 30, tzinfo=UTC), fields=detailed_pnl_fields(detail_lines=1_500),
    )
    records = await _list(deal_id)
    assert calls["n"] == 2
    assert _badged(records) == [str(newer)]


# ── Round 3 — the actual side IS the engine's Year-1 lines; absent stays absent ──
#
# Live (2e1db521 / 76d10a64, read-only 2026-11-03): inside the Year-1 basis
# statement the critic's own resolver read ``p_and_l_usali.budget.rooms_revenue``
# (9,985,570) as the "T-12 actual" rooms revenue and ``operating_revenue
# .total_revenue`` (10,839,200, the HR-sheet figure) as total revenue, while the
# engine reads rooms 9,496,400. And with no NOI on the statement, ``_build``
# printed "NOI: $0" / "OpEx Ratio: 100%" into the critic and analyst prompts.

_BASIS_2E1 = "Copy of Angler_s 2024 Full Year Detailed P&L.xlsm"


def _basis_2e1_fields() -> list[dict[str, object]]:
    return [
        {"field_name": "p_and_l_usali.budget.rooms_revenue", "value": 9_985_570.0},
        {"field_name": "p_and_l_usali.prior_year.rooms_revenue", "value": 9_807_990.0},
        {"field_name": "p_and_l_usali.forecast.total_revenue", "value": 11_239_200.0},
        {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_839_200.0},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_496_400.0},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_739_040.0},
        {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_228_476.0},
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.832567},
        {"field_name": "ttm_performance.subject.adr_usd", "value": 236.094},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_160_000.0},
        {"field_name": "p_and_l_usali.budget.insurance", "value": 2_000_000.0},
    ]


async def _insert_om(deal_id: UUID) -> None:
    await _insert_statement(
        deal_id, doc_type="OM", filename="Offering Memorandum.pdf",
        ts=datetime(2026, 7, 1, tzinfo=UTC),
        fields=[
            {"field_name": "ttm_summary_per_om.noi_usd", "value": 3_356_709.0, "source_page": 14, "confidence": 0.8},
            {"field_name": "ttm_summary_per_om.rooms_revenue_usd", "value": 9_541_537.0, "source_page": 14, "confidence": 0.8},
            {"field_name": "ttm_summary_per_om.total_revenue_usd", "value": 15_926_405.0, "source_page": 14, "confidence": 0.8},
        ],
    )


async def _insert_2e1(deal_id: UUID) -> UUID:
    return await _insert_statement(
        deal_id, doc_type="PNL", filename=_BASIS_2E1, extracted_period_year=2024,
        ts=datetime(2026, 8, 1, tzinfo=UTC), fields=_basis_2e1_fields(),
    )


@pytest.mark.asyncio
async def test_critic_actuals_equal_the_engines_year_one_lines() -> None:
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_expense_actuals, _load_t12_revenue_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_2e1(deal_id)
    async with get_session_factory()() as session:
        _b, actuals, _c, _k = await _load_critic_inputs(session, deal_id=str(deal_id), tenant_id=_TENANT)
        rev = await _load_t12_revenue_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
        exp = await _load_t12_expense_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert actuals is not None
    assert rev["rooms_revenue"] == 9_496_400.0
    assert actuals.rooms_revenue == rev["rooms_revenue"]  # never the budget line
    assert actuals.fb_revenue == rev["fb_revenue"]
    assert actuals.other_revenue == pytest.approx(rev.get("other_revenue", 0.0) + rev["misc_revenue"])
    # The statement's stated total (10,839,200, the HR-sheet figure) does not
    # foot to its own lines, and its forecast total is never read: total
    # revenue is not stated (the schema slot carries the sum of the lines,
    # listed as not stated, so nothing compares or prints it).
    assert "total_revenue" in actuals.not_stated
    assert actuals.total_revenue == pytest.approx(9_496_400.0 + 2_739_040.0 + 1_228_476.0)
    assert actuals.occupancy == pytest.approx(rev["occupancy"])
    assert actuals.adr == pytest.approx(rev["adr"])
    assert actuals.fixed_charges.insurance == exp["insurance"] == 1_160_000.0
    assert actuals.source_statement == _BASIS_2E1


@pytest.mark.asyncio
async def test_an_absent_actual_stays_absent_and_prints_not_stated() -> None:
    from app.agents.analyst import _format_spread as analyst_spread
    from app.agents.critic import _format_financials
    from app.agents.researcher import _format_spread as researcher_spread
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_2e1(deal_id)
    async with get_session_factory()() as session:
        _b, actuals, _c, _k = await _load_critic_inputs(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert actuals is not None
    for path in ("noi", "gop", "opex_ratio", "total_revenue", "mgmt_fee", "ffe_reserve", "dept_expenses.total"):
        assert path in actuals.not_stated, path
    for text_ in (
        _format_financials("ACTUALS", actuals),
        analyst_spread(actuals),
        researcher_spread(actuals.model_dump(mode="json")),
    ):
        assert "$0" not in text_ and "100.00%" not in text_, text_
        assert "NOI" in text_ or "noi" in text_
        assert "not stated" in text_


@pytest.mark.asyncio
async def test_noi_refusal_names_the_statement_the_model_uses() -> None:
    from app.api.analysis import _compute_variance
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_2e1(deal_id)
    await _insert_om(deal_id)
    async with get_session_factory()() as session:
        report = await _compute_variance(session, deal_id=deal_id, tenant_id=UUID(_TENANT))
    details = [r.detail for r in report.reasons]
    noi = [d for d in details if "NOI" in d]
    assert noi, details
    assert any(_BASIS_2E1 in d and "states no NOI before" in d for d in noi), noi
    assert not any("T-12" in d for d in noi), noi
    # The HR-sheet total its own lines exceed is not an actual: the refusal
    # names the statement too, in plain words.
    total = [d for d in details if "total revenue" in d and _BASIS_2E1 in d]
    assert total == [
        f"{_BASIS_2E1}, the statement the model uses, states no total revenue that its own "
        "revenue lines bear out — the broker's figure has nothing to be compared against"
    ], details


def test_reserve_basis_reason_names_the_statement() -> None:
    from app.agents.variance import RESERVE_BASIS_UNMATCHED, reserve_basis_reason

    text_ = reserve_basis_reason("noi", 3_356_709.0, 1_560_478.0, statement="FY2024 P&L.xlsx")
    assert "FY2024 P&L.xlsx" in text_ and "T-12" not in text_
    assert text_.endswith(RESERVE_BASIS_UNMATCHED)


def _basis_2e1_live_shape() -> list[dict[str, object]]:
    """The live 2e1db521 / 76d10a64 basis: NO full-period rooms revenue line —
    only budget / prior-year ones and segment splits — plus the HR-sheet total."""
    return [
        {"field_name": "p_and_l_usali.prior_year.rooms_revenue", "value": 9_807_990.0},
        {"field_name": "p_and_l_usali.budget.rooms_revenue", "value": 9_985_570.0},
        {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_839_200.0},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_739_040.0},
        {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_228_476.0},
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.832567},
        {"field_name": "ttm_performance.subject.adr_usd", "value": 236.094},
        {"field_name": "ttm_performance.subject.revpar_usd", "value": 196.564},
    ]


@pytest.mark.asyncio
async def test_without_a_full_period_rooms_line_rooms_is_derived_never_the_budget_or_prior_year() -> None:
    """2e1db521 / 76d10a64 live: rooms revenue is RevPAR × keys × days of the
    period (9.47M-9.50M), labelled derived — never the budget
    (9,985,570) or prior-year (9,807,990) line. With it, the statement's
    revenue lines exceed its 10,839,200 HR-sheet total, which is therefore not
    an actual."""
    from app.agents.critic import _format_financials
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)  # 132 keys
    await _insert_statement(
        deal_id, doc_type="PNL", filename=_BASIS_2E1, extracted_period_year=2024,
        ts=datetime(2026, 8, 1, tzinfo=UTC), fields=_basis_2e1_live_shape(),
    )
    async with get_session_factory()() as session:
        _b, actuals, _c, _k = await _load_critic_inputs(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert actuals is not None
    # FY2024 whose year end is not known to be Dec 31: 365 days (366 only for
    # a period known to end Dec 31 of a leap year).
    assert actuals.rooms_revenue == pytest.approx(196.564 * 132 * 365)
    assert 9_400_000 < actuals.rooms_revenue < 9_500_000
    assert "rooms_revenue" in actuals.derived and "rooms_revenue" not in actuals.not_stated
    assert "total_revenue" in actuals.not_stated
    text_ = _format_financials("ACTUALS", actuals)
    assert "derived:" in text_ and "10,839,200" not in text_ and "9,985,570" not in text_
