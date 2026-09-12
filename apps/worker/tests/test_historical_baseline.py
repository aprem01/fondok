"""Engine + endpoint tests for ``apps/worker/app/engines/historical_baseline.py``.

Pins the Wave 2 P2.6 contract:

* Coverage math (years-with-data / lookback_years) — Sam's ask.
* Gap detection — missing fiscal years between min and max.
* Tie-break on duplicate-year docs — lowest USALI deviation count wins.
* Derived RevPAR (occ × ADR) — institutional shorthand identity.
* YoY walk — sorted by abs(yoy_pct) DESC, 0.5% noise floor.
* Period comparability (FON-44 §4) — the prior is the previous FISCAL
  YEAR, not the previous list element; a partial period, a gap or a
  missing top line refuses the comparison with a reason instead of
  reporting a four-digit percentage.
* Tenant isolation on the GET endpoint — cross-tenant deal returns 404.
* Endpoint happy path returns both baseline + walk.
* Undistributed rollup combines A&G + sales/mkt + utilities + prop_ops + IT.

The first 9 tests hit the pure-function ``build_baseline_from_pnls``
+ ``walk_yoy`` entrypoints. The last 3 tests spin up the FastAPI app
and exercise the GET endpoint end-to-end with a real (SQLite) DB.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN. Same pattern the
# broker-questions tests use.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-historical-baseline.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-historical-baseline-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


from app.engines.historical_baseline import (  # noqa: E402
    HistoricalBaseline,
    build_baseline_from_pnls,
    walk_yoy,
)


TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


# ────────────────────────── helpers ──────────────────────────


def _baseline_fields(
    *,
    rooms_rev: float = 12_000_000.0,
    fb_rev: float = 1_800_000.0,
    other_rev: float = 600_000.0,
    rooms_dept: float = 3_600_000.0,
    fb_dept: float = 1_500_000.0,
    other_dept: float = 250_000.0,
    ag: float = 800_000.0,
    sm: float = 600_000.0,
    util: float = 500_000.0,
    rm: float = 400_000.0,
    it: float = 200_000.0,
    prop_tax: float = 350_000.0,
    insurance: float = 200_000.0,
    mgmt_fee: float = 380_000.0,
    occ: float = 0.74,
    adr: float = 280.0,
) -> list[dict[str, object]]:
    """Build an extraction-results-shaped ``fields`` list with a full
    USALI line-item complement so the rollup synthesis lights up.

    Uses canonical names so the alias map / USALI scorer rollups can
    derive ``total_revenue``, ``gop``, ``undistributed_expenses``,
    ``noi``.
    """
    return [
        {"field_name": "rooms_revenue", "value": rooms_rev,
         "source_page": 1, "confidence": 0.95},
        {"field_name": "fb_revenue", "value": fb_rev,
         "source_page": 1, "confidence": 0.95},
        {"field_name": "other_revenue", "value": other_rev,
         "source_page": 1, "confidence": 0.9},
        {"field_name": "rooms_dept_expense", "value": rooms_dept,
         "source_page": 1, "confidence": 0.95},
        {"field_name": "fb_dept_expense", "value": fb_dept,
         "source_page": 1, "confidence": 0.95},
        {"field_name": "other_dept_expense", "value": other_dept,
         "source_page": 1, "confidence": 0.9},
        # Undistributed components — five buckets.
        {"field_name": "ag_expense", "value": ag,
         "source_page": 2, "confidence": 0.9},
        {"field_name": "marketing_expense", "value": sm,
         "source_page": 2, "confidence": 0.9},
        {"field_name": "utilities_expense", "value": util,
         "source_page": 2, "confidence": 0.9},
        {"field_name": "rm_expense", "value": rm,
         "source_page": 2, "confidence": 0.9},
        {"field_name": "information_telecom", "value": it,
         "source_page": 2, "confidence": 0.9},
        # Fixed-block components.
        {"field_name": "property_tax", "value": prop_tax,
         "source_page": 3, "confidence": 0.9},
        {"field_name": "insurance_expense", "value": insurance,
         "source_page": 3, "confidence": 0.9},
        {"field_name": "mgmt_fee", "value": mgmt_fee,
         "source_page": 3, "confidence": 0.9},
        # Top-line ops KPIs.
        {"field_name": "occupancy", "value": occ,
         "source_page": 1, "confidence": 0.95},
        {"field_name": "adr", "value": adr,
         "source_page": 1, "confidence": 0.95},
    ]


def _row(year: int, **kwargs) -> dict[str, object]:
    """Build a single row entry the engine's pure-function entrypoint
    expects: ``{fiscal_year, document_id, deviation_count, fields}``.
    """
    return {
        "fiscal_year": year,
        "document_id": kwargs.pop("doc_id", f"doc-{year}"),
        "deviation_count": kwargs.pop("deviations", 0),
        "fields": _baseline_fields(**kwargs),
    }


def _typed_row(year: int, doc_type: str, **kwargs) -> dict[str, object]:
    """``_row`` plus the ``doc_type`` the period basis resolves from.

    The loader always carries it (``documents.doc_type`` is in the query and
    the SQL only admits the P&L family); the pure entrypoint takes it as an
    optional key, so a row without one keeps the annual default.
    """
    return dict(_row(year, **kwargs), doc_type=doc_type)


# ────────────────────────── engine tests ──────────────────────────


def test_empty_returns_zero_coverage() -> None:
    """No P&L docs → empty baseline with coverage_pct=0.0 and no gaps.

    The UI uses ``coverage_pct == 0`` as the cue to hide the panel
    entirely (no historical docs uploaded yet).
    """
    baseline = build_baseline_from_pnls([], lookback_years=5)
    assert baseline.years == []
    assert baseline.gaps == []
    assert baseline.coverage_pct == 0.0
    assert baseline.look_back_years == 5


def test_three_year_sequence_no_gaps() -> None:
    """2022, 2023, 2024 → coverage 3/5 = 0.6, gaps=[].

    Three years of data inside a 5-year lookback window — happy path.
    """
    baseline = build_baseline_from_pnls(
        [_row(2022), _row(2023), _row(2024)],
        lookback_years=5,
    )
    assert [y.fiscal_year for y in baseline.years] == [2022, 2023, 2024]
    assert baseline.gaps == []
    assert baseline.coverage_pct == pytest.approx(0.6)
    assert baseline.look_back_years == 5


def test_gap_detection_middle_year() -> None:
    """2022, 2024 → gap at 2023 (middle of the span).

    The gap detector walks min..max inclusive and surfaces every year
    that didn't land in the result set. Single-year gaps light up the
    UI's "Missing 2023" chip.
    """
    baseline = build_baseline_from_pnls(
        [_row(2022), _row(2024)],
        lookback_years=5,
    )
    assert [y.fiscal_year for y in baseline.years] == [2022, 2024]
    assert baseline.gaps == [2023]
    assert baseline.coverage_pct == pytest.approx(0.4)


def test_gap_detection_multiple_missing() -> None:
    """2020, 2024 → gaps=[2021, 2022, 2023] (3 missing years).

    Multi-year gap — the UI renders "Missing 2021-2023" for this
    kind of payload.
    """
    baseline = build_baseline_from_pnls(
        [_row(2020), _row(2024)],
        lookback_years=5,
    )
    assert baseline.gaps == [2021, 2022, 2023]
    assert baseline.coverage_pct == pytest.approx(0.4)


def test_picks_lower_usali_deviation_when_two_docs_same_year() -> None:
    """When two docs cover 2023, the one with FEWER USALI deviations
    wins (proxy for "cleaner extraction"). Tie-break breaks on
    insertion order — first wins.
    """
    # Same year, but the second doc has a wildly different rooms_rev
    # so we can tell which one was picked.
    dirty = _row(2023, doc_id="doc-dirty", deviations=8, rooms_rev=99_999.0)
    clean = _row(2023, doc_id="doc-clean", deviations=0, rooms_rev=12_000_000.0)
    baseline = build_baseline_from_pnls([dirty, clean], lookback_years=5)

    assert len(baseline.years) == 1
    year = baseline.years[0]
    assert year.fiscal_year == 2023
    # Clean extraction's rooms_revenue should be the one we see.
    assert year.rooms_revenue == pytest.approx(12_000_000.0)
    # Source document trail should point at the clean doc.
    assert year.source_document_ids == ["doc-clean"]


def test_derived_revpar_from_occ_and_adr() -> None:
    """occ=0.72, adr=$250 → revpar=180 (institutional shorthand
    RevPAR ≡ occ × ADR identity). The extractor doesn't always emit
    revpar directly; the engine derives it.
    """
    fields = [
        {"field_name": "occupancy", "value": 0.72,
         "source_page": 1, "confidence": 0.95},
        {"field_name": "adr", "value": 250.0,
         "source_page": 1, "confidence": 0.95},
    ]
    baseline = build_baseline_from_pnls(
        [{"fiscal_year": 2024, "document_id": "doc-1",
          "deviation_count": 0, "fields": fields}],
        lookback_years=5,
    )
    assert len(baseline.years) == 1
    year = baseline.years[0]
    assert year.occupancy == pytest.approx(0.72)
    assert year.adr == pytest.approx(250.0)
    assert year.revpar == pytest.approx(180.0)


def test_walk_yoy_sorted_by_abs_pct_desc() -> None:
    """Walk output is sorted with the biggest abs(yoy_pct) first.

    Build a 3-year series where multiple lines drift, then verify the
    walk is strictly ordered by abs(yoy_pct) DESC (with None-pct entries
    bringing up the rear).
    """
    # Year 2022 baseline; 2023 perturbations chosen so each top-line
    # delta is distinct (rooms_rev -8%, fb_rev +20%, other_rev +1%).
    # Note: derived lines (gop, noi, total_revenue) will also shift —
    # we don't pin them by absolute line, just confirm ordering.
    y1 = _row(2022)
    y2 = _row(
        2023,
        rooms_rev=11_040_000.0,   # -8%
        fb_rev=2_160_000.0,       # +20%
        other_rev=606_000.0,      # +1.0%
    )
    baseline = build_baseline_from_pnls([y1, y2], lookback_years=5)
    walk = walk_yoy(baseline)

    # Every pct-bearing entry comes before any None-pct entry.
    pct_bearing = [d for d in walk if d.yoy_pct is not None]
    none_bearing = [d for d in walk if d.yoy_pct is None]
    assert walk == pct_bearing + none_bearing

    # And the pct-bearing slice is sorted by abs(yoy_pct) DESC.
    abs_pcts = [abs(d.yoy_pct) for d in pct_bearing]
    assert abs_pcts == sorted(abs_pcts, reverse=True)


def test_walk_yoy_drops_below_0_005() -> None:
    """A 0.4% YoY drift is below the 0.5% noise floor — must be EXCLUDED
    from the walk output. Otherwise the UI fills with extractor
    rounding artifacts.
    """
    # Year 1 → Year 2: rooms_revenue moves by 0.4% (below 0.5% floor),
    # other_revenue moves by 10% (well above floor).
    y1 = _row(2023)
    y2 = _row(2024, rooms_rev=12_048_000.0, other_rev=660_000.0)  # +0.4% / +10%
    baseline = build_baseline_from_pnls([y1, y2], lookback_years=5)
    walk = walk_yoy(baseline)

    pct_bearing = [d for d in walk if d.yoy_pct is not None]
    # rooms_revenue 0.4% drift was dropped; other_revenue 10% survives.
    rooms_pct_entries = [
        d for d in pct_bearing
        if d.line == "rooms_revenue" and d.year == 2024
    ]
    assert rooms_pct_entries == []  # below noise floor
    other_pct_entries = [
        d for d in pct_bearing
        if d.line == "other_revenue" and d.year == 2024
    ]
    assert len(other_pct_entries) == 1
    assert other_pct_entries[0].yoy_pct == pytest.approx(0.10, rel=1e-3)


def test_walk_yoy_handles_null_prior_year() -> None:
    """The first year of the series has no prior — every line yields a
    yoy_pct=None entry (so the panel can still surface the chip, just
    without an arrow).
    """
    baseline = build_baseline_from_pnls([_row(2023)], lookback_years=5)
    walk = walk_yoy(baseline)

    # Every walk entry for the first (and only) year has yoy_pct=None.
    assert all(d.yoy_pct is None for d in walk)
    assert all(d.year == 2023 for d in walk)
    # And there's at least one entry per major line (rooms_revenue, gop, noi).
    lines_present = {d.line for d in walk}
    assert "rooms_revenue" in lines_present
    assert "gop" in lines_present
    assert "noi" in lines_present


def test_undistributed_rollup_combines_4_buckets() -> None:
    """undistributed = A&G + sales/mkt + utilities + prop_ops + IT/telecom.

    The catalog spec says "4 buckets" (admin + sales + utilities +
    prop_ops + marketing) — we treat sales & marketing as one bucket,
    add IT/telecom as a fifth (USALI 11th edition added it), and the
    sum must equal the line-by-line total.
    """
    # 800k + 600k + 500k + 400k + 200k = 2,500,000
    baseline = build_baseline_from_pnls(
        [_row(2024, ag=800_000.0, sm=600_000.0, util=500_000.0,
              rm=400_000.0, it=200_000.0)],
        lookback_years=5,
    )
    year = baseline.years[0]
    assert year.undistributed == pytest.approx(2_500_000.0)
    # And the GOP / NOI math chains through:
    # total_rev = 12,000,000 + 1,800,000 + 600,000 = 14,400,000
    # dept_exp = 3,600,000 + 1,500,000 + 250,000 = 5,350,000
    # gop = 14,400,000 - 5,350,000 - 2,500,000 = 6,550,000
    # fixed = 350,000 + 200,000 + 380,000 = 930,000
    # noi = 6,550,000 - 930,000 = 5,620,000
    assert year.total_revenue == pytest.approx(14_400_000.0)
    assert year.gop == pytest.approx(6_550_000.0)
    assert year.fixed_expenses == pytest.approx(930_000.0)
    assert year.noi == pytest.approx(5_620_000.0)


# ───────────────── period comparability (FON-44 §4) ─────────────────
#
# Sam, August 2026: "Fixed Expenses +11,170%, F&B Dept Expense +4,476% …
# driven by incomplete/partial or inconsistently classified historical
# periods." Two causes, both pinned below: the walk divided by the previous
# ELEMENT of the list rather than the previous fiscal year, and it had no
# idea what period any statement covered.


#: The exact walk a contiguous FY→FY pair produced BEFORE the comparability
#: gate existed, captured from the engine at c49f12d. Every one of these
#: must survive unchanged — the gate may only remove comparisons that were
#: never legitimate, never a real year-over-year move.
#: ``(line, yoy_abs, yoy_pct)``, in the engine's own sort order.
_CONTIGUOUS_FY_WALK_2023: list[tuple[str, float, float]] = [
    ("fnb_revenue", 360_000.0, 0.2),
    ("noi", -594_000.0, -0.10569395017793594),
    ("gop", -594_000.0, -0.09068702290076336),
    ("rooms_revenue", -960_000.0, -0.08),
    ("total_revenue", -594_000.0, -0.04125),
    ("other_revenue", 6_000.0, 0.01),
]


def test_contiguous_full_years_walk_exactly_as_before() -> None:
    """THE no-regression assertion.

    A 2022 → 2023 pair of full fiscal years is comparable by every rule the
    gate applies, so it must produce precisely the growth figures it
    produced before FON-44 §4 — same lines, same order, same numbers, and
    no ``reason`` on any of them.
    """
    baseline = build_baseline_from_pnls(
        [
            _row(2022),
            _row(2023, rooms_rev=11_040_000.0, fb_rev=2_160_000.0,
                 other_rev=606_000.0),
        ],
        lookback_years=5,
    )
    walk = walk_yoy(baseline)

    pct_bearing = [d for d in walk if d.yoy_pct is not None]
    assert [d.line for d in pct_bearing] == [
        line for line, _abs, _pct in _CONTIGUOUS_FY_WALK_2023
    ]
    assert [d.yoy_abs for d in pct_bearing] == pytest.approx(
        [abs_ for _line, abs_, _pct in _CONTIGUOUS_FY_WALK_2023]
    )
    assert [d.yoy_pct for d in pct_bearing] == pytest.approx(
        [pct for _line, _abs, pct in _CONTIGUOUS_FY_WALK_2023]
    )
    assert all(d.year == 2023 for d in pct_bearing)
    # A produced percentage carries no refusal.
    assert all(d.reason is None for d in pct_bearing)
    # And the 2022 rows are value-only because 2022 is the first year of
    # the series — that is not a refusal, so they carry no reason either.
    assert all(
        d.reason is None for d in walk if d.year == 2022
    )


def test_walk_refuses_a_gap_year_instead_of_dividing_by_the_last_one() -> None:
    """Sam's deal: "Coverage 4/5 yrs · Missing 2020-2022".

    2023's prior is 2022, which this deal does not have. The old walk took
    the previous ELEMENT of the list — 2019 — and reported the four-year
    move as a year-over-year growth rate. There is now no percentage at
    all, and the row says why.
    """
    baseline = build_baseline_from_pnls(
        [
            _typed_row(2019, "PNL"),
            _typed_row(2023, "PNL", rooms_rev=11_040_000.0),
        ],
        lookback_years=5,
    )
    walk = walk_yoy(baseline)

    assert baseline.gaps == [2020, 2021, 2022]
    # NOTHING in the walk carries a percentage — not one line.
    assert [d for d in walk if d.yoy_pct is not None] == []
    rows_2023 = [d for d in walk if d.year == 2023]
    assert rows_2023, "2023 still renders its values, just without growth"
    assert all(d.yoy_abs is None and d.yoy_pct is None for d in rows_2023)
    assert {d.reason for d in rows_2023} == {"period_mismatch"}
    # The line Sam quoted, specifically.
    fixed_2023 = next(d for d in rows_2023 if d.line == "fixed_expenses")
    assert fixed_2023.yoy_pct is None
    assert fixed_2023.reason == "period_mismatch"


def test_walk_refuses_year_to_date_against_a_full_year() -> None:
    """A YTD stub divided by a full fiscal year is the four-digit-percent
    artifact. The 2023 statement is classified ``PNL_YTD``; the registry
    resolves that to the ``ytd`` scope, so the pair is refused even though
    the two years are adjacent.
    """
    baseline = build_baseline_from_pnls(
        [
            _typed_row(2022, "PNL"),
            _typed_row(2023, "PNL_YTD", rooms_rev=4_000_000.0),
        ],
        lookback_years=5,
    )
    walk = walk_yoy(baseline)

    assert [y.period_basis for y in baseline.years] == ["FY", "YTD"]
    assert [y.is_partial for y in baseline.years] == [False, True]
    rows_2023 = [d for d in walk if d.year == 2023]
    assert rows_2023
    assert all(d.yoy_pct is None for d in rows_2023)
    assert {d.reason for d in rows_2023} == {"period_mismatch"}
    # Had it divided, rooms_revenue would have read -66.7%.
    assert not any(d.yoy_pct is not None for d in walk)


def test_a_stated_period_type_refuses_even_when_the_doc_type_says_annual() -> None:
    """The basis comes from the statement, not only from its filing.

    ``_doc_scope`` ranks a stated ``period_type`` above the doc-type
    default (pinned in ``test_doc_scope_pnl_family.py``), so a document
    classified ``PNL`` that says it covers a year-to-date is treated as
    partial here too — one resolver, one answer.
    """
    ytd_fields = [
        *_baseline_fields(rooms_rev=4_000_000.0),
        {"field_name": "p_and_l_usali.period_type", "value": "ytd",
         "source_page": 1, "confidence": 0.9},
    ]
    baseline = build_baseline_from_pnls(
        [
            _typed_row(2022, "PNL"),
            {"fiscal_year": 2023, "document_id": "doc-2023",
             "deviation_count": 0, "doc_type": "PNL", "fields": ytd_fields},
        ],
        lookback_years=5,
    )
    assert baseline.years[1].period_basis == "YTD"
    assert baseline.years[1].is_partial is True
    assert all(d.yoy_pct is None for d in walk_yoy(baseline))


def test_walk_refuses_when_either_side_has_no_total_revenue() -> None:
    """No top line → no way to know the statement covers the period it
    claims to, so no line on it is compared. The refusal is ``no_source``
    (the field never resolved), not ``period_mismatch``.
    """
    partial_fields = [
        {"field_name": "rooms_revenue", "value": 11_000_000.0,
         "source_page": 1, "confidence": 0.9},
        {"field_name": "property_tax", "value": 360_000.0,
         "source_page": 3, "confidence": 0.9},
        {"field_name": "insurance_expense", "value": 210_000.0,
         "source_page": 3, "confidence": 0.9},
    ]
    baseline = build_baseline_from_pnls(
        [
            _typed_row(2022, "PNL"),
            {"fiscal_year": 2023, "document_id": "doc-2023",
             "deviation_count": 0, "doc_type": "PNL",
             "fields": partial_fields},
        ],
        lookback_years=5,
    )
    assert baseline.years[1].total_revenue is None

    rows_2023 = [d for d in walk_yoy(baseline) if d.year == 2023]
    # rooms_revenue WOULD have divided (-8.3%) — it is refused with it.
    assert {d.line for d in rows_2023} == {"rooms_revenue", "fixed_expenses"}
    assert all(d.yoy_pct is None for d in rows_2023)
    assert {d.reason for d in rows_2023} == {"no_source"}


def test_a_trailing_twelve_still_compares_against_a_fiscal_year() -> None:
    """The standard hotel package is three annuals plus the current T-12.

    A fiscal year and a trailing twelve are different periods but the same
    LENGTH, so their magnitudes are commensurate and the walk keeps
    producing the swing. This is the boundary of the gate: it refuses
    incomparable MAGNITUDES, not every difference in filing.
    """
    baseline = build_baseline_from_pnls(
        [_typed_row(2023, "PNL"), _typed_row(2024, "T12",
                                             rooms_rev=13_200_000.0)],
        lookback_years=5,
    )
    assert [y.period_basis for y in baseline.years] == ["FY", "T12"]
    assert not any(y.is_partial for y in baseline.years)

    rooms_2024 = [
        d for d in walk_yoy(baseline)
        if d.line == "rooms_revenue" and d.year == 2024
    ]
    assert len(rooms_2024) == 1
    assert rooms_2024[0].yoy_pct == pytest.approx(0.10)
    assert rooms_2024[0].reason is None


def test_period_basis_defaults_to_annual_without_a_doc_type() -> None:
    """A row the caller built by hand (no ``doc_type``, no ``period_type``)
    keeps the annual default, so the pure entrypoint compares exactly as it
    did before the gate. Nothing is guessed for the LOADER, which always
    carries a doc type — the SQL admits no document without one.
    """
    baseline = build_baseline_from_pnls([_row(2023)], lookback_years=5)
    year = baseline.years[0]
    assert year.period_basis == "FY"
    assert year.is_partial is False


# ────────────────────────── API endpoint tests ──────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so each starts deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "broker_questions",
            "audit_log",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


async def _seed_pnl(
    *,
    deal_id: str,
    tenant_id: str,
    fiscal_year: int,
    fields: list[dict[str, object]] | None = None,
    doc_type: str = "T12",
    status: str = "Extracted",
    deviation_count: int = 0,
) -> str:
    """Insert one documents + one extraction_results row covering ``fiscal_year``.

    Returns the document id so the caller can assert on
    ``source_document_ids`` round-tripping through the API.
    """
    from sqlalchemy import text

    from app.database import get_session_factory

    doc_id = str(uuid4())
    extr_id = str(uuid4())
    deviations_blob = json.dumps(
        {
            "inconclusive": False,
            "applicable_count": 5,
            "passed_count": 5 - deviation_count,
            "deviations": [
                {"rule_id": f"R{i}", "rule_name": f"rule {i}",
                 "severity": "WARN", "message": "",
                 "actual_value": 0.0, "threshold_min": 0.0,
                 "threshold_max": 1.0,
                 "requires_market_context": False}
                for i in range(deviation_count)
            ],
        }
    )
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    fiscal_year, usali_deviations
                ) VALUES (
                    :id, :deal, :tenant, :fname, :dtype, :stat, :year,
                    :devs
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fname": f"pnl-{fiscal_year}.pdf",
                "dtype": doc_type,
                "stat": status,
                "year": fiscal_year,
                "devs": deviations_blob,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields
                )
                """
            ),
            {
                "id": extr_id,
                "doc": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fields": json.dumps(
                    fields if fields is not None else _baseline_fields()
                ),
            },
        )
        await session.commit()
    return doc_id


