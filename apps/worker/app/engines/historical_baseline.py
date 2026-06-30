"""Historical baseline engine — multi-year P&L aggregation + YoY walk.

This is **Wave 2 P2.6** — Sam's June 2026 ask: "Institutional IC analysts
will not approve a deal without seeing the multi-year trend." Today
Fondok only renders the forward proforma (Y1..Y5); this module aggregates
the property's *own* historical actuals (typically 3-5 years of P&Ls
tagged with ``documents.fiscal_year``) into a single ``HistoricalBaseline``
the UI can stack side-by-side with the Y1 forecast.

Distinct from ``historical_variance.py``
----------------------------------------

``historical_variance.py`` emits **broker questions** (above-threshold
YoY swings drafted as copy-paste questions for the seller broker). This
module emits the **financial baseline itself** — the per-year P&L
roll-ups (revenue / expenses / GOP / NOI) that the analyst will look at
when deciding whether the proforma is plausible. The variance engine
consumes the same per-year P&Ls but only on consecutive pairs; the
baseline carries the FULL multi-year context.

Data lineage
------------

The Router already accepts multiple historical P&Ls per deal (every
``T12 / PNL / PNL_MONTHLY / PNL_YTD`` doc gets tagged with
``documents.fiscal_year`` in the wizard or pulled from the extracted
``period_ending`` — see migrations.py
``documents.add_fiscal_year``). The engine reads:

* ``documents`` joined to ``extraction_results`` filtered to the P&L
  family (``T12 / PNL / PNL_MONTHLY / PNL_YTD``) and ``status='Extracted'``.
* Highest-confidence extraction per year (one doc per year preferred;
  when two land on the same year, the one with the fewest USALI
  deviations wins — that's the cleaner extraction).
* USALI bucket roll-ups via ``_derive_usali_rollups`` so synthesized
  ``total_revenue`` / ``gop`` / ``undistributed`` / ``noi`` are
  honored alongside directly-emitted line items.

Lookback window
---------------

Default ``lookback_years=5`` per the Wave 1 product-decision doc
(``project_fondok_wave1_decisions.md`` #5: "5-yr gap look-back"). The
engine surfaces ``coverage_pct = years_with_data / lookback_years``
so the UI can render "Coverage 3/5 yrs — Missing 2020-2021" without
having to re-derive the math.

YoY walk
--------

``walk_yoy(baseline)`` projects the year-over-year deltas as a flat
list ordered by ``abs(yoy_pct) DESC`` so the biggest swings surface
first. The 0.5% threshold (``_YOY_NOISE_FLOOR = 0.005``) is intentional
— a 0.4% YoY drift on rooms revenue isn't analytical signal and would
just clutter the UI's "Walk" chips.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# We reuse the USALI scorer's alias map + roll-up synthesizer so the
# baseline reads the SAME canonical fields the compliance scorer does.
# Keeping a single source of truth means a new alias added for a future
# extractor flavor lights up both the scorer and the baseline at once.
from ..services.usali_scorer import _derive_usali_rollups

logger = logging.getLogger(__name__)

# The five P&L family doc_types the engine considers historical. PNL_BENCHMARK
# is intentionally excluded — that's a peer-set benchmark (HostStats /
# CBRE), not the SUBJECT property's own actuals.
#
# NOTE: the task description uses the token ``PNL_ANNUAL``; the canonical
# enum (``packages/schemas-py/fondok_schemas/document.py``) doesn't carry
# that value — multi-year annual P&Ls land under ``PNL`` (when the
# Extractor saw "year ended December 31, 2023") or ``T12``. We accept
# the whole family so neither tag is rejected at the loader.
_PNL_FAMILY_DOC_TYPES = ("T12", "PNL", "PNL_MONTHLY", "PNL_YTD")

# Drop YoY swings whose magnitude is below this floor — they're just
# extractor noise rounded to a percent and would clutter the walk chips
# the UI renders. 0.5% is wide enough to clear the rounding from
# ``round(value, 2)`` propagation through the rollup math.
_YOY_NOISE_FLOOR = 0.005


# ────────────────────────── public dataclasses ──────────────────────────


@dataclass
class HistoricalYear:
    """One historical fiscal year's P&L roll-up.

    Every numeric field is ``float | None`` — ``None`` means the
    extraction didn't carry that line (the UI renders an em-dash).
    Currency values are in dollars (not thousands), occupancy is a
    decimal (0..1), ADR/RevPAR are dollar amounts per unit/per
    occupied room.
    """

    fiscal_year: int
    occupancy: float | None = None
    adr: float | None = None
    revpar: float | None = None
    rooms_revenue: float | None = None
    fnb_revenue: float | None = None
    other_revenue: float | None = None
    total_revenue: float | None = None
    rooms_dept_expense: float | None = None
    fnb_dept_expense: float | None = None
    other_dept_expense: float | None = None
    undistributed: float | None = None  # A&G + sales/mkt + utilities + prop_ops + IT
    gop: float | None = None
    fixed_expenses: float | None = None  # property_tax + insurance + mgmt_fee
    noi: float | None = None
    source_document_ids: list[str] = field(default_factory=list)


@dataclass
class HistoricalBaseline:
    """End-to-end output of ``build_historical_baseline``.

    ``coverage_pct`` is the fraction of the ``look_back_years`` window
    that actually has data — 3 years out of 5 → ``0.6``. The UI uses
    this to render a coverage chip and decide whether to surface the
    panel at all (silent when coverage == 0).
    """

    years: list[HistoricalYear] = field(default_factory=list)
    gaps: list[int] = field(default_factory=list)
    look_back_years: int = 5
    coverage_pct: float = 0.0


@dataclass
class YoYDelta:
    """One above-noise YoY swing on a single line for a single year.

    ``year`` is the *current* year (the one being measured against the
    prior). ``yoy_pct`` is a signed decimal (-0.05 = down 5%); the walk
    is sorted by ``abs(yoy_pct) DESC`` so the biggest swing chips
    surface first.
    """

    line: str
    year: int
    value: float
    yoy_abs: float | None
    yoy_pct: float | None


# ────────────────────────── line-item labels ──────────────────────────


# Canonical line slugs we walk through when projecting the baseline +
# YoY walk. Order is the visual order rendered in the UI table (top to
# bottom). Centralizing here means the engine, the API, and the
# UI panel all share the same source of truth on the row catalog.
WALK_LINES: tuple[str, ...] = (
    "rooms_revenue",
    "fnb_revenue",
    "other_revenue",
    "total_revenue",
    "rooms_dept_expense",
    "fnb_dept_expense",
    "other_dept_expense",
    "undistributed",
    "gop",
    "fixed_expenses",
    "noi",
)


# ────────────────────────── private resolver ──────────────────────────


def _coerce_num(v: Any) -> float | None:
    """Numeric coerce — booleans rejected, parenthesized negatives
    accepted, dollar signs / commas / trailing % stripped.

    Mirrors ``historical_variance._coerce_value`` so the two engines
    agree on what counts as a number; kept duplicated here so the
    baseline module stays import-light (no need to pull from a sibling
    engine that may grow heavier deps later).
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None  # rejects NaN via self-equality
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        return f / 100.0 if pct else f
    return None


