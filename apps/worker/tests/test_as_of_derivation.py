"""Phase 2.2 — ``services.as_of.derive_report_as_of``.

Every document gets an as-of date derived from what it STATES, plus a
precision saying how much of that date was stated. The contract these
tests pin:

* one case per real shape on this corpus, using the field paths the
  extraction schemas at ``app/agents/extraction_schemas/*.md`` specify
  and the values the stored prod payloads at
  ``tests/fixtures/real_payloads/`` actually carry:
    - ``str_trend.report_year``            → 31 Dec, precision ``year``
    - ``cbre_horizons.publication_date``   → quarter end, ``quarter``
    - the P&L family's ``period_ending``   → the date, ``day``
    - the OM's newest ``transaction_comps.<n>.sale_date``
* a document that states nothing → ``(None, None)``
* a malformed date → ``(None, None)`` and NO exception. Dating is
  additive intelligence on top of extraction; it must never be able to
  fail an upload.

The one rule under test throughout: never guess a date.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app.services.as_of import derive_report_as_of

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_payloads"


def _fields(*pairs: tuple[str, object]) -> list[dict[str, object]]:
    """Build the extractor's flat payload shape from (path, value) pairs."""
    return [{"field_name": name, "value": value} for name, value in pairs]


# ────────────────────────── STR family → year ──────────────────────────


def test_str_trend_report_year_dates_to_december_31() -> None:
    """``str_trend.report_year`` is a bare year — 31 December, precision
    ``year``. The STR Trend report never states a day, so claiming one
    would be a guess (str_trend.md: "integer year the STR report covers")."""
    fields = _fields(
        ("str_trend.report_year", 2024),
        ("ttm_performance.subject.adr_usd", 233.45),
    )
    assert derive_report_as_of(fields, "STR_TREND") == (date(2024, 12, 31), "year")


def test_str_segmentation_report_year() -> None:
    """``str_segmentation.report_year`` is the STR_FAMILY sibling alias
    the registry lists on the same ``str_report_year`` concept."""
    fields = _fields(("str_segmentation.report_year", "2023"))
    assert derive_report_as_of(fields, "STR_SEGMENTATION") == (
        date(2023, 12, 31),
        "year",
    )


def test_legacy_str_with_no_date_stays_none() -> None:
    """The legacy single-tab STR report (str.md) carries no date field at
    all — it stays undated rather than borrowing one."""
    fields = _fields(
        ("ttm_performance.subject.occupancy_pct", 0.83),
        ("ttm_performance.indices.rgi_revpar_index", 1.02),
        ("comp_set.comp_set_size", 5),
    )
    assert derive_report_as_of(fields, "STR") == (None, None)


# ─────────────────────── CBRE Horizons → quarter ───────────────────────


def test_cbre_publication_date_quarter() -> None:
    """``cbre_horizons.publication_date`` = "Q3 2024" → 30 Sep 2024,
    precision ``quarter`` (cbre_horizons.md: "quarter + year")."""
    fields = _fields(
        ("cbre_horizons.publication_date", "Q3 2024"),
        ("cbre_horizons.market", "Seattle, WA"),
    )
    assert derive_report_as_of(fields, "CBRE_HORIZONS") == (
        date(2024, 9, 30),
        "quarter",
    )


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        ("Q1 2025", date(2025, 3, 31)),
        ("Q2 2025", date(2025, 6, 30)),
        ("Q4 2024", date(2024, 12, 31)),
        ("2024 Q3", date(2024, 9, 30)),
        ("q3 2024", date(2024, 9, 30)),
    ],
)
def test_cbre_quarter_variants(stated: str, expected: date) -> None:
    """Each quarter resolves to its own last day, in either word order
    and either case."""
    fields = _fields(("cbre_horizons.publication_date", stated))
    assert derive_report_as_of(fields, "CBRE_HORIZONS") == (expected, "quarter")


def test_cbre_publication_date_iso_is_day_precision() -> None:
    """cbre_horizons.md allows an ISO date instead of a quarter — then
    the precision really is ``day``."""
    fields = _fields(("cbre_horizons.publication_date", "2024-09-12"))
    assert derive_report_as_of(fields, "CBRE_HORIZONS") == (date(2024, 9, 12), "day")


# ────────────────────────── P&L family → day ───────────────────────────