@pytest.mark.asyncio
async def test_endpoint_tenant_scoped() -> None:
    """Seed a deal under TENANT_A; request as TENANT_B → 404 (deal-belongs
    gate fires before the baseline read). No cross-tenant leakage.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create deal under TENANT_A.
        r = await client.post(
            "/deals",
            json={"name": "Hist Hotel A", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        await _seed_pnl(deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2023)
        await _seed_pnl(deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2024)

        # GET as TENANT_B → 404 (deal not in this tenant).
        r = await client.get(
            f"/deals/{deal_id}/historical-baseline",
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_returns_walk_and_baseline() -> None:
    """Happy path: seed 3 years under TENANT_A, GET as TENANT_A returns
    the baseline + walk envelope with all the expected fields populated.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Hist Hotel B", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        # 2022 baseline, 2023 -30% rooms_rev, 2024 holds.
        await _seed_pnl(
            deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2022,
            fields=_baseline_fields(),
        )
        await _seed_pnl(
            deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2023,
            fields=_baseline_fields(rooms_rev=8_400_000.0),
        )
        await _seed_pnl(
            deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2024,
            fields=_baseline_fields(rooms_rev=8_400_000.0),
        )

        r = await client.get(
            f"/deals/{deal_id}/historical-baseline",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deal_id"] == str(deal_id)
        assert [y["fiscal_year"] for y in body["years"]] == [2022, 2023, 2024]
        assert body["gaps"] == []
        assert body["coverage_pct"] == pytest.approx(0.6)
        assert body["look_back_years"] == 5

        # Walk envelope present + ordered (sorted by abs(yoy_pct) DESC).
        pct_entries = [d for d in body["walk"] if d["yoy_pct"] is not None]
        assert pct_entries, "walk should have at least one pct-bearing entry"
        abs_pcts = [abs(d["yoy_pct"]) for d in pct_entries]
        assert abs_pcts == sorted(abs_pcts, reverse=True)
        # And the rooms_revenue 2023 chip MUST be in the walk — its
        # -30% drop is well above the 0.5% noise floor.
        rooms_2023 = [
            d for d in pct_entries
            if d["line"] == "rooms_revenue" and d["year"] == 2023
        ]
        assert len(rooms_2023) == 1
        assert rooms_2023[0]["yoy_pct"] == pytest.approx(-0.30, rel=1e-2)


@pytest.mark.asyncio
async def test_endpoint_carries_the_period_basis_and_the_refusal() -> None:
    """The comparability verdict has to reach the UI, not just the engine.

    Seeds Sam's shape — a 2019 annual and a 2023 annual with the years
    between them missing — and asserts the wire carries the per-year
    ``period_basis`` plus a ``period_mismatch`` on every 2023 swing, with
    no percentage anywhere. The panel filters ``yoy_pct === null`` out of
    its chip row, so the four-digit swings simply stop being offered as
    broker questions.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Hist Hotel D", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        await _seed_pnl(
            deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2019,
            doc_type="PNL", fields=_baseline_fields(),
        )
        await _seed_pnl(
            deal_id=deal_id, tenant_id=TENANT_A, fiscal_year=2023,
            doc_type="PNL", fields=_baseline_fields(rooms_rev=11_040_000.0),
        )

        r = await client.get(
            f"/deals/{deal_id}/historical-baseline",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["gaps"] == [2020, 2021, 2022]
        assert [y["period_basis"] for y in body["years"]] == ["FY", "FY"]
        assert [y["is_partial"] for y in body["years"]] == [False, False]

        assert body["walk"], "values still render, only the growth is withheld"
        assert all(d["yoy_pct"] is None for d in body["walk"])
        refused = [d for d in body["walk"] if d["year"] == 2023]
        assert refused
        assert {d["reason"] for d in refused} == {"period_mismatch"}
        # The first year of the series refuses nothing — it has no prior.
        assert {d["reason"] for d in body["walk"] if d["year"] == 2019} == {None}


@pytest.mark.asyncio
async def test_endpoint_empty_when_no_pnls() -> None:
    """A deal with NO P&L docs returns a well-formed empty envelope —
    the UI uses ``coverage_pct == 0`` as the cue to hide the panel.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Hist Hotel C", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        r = await client.get(
            f"/deals/{deal_id}/historical-baseline",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["years"] == []
        assert body["gaps"] == []
        assert body["coverage_pct"] == 0.0
        assert body["walk"] == []