def _resolve(flat: dict[str, Any], canonical: str) -> float | None:
    """Look ``canonical`` up on ``flat`` via the full USALI scorer
    resolution chain.

    Sam QA 2026-06-29: this used to do only ``flat.get(canonical)``
    + alias-map walk, while the broker-questions engine
    (``historical_variance._normalize_pnl``) also called
    ``usali_scorer._resolve_field`` as a final fallback. Result:
    same extraction_data, two different readings — the broker
    engine saw 2019 F&B = $1,028,110 while the P&L Historicals tab
    rendered blank because the flat dict didn't carry a bare
    ``fb_revenue`` key for that year, only the nested
    ``p_and_l_usali.operating_revenue.food_beverage_revenue`` path.

    Delegating to ``_resolve_field`` makes the scorer the single
    source of truth across all consumers: direct key → alias map
    → v3 token-match fallback. Whatever the broker engine sees,
    the historical baseline now sees too.
    """
    from ..services.usali_scorer import _resolve_field as _scorer_resolve

    return _coerce_num(_scorer_resolve(flat, canonical))


def _flatten_fields(fields: list[dict[str, Any]] | str | None) -> dict[str, Any]:
    """Reduce the extraction-results ``fields`` blob to a flat dict.

    Accepts either a parsed list (Postgres JSONB) or the JSON string
    SQLite hands back. Also tail-writes the last path component
    (``p_and_l_usali.rooms.revenue_usd`` → ``revenue_usd``) so the
    USALI alias map's bare-name aliases still resolve — same trick
    ``flatten_extraction_fields`` uses in usali_scorer.
    """
    if fields is None:
        return {}
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError:
            return {}
    if not isinstance(fields, list):
        return {}
    flat: dict[str, Any] = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = f.get("field_name")
        if not isinstance(name, str) or not name:
            continue
        value = f.get("value")
        if value is None:
            continue
        flat[name] = value
        # Skip monthly / per-page sub-records — same guard as
        # usali_scorer.flatten_extraction_fields so a per-month line
        # doesn't clobber a TTM total.
        if "." in name:
            lowered = name.lower()
            if (
                ".monthly." in lowered
                or ".page" in lowered
                or ".per_month." in lowered
            ):
                continue
            tail = name.rsplit(".", 1)[-1]
            flat.setdefault(tail, value)
    return flat