def test_pnl_period_ending_day_precision() -> None:
    """``p_and_l_usali.period_ending`` is the T-12 schema's MANDATORY
    period field — a full date, precision ``day``."""
    fields = _fields(
        ("p_and_l_usali.period_ending", "2025-05-31"),
        ("p_and_l_usali.net_operating_income.noi_usd", 4_970_460),
    )
    assert derive_report_as_of(fields, "T12") == (date(2025, 5, 31), "day")


def test_pnl_period_ending_outranks_statement_period() -> None:
    """Both can sit in the SAME payload (the real Angler's T-12 carries
    ``period_ending=2025-05-31`` AND ``statement_period=2025-12-31``).
    The registry lists the schema-canonical PNL_FAMILY alias first, so
    the statement's own period end wins over the generic fallback."""
    fields = _fields(
        ("property_overview.statement_period", "2025-12-31"),
        ("p_and_l_usali.period_ending", "2025-05-31"),
    )
    assert derive_report_as_of(fields, "T12") == (date(2025, 5, 31), "day")


def test_pnl_statement_period_end_fallback() -> None:
    """``property_overview.statement_period_end`` is the registry's
    any-document alias — used when the canonical path is absent."""
    fields = _fields(("property_overview.statement_period_end", "2024-12-31"))
    assert derive_report_as_of(fields, "PNL") == (date(2024, 12, 31), "day")


def test_pnl_statement_period_fallback() -> None:
    fields = _fields(("property_overview.statement_period", "2023-12-31"))
    assert derive_report_as_of(fields, "PNL") == (date(2023, 12, 31), "day")


def test_pnl_month_only_is_month_precision() -> None:
    """A statement that names only a month resolves to that month's last
    day with precision ``month`` — the day was never stated."""
    fields = _fields(("p_and_l_usali.period_ending", "2025-05"))
    assert derive_report_as_of(fields, "PNL_MONTHLY") == (date(2025, 5, 31), "month")


def test_pnl_month_name_is_month_precision() -> None:
    fields = _fields(("p_and_l_usali.period_ending", "May 2025"))
    assert derive_report_as_of(fields, "PNL_MONTHLY") == (date(2025, 5, 31), "month")


def test_pnl_us_date_format() -> None:
    """Scanned P&Ls surface US-formatted dates; the shared parser in
    ``coverage_audit`` already handles them."""
    fields = _fields(("p_and_l_usali.period_ending", "05/31/2025"))
    assert derive_report_as_of(fields, "T12") == (date(2025, 5, 31), "day")


def test_pnl_accepts_date_objects() -> None:
    """A payload that already carries ``datetime``/``date`` objects (a
    parsed workbook cell) is dated the same way."""
    assert derive_report_as_of(
        _fields(("p_and_l_usali.period_ending", date(2025, 5, 31))), "T12"
    ) == (date(2025, 5, 31), "day")
    assert derive_report_as_of(
        _fields(("p_and_l_usali.period_ending", datetime(2025, 5, 31, 12, 0))), "T12"
    ) == (date(2025, 5, 31), "day")


# ───────────────────────────── OM → comps ──────────────────────────────


def test_om_newest_transaction_comp_wins() -> None:
    """The OM's Comparable Sales table dates the OM: the NEWEST sale the
    broker chose to print is the freshest fact the document states
    (om.md: ``transaction_comps.<n>.sale_date``)."""
    fields = _fields(
        ("transaction_comps.1.sale_date", "2024-08-15"),
        ("transaction_comps.2.sale_date", "2025-02-03"),
        ("transaction_comps.3.sale_date", "2023-11-30"),
        ("broker_proforma.noi_usd", 6_000_000),
    )
    assert derive_report_as_of(fields, "OM") == (date(2025, 2, 3), "day")


def test_om_falls_back_to_its_own_period_end() -> None:
    """An OM with no comp dates falls back to the period end of its own
    historical / TTM operating statement."""
    fields = _fields(
        ("transaction_comps.1.name", "Some Hotel"),
        ("ttm_summary_per_om.period_ending", "2024-12-31"),
        ("broker_proforma.noi_usd", 6_000_000),
    )
    assert derive_report_as_of(fields, "OM") == (date(2024, 12, 31), "day")


