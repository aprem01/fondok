"""Financial-document coverage + gap detection for a deal.

Roadmap item #7 — sequential + detail-level gap detection on financial
uploads. Sam's framing on the June 25 call:

    "If I have financials from 2019 to 2025 but I'm missing detailed
    for 2024 to 2025, only summary — that's a gap I'd want Fondok to
    flag."

Two flavors of gap:

* **Sequential gap** — "you have 2019, 2020, 2022-2025; missing 2021".
* **Detail-level gap** — "you have an annual T-12 for 2024 but no
  monthly breakdown" / "monthly P&L only through October, missing
  Nov-Dec".

Per Wave 1 product decisions (locked 2026-06-27):

* Look-back window is 5 years by default; deal-level override allowed
  via API query param.
* Non-calendar fiscal years flag gaps but mark them ``dismissible``
  so the analyst can clear with one click.

This module is purely a read model — it never writes back. The web app
calls ``GET /deals/{deal_id}/document_coverage`` (wired in
``api/documents.py``) and renders a strip of gap chips on the
Onboarding / Data Room view.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Period-type ranking + period-type extraction already live next door —
# reuse, don't reinvent. ``_PERIOD_TYPE_RANK`` is imported so other
# downstream modules that key off the same ranking can pull it from
# either home (single source of truth lives in engine_runner →
# field_catalog.yaml).
from .engine_runner import _PERIOD_TYPE_RANK, _extract_period_type  # noqa: F401

logger = logging.getLogger(__name__)


# ─────────────────────────── tunables ───────────────────────────


# Default look-back. Wave 1 decision #7: 5 years, overridable per deal.
DEFAULT_LOOKBACK_YEARS = 5

# Period-type tokens that mean "this row represents the full year"
# (so a year that has any of these counts as having annual coverage).
_ANNUAL_PERIOD_TYPES = frozenset(
    {
        "annual",
        "fiscal_year",
        "full_year",
        "trailing_twelve",
        "ttm",
        "t12",
        "rolling_twelve",
    }
)
_MONTHLY_PERIOD_TYPES = frozenset({"monthly", "month", "single_month"})

# doc_type values that count as P&L family — same list engine_runner uses
# for its T-12 loader. Quarterly P&Ls (which would be PNL_QTD) are not
# yet a distinct doc_type, so we leave them out — they'd pass through as
# PNL_MONTHLY or PNL_YTD via period_type refinement.
_PNL_DOC_TYPES = ("T12", "PNL_MONTHLY", "PNL_YTD")


# ─────────────────────────── dataclasses ───────────────────────────


GapType = Literal[
    "year_missing",
    "month_partial",
    "annual_no_detail",
    "summary_only",
]
Severity = Literal["error", "warn", "info"]


@dataclass
class CoverageGap:
    """One gap in the deal's financial-document coverage.

    ``gap_type`` distinguishes the four shapes the UI renders:

    * ``year_missing``   — sequential gap: year has zero P&L coverage.
    * ``month_partial``  — monthly P&L exists for the year but doesn't
      cover all 12 months.
    * ``annual_no_detail`` — annual T-12 / TTM present, zero monthly
      P&Ls for that year.
    * ``summary_only``   — only YTD / partial summary exists for the
      year, no annual T-12 and no full monthly coverage.

    ``dismissible`` is ``True`` for gaps that may legitimately not be
    gaps once the analyst confirms a non-calendar fiscal year (per the
    Wave 1 product decisions). The UI shows a one-click "dismiss"
    affordance for these.
    """

    gap_type: GapType
    year: int
    message: str
    severity: Severity
    months_missing: list[int] | None = None
    dismissible: bool = False


@dataclass
class DocumentCoverage:
    """Coverage rollup the API returns for one deal."""

    deal_id: str
    # year → list of contributing docs ({doc_id, doc_type, period_type,
    # period_ending}). Years are present even when they have only summary
    # coverage; missing years live in ``gaps`` only.
    year_coverage: dict[int, list[dict[str, Any]]]
    gaps: list[CoverageGap]
    lookback_years: int


# ─────────────────────────── parsing helpers ───────────────────────────


def _parse_iso_date(value: Any) -> date | None:
    """Extract a ``datetime.date`` from any of the shapes the Extractor
    emits for ``period_ending``. Returns ``None`` for unparseable input.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Strict ISO first (cheap), then a couple of common fallbacks.
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
        for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