def _build_year_from_flat(
    flat: dict[str, Any], *, fiscal_year: int, doc_id: str | None
) -> HistoricalYear:
    """Project a per-year flat extraction dict into ``HistoricalYear``.

    Calls ``_derive_usali_rollups`` so synthesized totals
    (``total_revenue``, ``gop``, ``undistributed_expenses``, ``noi``)
    are populated before we resolve. Then computes the few derived
    fields that aren't part of the USALI catalog:

    * ``revpar = occupancy × adr`` when both are known (institutional
      shorthand — keeps the panel from showing "—" when the extractor
      grabbed only occ + adr).
    * ``undistributed`` is the four-bucket sum (A&G + sales/mkt +
      utilities + prop_ops + info/telecom) — the USALI roll-up
      already does this, we just re-expose it under the friendlier
      name ``undistributed``.
    * ``fixed_expenses = property_tax + insurance + mgmt_fee`` — the
      panel surfaces this directly (USALI's ``fixed_charges`` only
      covers tax + insurance; institutional IC bundles mgmt fee
      into the fixed block).
    """
    # ``_derive_usali_rollups`` is a side-effecting helper that
    # populates total_revenue / gop / undistributed_expenses / noi
    # into ``flat`` in-place when the extractor didn't ship them.
    _derive_usali_rollups(flat)

    occ = _resolve(flat, "occupancy")
    adr = _resolve(flat, "adr")
    revpar = _resolve(flat, "revpar")
    # Derive RevPAR from occ × ADR when the extractor didn't emit it
    # directly. Institutional shorthand identity (RevPAR ≡ occ × ADR
    # by definition — drift > 0.5% is an extraction bug).
    if revpar is None and occ is not None and adr is not None:
        revpar = occ * adr

    rooms_rev = _resolve(flat, "rooms_revenue")
    fnb_rev = _resolve(flat, "fb_revenue")
    other_rev = _resolve(flat, "other_revenue")
    total_rev = _resolve(flat, "total_revenue")

    rooms_dept = _resolve(flat, "rooms_dept_expense")
    fnb_dept = _resolve(flat, "fb_dept_expense")
    other_dept = _resolve(flat, "other_dept_expense")

    # Undistributed = A&G + sales/mkt + utilities + prop_ops + IT/telecom.
    # When the USALI scorer's rollup already synthesized
    # ``undistributed_expenses``, honor that. Otherwise fall through to a
    # direct 5-bucket sum so a partial extraction (no scorer synthesis)
    # still surfaces a usable total.
    undist = _resolve(flat, "undistributed_expenses")
    if undist is None:
        parts = [
            _resolve(flat, "ag_expense"),
            _resolve(flat, "marketing_expense"),
            _resolve(flat, "utilities_expense"),
            _resolve(flat, "rm_expense"),
            _resolve(flat, "information_telecom"),
        ]
        present = [p for p in parts if p is not None]
        # ≥2 components required so a single-line extraction doesn't
        # masquerade as a "total". Same threshold the USALI rollup uses.
        if len(present) >= 2:
            undist = sum(present)

    gop = _resolve(flat, "gop")
    if gop is None and total_rev is not None and undist is not None:
        dept_total = sum(
            v for v in (rooms_dept, fnb_dept, other_dept) if v is not None
        ) if any(v is not None for v in (rooms_dept, fnb_dept, other_dept)) else None
        if dept_total is not None:
            gop = total_rev - dept_total - undist

    # Fixed = property_tax + insurance + mgmt_fee. Institutional IC
    # convention bundles mgmt fee into the fixed block (USALI's
    # ``fixed_charges`` only covers tax + insurance, with mgmt_fee
    # sitting between GOP and NOI — same dollars either way).
    prop_tax = _resolve(flat, "property_tax")
    insurance = _resolve(flat, "insurance_expense")
    mgmt_fee = _resolve(flat, "mgmt_fee")
    fixed_parts = [v for v in (prop_tax, insurance, mgmt_fee) if v is not None]
    fixed = sum(fixed_parts) if fixed_parts else None

    noi = _resolve(flat, "noi")
    if noi is None and gop is not None and fixed is not None:
        noi = gop - fixed

    return HistoricalYear(
        fiscal_year=fiscal_year,
        occupancy=occ,
        adr=adr,
        revpar=revpar,
        rooms_revenue=rooms_rev,
        fnb_revenue=fnb_rev,
        other_revenue=other_rev,
        total_revenue=total_rev,
        rooms_dept_expense=rooms_dept,
        fnb_dept_expense=fnb_dept,
        other_dept_expense=other_dept,
        undistributed=undist,
        gop=gop,
        fixed_expenses=fixed,
        noi=noi,
        source_document_ids=[doc_id] if doc_id else [],
    )