def test_om_ignores_the_in_place_debt_maturity_date() -> None:
    """``in_place_debt.maturity_date`` is a FUTURE date about the
    seller's loan, not a statement of when the OM is current. An OM
    carrying only that stays undated."""
    fields = _fields(
        ("in_place_debt.maturity_date", "2029-06-01"),
        ("property_overview.keys", 132),
    )
    assert derive_report_as_of(fields, "OM") == (None, None)


def test_om_with_no_dates_stays_none() -> None:
    fields = _fields(
        ("property_overview.keys", 132),
        ("asking_price.headline_price_usd", 79_000_000),
    )
    assert derive_report_as_of(fields, "OM") == (None, None)


def test_om_history_year_is_the_last_resort() -> None:
    """The shape the LIVE Angler's OM actually extracts to: no comp
    dates, no period field, just a year-tagged history block (page 40's
    "Year Ended December 31, 2024" column → ``om_history.2024.*``).
    The newest history year dates the OM, precision ``year``."""
    fields = _fields(
        ("om_history.2022.noi_usd", 4_100_000),
        ("om_history.2023.noi_usd", 4_600_000),
        ("om_history.2024.noi_usd", 5_100_000),
        ("ttm_summary_per_om.noi_usd", 5_100_000),
        ("property_overview.keys", 132),
    )
    assert derive_report_as_of(fields, "OM") == (date(2024, 12, 31), "year")


def test_om_history_year_never_reads_the_broker_proforma() -> None:
    """The Angler's OM pro forma runs 2025-2029 and is year-tagged the
    same way. Dating the OM by a PROJECTION would push its as-of into
    the future — the exact failure this module exists to prevent."""
    fields = _fields(
        ("broker_proforma.2025.noi_usd", 5_500_000),
        ("broker_proforma.2029.noi_usd", 7_200_000),
        ("property_overview.keys", 132),
    )
    assert derive_report_as_of(fields, "OM") == (None, None)


def test_om_stated_period_end_outranks_the_history_year() -> None:
    """A stated period end is more precise than a year-tagged namespace,
    so it wins even when a later history year exists."""
    fields = _fields(
        ("om_history.2024.noi_usd", 5_100_000),
        ("ttm_summary_per_om.period_ending", "2024-11-30"),
    )
    assert derive_report_as_of(fields, "OM") == (date(2024, 11, 30), "day")


def test_om_comps_outrank_the_history_year() -> None:
    fields = _fields(
        ("om_history.2024.noi_usd", 5_100_000),
        ("transaction_comps.1.sale_date", "2025-02-03"),
    )
    assert derive_report_as_of(fields, "OM") == (date(2025, 2, 3), "day")


def test_om_history_year_is_om_only() -> None:
    """A year-tagged history namespace on a non-OM document is not an
    as-of source — only the OM resolver reaches for it."""
    fields = _fields(("om_history.2024.noi_usd", 5_100_000))
    assert derive_report_as_of(fields, "T12") == (None, None)
    assert derive_report_as_of(fields, "PROPERTY_INFO") == (None, None)


# ───────────────────── nothing stated / malformed ──────────────────────


def test_nothing_stated_returns_none_none() -> None:
    """A document that states no date at all is undated. This is the
    rule the whole module exists to keep: never guess."""
    fields = _fields(
        ("p_and_l_usali.operating_revenue.rooms_revenue", 8_000_000),
        ("p_and_l_usali.undistributed.utilities", 400_000),
        ("occupancy_pct", 0.83),
    )
    assert derive_report_as_of(fields, "T12") == (None, None)


def test_empty_payload_returns_none_none() -> None:
    assert derive_report_as_of([], "T12") == (None, None)
    assert derive_report_as_of(None, "T12") == (None, None)
    assert derive_report_as_of({}, "OM") == (None, None)


@pytest.mark.parametrize(
    "junk",
    [
        "not a date",
        "TBD",
        "",
        "   ",
        "13/45/9999",
        "Q9 2024",
        "1823-01-01T",
        "period ending soon",
        1234,
        0,
        -1,
        3.14,
        True,
        None,
        [],
        {"nested": "object"},
        object(),
    ],
)
def test_malformed_date_returns_none_none_without_raising(junk: object) -> None:
    """A malformed / nonsense period value yields ``(None, None)`` and
    never raises. An unreadable date must not be able to fail an
    upload — the whole derivation is best-effort."""
    fields = _fields(("p_and_l_usali.period_ending", junk))
    assert derive_report_as_of(fields, "T12") == (None, None)


