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