# ────────────────────────── public entrypoints ──────────────────────────


def build_baseline_from_pnls(
    rows: list[dict[str, Any]],
    *,
    lookback_years: int = 5,
) -> HistoricalBaseline:
    """Pure-function variant of ``build_historical_baseline`` — no DB.

    Each entry in ``rows`` is ``{fiscal_year: int, document_id?: str,
    deviation_count?: int, fields: list[dict] | dict}``. When the same
    ``fiscal_year`` appears twice, the entry with the LOWER
    ``deviation_count`` wins (cleaner USALI extraction). Ties break on
    insertion order (first wins).

    Used directly by tests (so they don't need a DB) and by
    ``build_historical_baseline`` after it has done the SQL query.
    """
    # Group by year, keeping the row with the lowest deviation count.
    by_year: dict[int, dict[str, Any]] = {}
    for r in rows:
        fy = r.get("fiscal_year")
        if not isinstance(fy, int):
            continue
        existing = by_year.get(fy)
        if existing is None:
            by_year[fy] = r
            continue
        if (
            int(r.get("deviation_count") or 0) < int(existing.get("deviation_count") or 0)
        ):
            by_year[fy] = r

    years: list[HistoricalYear] = []
    for fy in sorted(by_year.keys()):
        row = by_year[fy]
        fields = row.get("fields")
        flat = (
            _flatten_fields(fields)
            if isinstance(fields, (list, str))
            else (fields if isinstance(fields, dict) else {})
        )
        doc_id = row.get("document_id")
        years.append(
            _build_year_from_flat(
                flat,
                fiscal_year=fy,
                doc_id=str(doc_id) if doc_id else None,
            )
        )

    # Gap detection — every fiscal year between min and max that didn't
    # land in ``by_year`` is a gap. Single-year baselines have no gaps
    # by definition.
    gaps: list[int] = []
    if len(years) >= 2:
        lo = years[0].fiscal_year
        hi = years[-1].fiscal_year
        present = {y.fiscal_year for y in years}
        gaps = [y for y in range(lo, hi + 1) if y not in present]

    coverage_pct = (
        round(len(years) / lookback_years, 4) if lookback_years > 0 else 0.0
    )

    return HistoricalBaseline(
        years=years,
        gaps=gaps,
        look_back_years=lookback_years,
        coverage_pct=coverage_pct,
    )