def test_malformed_date_does_not_block_a_readable_sibling() -> None:
    """When one period field is junk and another is readable, the
    readable one still dates the document."""
    fields = _fields(
        ("p_and_l_usali.period_ending", "see cover page"),
        ("property_overview.statement_period", "2024-12-31"),
    )
    assert derive_report_as_of(fields, "T12") == (date(2024, 12, 31), "day")


def test_malformed_payload_shapes_do_not_raise() -> None:
    """Defensive: a payload that is not the expected list-of-dicts shape
    is undated, not an exception."""
    for junk in ("a string", 42, {"field_name"}, [None], [42], [{"value": 1}]):
        assert derive_report_as_of(junk, "T12") == (None, None)


def test_out_of_range_year_is_not_a_date() -> None:
    """A stray numeric that happens to sit in a year-shaped field is
    rejected outside [1900, 2100] rather than becoming a date."""
    assert derive_report_as_of(
        _fields(("str_trend.report_year", 56387)), "STR_TREND"
    ) == (None, None)


# ───────────────────────── doc-type handling ───────────────────────────


def test_doc_type_is_case_insensitive() -> None:
    fields = _fields(("p_and_l_usali.period_ending", "2025-05-31"))
    assert derive_report_as_of(fields, "t12") == (date(2025, 5, 31), "day")
    assert derive_report_as_of(fields, " T12 ") == (date(2025, 5, 31), "day")


def test_unknown_doc_type_still_reads_a_stated_period_end() -> None:
    """An INSURANCE / CAPEX / PROPERTY_INFO document that happens to
    state a period end is dated by it; the generic pass is driven by
    the registry's any-document ``period_ending`` aliases."""
    fields = _fields(("period_ending", "2024-12-31"))
    assert derive_report_as_of(fields, "INSURANCE") == (date(2024, 12, 31), "day")
    assert derive_report_as_of(fields, None) == (date(2024, 12, 31), "day")


def test_mapping_payload_shape_is_accepted() -> None:
    """``{path: value}`` mappings resolve the same as the extractor's
    list-of-dicts shape."""
    assert derive_report_as_of(
        {"p_and_l_usali.period_ending": "2025-05-31"}, "T12"
    ) == (date(2025, 5, 31), "day")


# ────────────────── real stored prod payloads (corpus) ─────────────────


def test_real_anglers_t12_payload() -> None:
    """The stored Angler's T-12 payload (document 0c8fd0e5, 213 fields)
    carries ``p_and_l_usali.period_ending = 2025-05-31`` alongside
    ``property_overview.statement_period = 2025-12-31``. The T-12's own
    period end is the as-of date."""
    payload = json.loads((_FIXTURES_DIR / "anglers_t12_real.json").read_text())
    assert derive_report_as_of(payload["fields"], "T12") == (
        date(2025, 5, 31),
        "day",
    )


def test_real_anglers_annual_pnl_payload() -> None:
    """The stored annual-P&L payload emits ``p_and_l_usali.period.end_date``
    (not the canonical ``period_ending``) — the same prod variance
    ``coverage_audit`` documents. It still dates to 31 Dec 2023."""
    payload = json.loads((_FIXTURES_DIR / "anglers_annual_pnl_real.json").read_text())
    assert derive_report_as_of(payload["fields"], "PNL") == (
        date(2023, 12, 31),
        "day",
    )


# ─────────────────────────── return contract ───────────────────────────


def test_return_shape_is_always_a_two_tuple_of_matched_optionals() -> None:
    """Both halves are set together or both are ``None`` — a date with
    no precision (or a precision with no date) is never returned."""
    samples: list[tuple[list[dict[str, object]], str]] = [
        (_fields(("str_trend.report_year", 2024)), "STR_TREND"),
        (_fields(("cbre_horizons.publication_date", "Q3 2024")), "CBRE_HORIZONS"),
        (_fields(("p_and_l_usali.period_ending", "2025-05-31")), "T12"),
        (_fields(("transaction_comps.1.sale_date", "2025-02-03")), "OM"),
        (_fields(("property_overview.keys", 132)), "OM"),
        (_fields(("p_and_l_usali.period_ending", "junk")), "T12"),
    ]
    from app.services.as_of import PRECISIONS

    for fields, doc_type in samples:
        as_of, precision = derive_report_as_of(fields, doc_type)
        assert (as_of is None) == (precision is None)
        if as_of is not None:
            assert isinstance(as_of, date)
            assert precision in PRECISIONS