def _extract_period_ending(raw_fields: list[Any]) -> date | None:
    """Pull ``p_and_l_usali.period_ending`` (or any ``*.period_ending``)
    off a flat extraction-fields list and parse to a ``date``.

    Mirrors ``engine_runner._extract_period_type`` so we use the same
    convention everywhere — Extractor agent emits a dotted path under
    ``p_and_l_usali``; we accept any field whose name ends with
    ``period_ending``.
    """
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip().lower()
        if not name.endswith("period_ending"):
            continue
        parsed = _parse_iso_date(f.get("value"))
        if parsed is not None:
            return parsed
    return None


def _coerce_fields_list(raw_fields: Any) -> list[Any] | None:
    """Normalize a possibly-JSON-encoded ``fields`` payload to a list.
    Returns ``None`` when the payload is neither a list nor a JSON-encoded
    list — callers should treat that as a missing-fields row.
    """
    if isinstance(raw_fields, list):
        return raw_fields
    if isinstance(raw_fields, str):
        try:
            parsed = json.loads(raw_fields)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, list):
            return parsed
    return None


# ─────────────────────────── core logic ───────────────────────────


def _bucket_docs_by_year(
    extracted_rows: Iterable[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Group extraction rows by ``period_ending.year``.

    Each input row must look like ``{"document_id": str|UUID, "doc_type":
    str, "fields": list|str}``. Rows without a discoverable period_ending
    are silently dropped from the year map (they still exist as
    documents — the UI just can't attribute them to a year).
    """
    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in extracted_rows:
        fields = _coerce_fields_list(row.get("fields"))
        if fields is None:
            continue
        period_ending = _extract_period_ending(fields)
        if period_ending is None:
            continue
        period_type = _extract_period_type(fields) or "unknown"
        doc_id = row.get("document_id")
        entry: dict[str, Any] = {
            "doc_id": str(doc_id) if doc_id is not None else None,
            "doc_type": (row.get("doc_type") or "").upper() or None,
            "period_type": period_type,
            "period_ending": period_ending.isoformat(),
        }
        by_year.setdefault(period_ending.year, []).append(entry)
    return by_year


def _months_covered(year_entries: list[dict[str, Any]]) -> set[int]:
    """Return the set of months (1..12) covered by monthly P&Ls in a
    year's entries. Annual / YTD / quarterly entries are ignored — only
    rows whose ``period_type`` is in the monthly family count.

    A single monthly P&L covers exactly its ``period_ending.month``;
    a future enhancement could parse ``period_start`` to support
    multi-month rollups, but every Extractor-emitted monthly we've seen
    closes the month equal to ``period_ending``.
    """
    covered: set[int] = set()
    for entry in year_entries:
        if entry.get("period_type") not in _MONTHLY_PERIOD_TYPES:
            continue
        period_ending = _parse_iso_date(entry.get("period_ending"))
        if period_ending is None:
            continue
        covered.add(period_ending.month)
    return covered


def _has_annual_coverage(year_entries: list[dict[str, Any]]) -> bool:
    """True when one of the year's entries is annual / TTM (period_type
    rank 0 or 1)."""
    return any(
        e.get("period_type") in _ANNUAL_PERIOD_TYPES for e in year_entries
    )


def _has_summary_coverage(year_entries: list[dict[str, Any]]) -> bool:
    """True when the year has YTD or quarterly coverage (i.e. it's not
    silent, but it's also not annual). Used to label ``summary_only``
    gaps."""
    for e in year_entries:
        pt = e.get("period_type") or ""
        if pt in {"ytd", "year_to_date", "quarterly", "quarter"}:
            return True
    return False


def _infer_fiscal_year_end_month(
    year_coverage: dict[int, list[dict[str, Any]]],
) -> int:
    """Best-effort guess at the deal's fiscal year-end month from the
    annual entries we've seen. Returns 12 (calendar year) when we can't
    tell — that keeps the dismissible flag off for the common case.

    A property whose annual T-12s all close on, say, 2024-06-30 has a
    June fiscal year-end. We mode-vote across the annual entries to
    smooth over the occasional rogue TTM.
    """
    months: Counter[int] = Counter()
    for entries in year_coverage.values():
        for entry in entries:
            if entry.get("period_type") not in _ANNUAL_PERIOD_TYPES:
                continue
            period_ending = _parse_iso_date(entry.get("period_ending"))
            if period_ending is None:
                continue
            months[period_ending.month] += 1
    if not months:
        return 12
    # Mode wins; tie → calendar year (12) when present.
    most_common_month, _ = months.most_common(1)[0]
    return most_common_month


def _detect_sequential_gaps(
    year_coverage: dict[int, list[dict[str, Any]]],
    *,
    lookback_years: int,
    current_year: int,
    dismissible: bool,
) -> list[CoverageGap]:
    """Find years in the look-back window with zero coverage.

    Window is ``max(earliest_covered_year, current_year - lookback_years)``
    to ``current_year`` inclusive. We don't extrapolate before the
    earliest year the analyst has actually uploaded — if every document
    is from 2022 onward, we don't whine about "missing 2018".
    """
    if not year_coverage:
        return []
    earliest = min(year_coverage.keys())
    window_start = max(earliest, current_year - lookback_years)
    gaps: list[CoverageGap] = []
    for year in range(window_start, current_year + 1):
        if year in year_coverage:
            continue
        gaps.append(
            CoverageGap(
                gap_type="year_missing",
                year=year,
                message=f"Missing {year} financials",
                severity="warn",
                dismissible=dismissible,
            )
        )
    return gaps


def _detect_detail_gaps(
    year_coverage: dict[int, list[dict[str, Any]]],
    *,
    current_year: int,
    dismissible: bool,
) -> list[CoverageGap]:
    """For every year that has SOME coverage, flag the detail-level
    holes:

    * Annual present but zero monthly entries → ``annual_no_detail``.
    * Monthly entries present but < 12 months covered → ``month_partial``.
    * Only YTD / quarterly summary, no annual, no monthly →
      ``summary_only``.

    The current year is special-cased: we don't flag ``month_partial``
    for it because the year isn't done yet (we're not going to complain
    about missing December 2026 in June 2026). Annual_no_detail is still
    flagged though — Sam's example was exactly "2024 annual T-12 but no
    monthly breakdown".
    """
    gaps: list[CoverageGap] = []
    for year in sorted(year_coverage.keys()):
        entries = year_coverage[year]
        months = _months_covered(entries)
        annual = _has_annual_coverage(entries)
        summary = _has_summary_coverage(entries)

        if annual and not months:
            gaps.append(
                CoverageGap(
                    gap_type="annual_no_detail",
                    year=year,
                    message=(
                        f"Have {year} annual T-12 but no monthly "
                        "breakdown"
                    ),
                    severity="info",
                    dismissible=dismissible,
                )
            )
            continue

        if months and len(months) < 12:
            missing = sorted(set(range(1, 13)) - months)
            # For the current year, partial coverage is expected — only
            # flag if it's an older year (which should be fully closed).
            if year < current_year:
                gaps.append(
                    CoverageGap(
                        gap_type="month_partial",
                        year=year,
                        message=(
                            f"Have {year} monthly detail through "
                            f"month {max(months)}; missing months "
                            f"{missing}"
                        ),
                        severity="warn",
                        months_missing=missing,
                        dismissible=dismissible,
                    )
                )
            continue

        if not annual and not months and summary:
            gaps.append(
                CoverageGap(
                    gap_type="summary_only",
                    year=year,
                    message=(
                        f"Only summary coverage for {year}; no annual "
                        "T-12 and no monthly detail"
                    ),
                    severity="warn",
                    dismissible=dismissible,
                )
            )
    return gaps


# ─────────────────────────── DB load ───────────────────────────


async def _load_extraction_rows(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Read every extracted P&L row for the deal, tenant-scoped.

    We re-filter on ``er.tenant_id`` AND ``d.tenant_id`` (belt + braces)
    because the join could otherwise leak rows whose document moved to
    another tenant — defensive after the P0 fix in commit ``2a8ed64``.
    """
    placeholders = ", ".join(f":t{i}" for i, _ in enumerate(_PNL_DOC_TYPES))
    params: dict[str, Any] = {
        "deal": deal_id,
        "tenant": tenant_id,
    }
    for i, t in enumerate(_PNL_DOC_TYPES):
        params[f"t{i}"] = t

    rows = await session.execute(
        text(
            f"""
            SELECT er.document_id,
                   er.fields,
                   d.doc_type,
                   d.status
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.tenant_id = :tenant
               AND d.status = 'EXTRACTED'
               AND UPPER(COALESCE(d.doc_type, '')) IN ({placeholders})
             ORDER BY er.created_at DESC
            """
        ),
        params,
    )
    return [dict(r._mapping) for r in rows.fetchall()]


# ─────────────────────────── public entry point ───────────────────────────


async def audit_document_coverage(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
    current_year: int | None = None,
) -> DocumentCoverage:
    """Produce the sequential + detail-level coverage report for a deal.

    Parameters
    ----------
    session
        AsyncSession bound to the worker DB.
    deal_id, tenant_id
        Stringified UUIDs. Every query is tenant-scoped — a tenant can
        only see its own documents.
    lookback_years
        How many years back from ``current_year`` to demand contiguous
        coverage. Defaults to 5 per the Wave 1 decision; the API accepts
        an override.
    current_year
        Override "today's year" — used by tests to pin determinism.
        Defaults to the current calendar year.

    Returns
    -------
    DocumentCoverage
        ``year_coverage`` is sorted-on-read in the API layer; the
        in-memory shape is dict[int, list[dict]] for cheap lookups.
    """
    if current_year is None:
        current_year = date.today().year
    lookback_years = max(1, int(lookback_years))

    extracted_rows = await _load_extraction_rows(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    year_coverage = _bucket_docs_by_year(extracted_rows)

    # Non-calendar FY → all gaps for this deal flip to dismissible per
    # Wave 1 decision. We infer the FY-end month from the annual rows we
    # have; absent any annual coverage we conservatively assume calendar
    # year (months = 12).
    fy_end_month = _infer_fiscal_year_end_month(year_coverage)
    non_calendar_fy = fy_end_month != 12

    sequential = _detect_sequential_gaps(
        year_coverage,
        lookback_years=lookback_years,
        current_year=current_year,
        dismissible=non_calendar_fy,
    )
    detail = _detect_detail_gaps(
        year_coverage,
        current_year=current_year,
        dismissible=non_calendar_fy,
    )

    return DocumentCoverage(
        deal_id=deal_id,
        year_coverage=year_coverage,
        gaps=sequential + detail,
        lookback_years=lookback_years,
    )


# Re-exported so the API layer can pick the right dataclass-to-pydantic
# bridge without importing private symbols.
__all__ = [
    "DEFAULT_LOOKBACK_YEARS",
    "CoverageGap",
    "DocumentCoverage",
    "audit_document_coverage",
]


def _validate_uuid(value: str | UUID) -> str:
    """Coerce ``UUID | str`` to canonical string; raises ValueError on
    garbage input. Used by the API layer before hitting the DB to keep
    SQL parameter binding simple."""
    if isinstance(value, UUID):
        return str(value)
    return str(UUID(str(value)))