async def build_historical_baseline(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    lookback_years: int = 5,
) -> HistoricalBaseline:
    """Build the 3-5 year historical baseline for one deal.

    Query strategy
    --------------

    1. ``documents`` joined to ``extraction_results`` filtered to the
       P&L family (``T12 / PNL / PNL_MONTHLY / PNL_YTD``) AND
       ``documents.fiscal_year IS NOT NULL`` AND ``status='Extracted'``.
    2. Per-year: highest-confidence extraction wins — proxied by the
       USALI deviation count (fewer = cleaner). Ties break on
       ``created_at DESC`` so the most-recent extraction is canonical.
    3. The list-of-records ``fields`` blob is flattened + USALI
       roll-ups synthesized via ``_derive_usali_rollups`` so derived
       totals (``total_revenue`` / ``gop`` / ``noi``) are populated
       before we project into the dataclass.

    Returns an empty-but-well-formed baseline when no docs match —
    ``coverage_pct=0.0`` is the UI's cue to render nothing.
    """
    # The deviation count proxy: read ``documents.usali_deviations``,
    # parse to a list (JSONB on Postgres, TEXT-with-JSON on SQLite),
    # count the entries. NULL/parse-failure → 999 (sorts last).
    rows_result = await session.execute(
        text(
            """
            SELECT er.fields,
                   d.fiscal_year,
                   d.id            AS document_id,
                   d.usali_deviations,
                   er.created_at
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND UPPER(COALESCE(d.doc_type, '')) IN
                   ('T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD')
               AND UPPER(COALESCE(d.status, '')) IN ('EXTRACTED', 'EXTRACT')
               AND d.fiscal_year IS NOT NULL
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )

    raw_rows: list[dict[str, Any]] = []
    for r in rows_result.fetchall():
        m = r._mapping
        fy = m.get("fiscal_year")
        if not isinstance(fy, int):
            continue
        # Deviation count proxy: parse the JSONB / JSON-TEXT and count
        # entries. Anything we can't parse → 999 so a malformed row
        # doesn't beat a clean row on the tie-break.
        dev = m.get("usali_deviations")
        deviation_count = _deviation_count(dev)
        raw_rows.append(
            {
                "fiscal_year": fy,
                "document_id": m.get("document_id"),
                "fields": m.get("fields"),
                "deviation_count": deviation_count,
            }
        )

    return build_baseline_from_pnls(raw_rows, lookback_years=lookback_years)


def _deviation_count(raw: Any) -> int:
    """Best-effort count of USALI deviations on a documents row.

    Accepts the parsed-dict shape Postgres ships
    (``{"deviations": [...], "applicable_count": ..., ...}``) and the
    JSON-encoded-string shape SQLite ships. Returns 999 on any failure
    so unparseable rows sort LAST in the same-year tie-break.
    """
    if raw is None:
        return 999
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return 999
    if isinstance(raw, dict):
        devs = raw.get("deviations")
        if isinstance(devs, list):
            return len(devs)
        return 999
    if isinstance(raw, list):
        # Pre-Wave-1 schema sometimes shipped the bare deviation list.
        return len(raw)
    return 999


# ────────────────────────── YoY walk projection ──────────────────────────


def _walk_value(year: HistoricalYear, line: str) -> float | None:
    """Pull one line from a ``HistoricalYear`` by canonical slug."""
    return getattr(year, line, None)


def walk_yoy(baseline: HistoricalBaseline) -> list[YoYDelta]:
    """Project consecutive-year deltas across every walk line.

    Sorted by ``abs(yoy_pct) DESC`` so the UI's "Walk" chips render
    biggest swings first. ``yoy_pct=None`` rows (first year of the
    series, or zero-prior-year divisions) sort LAST.

    The noise floor (``_YOY_NOISE_FLOOR = 0.005``) drops swings whose
    magnitude is below 0.5% — those are extraction rounding artifacts,
    not analytical signal.
    """
    deltas: list[YoYDelta] = []
    years_sorted = sorted(baseline.years, key=lambda y: y.fiscal_year)
    for i, year in enumerate(years_sorted):
        for line in WALK_LINES:
            val = _walk_value(year, line)
            if val is None:
                continue
            if i == 0:
                # First year — no prior to compare; emit a "value only"
                # entry so the panel can show a 2023 chip with no
                # arrow. ``yoy_pct=None`` sorts these last.
                deltas.append(
                    YoYDelta(
                        line=line,
                        year=year.fiscal_year,
                        value=val,
                        yoy_abs=None,
                        yoy_pct=None,
                    )
                )
                continue
            prior = _walk_value(years_sorted[i - 1], line)
            if prior is None or prior == 0:
                deltas.append(
                    YoYDelta(
                        line=line,
                        year=year.fiscal_year,
                        value=val,
                        yoy_abs=None,
                        yoy_pct=None,
                    )
                )
                continue
            yoy_abs = val - prior
            yoy_pct = yoy_abs / prior
            if abs(yoy_pct) < _YOY_NOISE_FLOOR:
                # Below the noise floor — skip. The UI walk chips would
                # be cluttered with sub-1% drifts that aren't signal.
                continue
            deltas.append(
                YoYDelta(
                    line=line,
                    year=year.fiscal_year,
                    value=val,
                    yoy_abs=yoy_abs,
                    yoy_pct=yoy_pct,
                )
            )

    # Sort: pct-bearing entries first (by abs DESC), then nulls.
    deltas.sort(
        key=lambda d: (
            d.yoy_pct is None,
            -abs(d.yoy_pct) if d.yoy_pct is not None else 0,
        )
    )
    return deltas


# ────────────────────────── JSONB serializer ──────────────────────────


def baseline_to_dict(baseline: HistoricalBaseline) -> dict[str, Any]:
    """Project the dataclass tree into a JSON-safe dict.

    Used by the API endpoint to stuff the baseline into a Pydantic
    response model without round-tripping every field by hand.
    """
    return {
        "years": [asdict(y) for y in baseline.years],
        "gaps": list(baseline.gaps),
        "look_back_years": baseline.look_back_years,
        "coverage_pct": baseline.coverage_pct,
    }


def walk_to_list(walk: list[YoYDelta]) -> list[dict[str, Any]]:
    """Project YoY deltas into a list of dicts for the API response."""
    return [asdict(d) for d in walk]


__all__ = [
    "HistoricalBaseline",
    "HistoricalYear",
    "WALK_LINES",
    "YoYDelta",
    "baseline_to_dict",
    "build_baseline_from_pnls",
    "build_historical_baseline",
    "walk_to_list",
    "walk_yoy",
]