# ───────────────────────── migration + persist ─────────────────────────


async def _seed_doc(doc_type: str) -> tuple[str, str, str]:
    """Insert a minimal deal + documents row; return (deal, doc, tenant)."""
    from uuid import uuid4

    from sqlalchemy import text

    from app.database import get_session_factory

    deal_id, doc_id, tenant_id = str(uuid4()), str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, "
                "created_at, updated_at) "
                "VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)"
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "name": "As-Of Test Hotel",
                "ts": "2026-09-10 00:00:00",
            },
        )
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                "doc_type, status, uploaded_at) "
                "VALUES (:id, :deal, :tenant, :fn, :dt, 'EXTRACTED', :ts)"
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": f"as_of_{doc_type.lower()}.xlsx",
                "dt": doc_type,
                "ts": "2026-09-10 00:00:00",
            },
        )
        await session.commit()
    return deal_id, doc_id, tenant_id


async def test_persist_report_as_of_round_trips() -> None:
    """The Phase 2.2 migration columns exist and the persisted date
    reads back through ``DocumentRecord`` on the dev (SQLite) dialect."""
    from sqlalchemy import text

    from app.api.documents import DocumentRecord, _persist_report_as_of
    from app.database import get_session_factory

    deal_id, doc_id, tenant_id = await _seed_doc("T12")
    factory = get_session_factory()
    async with factory() as session:
        result = await _persist_report_as_of(
            session,
            deal_id=deal_id,
            doc_id=doc_id,
            tenant_id=tenant_id,
            doc_type="T12",
            fields=_fields(("p_and_l_usali.period_ending", "2025-05-31")),
        )
        assert result == (date(2025, 5, 31), "day")

        row = (
            await session.execute(
                text(
                    "SELECT id, deal_id, tenant_id, filename, doc_type, "
                    "status, uploaded_at, report_as_of, report_as_of_precision "
                    "FROM documents WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": doc_id, "tenant": tenant_id},
            )
        ).first()
    assert row is not None
    mapping = dict(row._mapping)
    record = DocumentRecord(
        id=mapping["id"],
        deal_id=mapping["deal_id"],
        tenant_id=mapping["tenant_id"],
        filename=mapping["filename"],
        doc_type=mapping["doc_type"],
        status=mapping["status"],
        uploaded_at=datetime(2026, 9, 10),
        report_as_of=mapping["report_as_of"],
        report_as_of_precision=mapping["report_as_of_precision"],
    )
    assert record.report_as_of == date(2025, 5, 31)
    assert record.report_as_of_precision == "day"


async def test_persist_report_as_of_writes_null_when_nothing_stated() -> None:
    """A document that states no date is persisted as NULL, and a later
    re-derivation that finds nothing CLEARS a previously written date —
    a stale as-of must not survive a re-extraction."""
    from sqlalchemy import text

    from app.api.documents import _persist_report_as_of
    from app.database import get_session_factory

    deal_id, doc_id, tenant_id = await _seed_doc("OM")
    factory = get_session_factory()
    async with factory() as session:
        assert await _persist_report_as_of(
            session,
            deal_id=deal_id,
            doc_id=doc_id,
            tenant_id=tenant_id,
            doc_type="OM",
            fields=_fields(("transaction_comps.1.sale_date", "2025-02-03")),
        ) == (date(2025, 2, 3), "day")

        assert await _persist_report_as_of(
            session,
            deal_id=deal_id,
            doc_id=doc_id,
            tenant_id=tenant_id,
            doc_type="OM",
            fields=_fields(("property_overview.keys", 132)),
        ) == (None, None)

        row = (
            await session.execute(
                text(
                    "SELECT report_as_of, report_as_of_precision FROM documents "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": doc_id, "tenant": tenant_id},
            )
        ).first()
    assert row is not None
    assert row._mapping["report_as_of"] is None
    assert row._mapping["report_as_of_precision"] is None
