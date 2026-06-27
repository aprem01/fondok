"""STR comp-set drift detection across years.

Wave 1 roadmap item #8. Eshan's exact framing on the June 25 2026 call:
"In 2024 you had Hilton South Beach in your comp set; in 2025 it was
replaced with W South Beach. Fondok could make those notes on the side."

When the same property's STR Trend report is uploaded across multiple
years, the report's "Competitive Set" changes — CoStar replaces tired
comparables with new builds, brand-flag conversions land, properties
trade. The diff itself is *underwriting signal*: a comp-set churn from
Hilton South Beach (full-service legacy) to W South Beach (luxury
lifestyle) changes the RevPAR-index narrative even when the index
number looks identical year-over-year.

Implementation notes
====================
- Properties are matched across years on `name`.
- Exact match (case-insensitive, whitespace-collapsed) is treated as
  ``unchanged`` and never flagged.
- For the rest, we compute Python-stdlib ``difflib.SequenceMatcher``
  ratios pairwise between unmatched names in year_from vs year_to:
    * ratio >= 0.80 → ``uncertain_matches`` (flag for analyst review,
      do NOT count as drift). Wave-1 decision (locked 2026-06-27)
      pinned the threshold at 80%.
    * ratio <  0.80 → unmatched, counts as drift.
- Diffs are emitted per consecutive year pair, ascending. Three years
  (2023, 2024, 2025) → two drifts: (2023→2024) and (2024→2025).

The service queries its own SQL rather than reusing
``documents._aggregate_market_data`` because that aggregator keeps only
the most-recent STR_TREND extraction per deal — the drift detector
needs to see ALL of them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Fuzzy match threshold for "this might be the same property under a
# slightly different name." Wave 1 decision (locked 2026-06-27): 80%
# Levenshtein-style similarity. We use difflib.SequenceMatcher's ratio
# instead of true Levenshtein to avoid a new dependency — for short
# hotel-name strings the two measures track each other closely.
FUZZY_MATCH_THRESHOLD = 0.80


# ─────────────────────────── data shapes ───────────────────────────


@dataclass
class CompSetEntry:
    """One competitor row inside a single year's STR_TREND extraction.

    Mirrors the dataclass shape Eshan asked for in the roadmap. The
    Pydantic response model below carries the same fields plus
    JSON-safe semantics for the FastAPI surface.
    """

    name: str
    keys: int | None = None


@dataclass
class CompSetDrift:
    year_from: int
    year_to: int
    added: list[CompSetEntry] = field(default_factory=list)
    removed: list[CompSetEntry] = field(default_factory=list)
    unchanged: list[CompSetEntry] = field(default_factory=list)
    # Each entry: {"from_name": str, "to_name": str, "similarity": float}
    uncertain_matches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CompSetDriftReport:
    deal_id: str
    drifts: list[CompSetDrift] = field(default_factory=list)


# ─────────────────────────── Pydantic response shapes ─────────────────


class CompSetEntryOut(BaseModel):
    """JSON-safe competitor entry returned by the API."""

    model_config = ConfigDict(extra="forbid")

    name: str
    keys: int | None = None


class UncertainMatch(BaseModel):
    """A fuzzy-matched pair that crossed the 80% similarity bar.

    Surfaced to the analyst as "might be the same property under a
    slightly different name" — they decide whether to treat it as
    drift or as a rename.
    """

    model_config = ConfigDict(extra="forbid")

    from_name: str
    to_name: str
    similarity: float = Field(ge=0.0, le=1.0)


class CompSetDriftOut(BaseModel):
    """One consecutive-year diff."""

    model_config = ConfigDict(extra="forbid")

    year_from: int
    year_to: int
    added: list[CompSetEntryOut] = Field(default_factory=list)
    removed: list[CompSetEntryOut] = Field(default_factory=list)
    unchanged: list[CompSetEntryOut] = Field(default_factory=list)
    uncertain_matches: list[UncertainMatch] = Field(default_factory=list)


class CompSetDriftReportOut(BaseModel):
    """Full report for a deal — N years produce N-1 drifts, ascending."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    drifts: list[CompSetDriftOut] = Field(default_factory=list)


# ─────────────────────────── helpers ───────────────────────────


def _canonical(name: str) -> str:
    """Lower + collapse whitespace so 'Hilton  South Beach' equals
    'hilton south beach' on the exact-match path. Punctuation is left
    alone — 'Hilton, South Beach' is *not* canonically equal to
    'Hilton South Beach' so the fuzzy path can score them and decide.
    """
    return " ".join(name.lower().split())


def _similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on canonical forms. Symmetric in practice."""
    return SequenceMatcher(None, _canonical(a), _canonical(b)).ratio()


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_fields(raw: Any) -> list[dict[str, Any]]:
    """The ``extraction_results.fields`` column is JSONB on Postgres
    but TEXT on SQLite — the test suite runs on SQLite. Accept either."""
    if isinstance(raw, list):
        return [f for f in raw if isinstance(f, dict)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [f for f in parsed if isinstance(f, dict)]
    return []


def _extract_compset_and_year(
    fields: list[dict[str, Any]],
) -> tuple[int | None, list[CompSetEntry]]:
    """Pull ``report_year`` + compset rows out of one extraction's fields.

    The extractor emits flat rows of ``{field_name, value, ...}``. Comp
    set entries land at ``ttm_performance.compset.<n>.{name,keys,...}``.
    The schema also carries ``str_trend.report_year`` at the document
    level.

    Returns (report_year, [CompSetEntry...]). report_year is None when
    the extraction predates the str_trend.report_year schema field — in
    that case the caller skips this extraction (we can't sort it into
    the timeline).
    """
    report_year: int | None = None
    compset_by_idx: dict[int, dict[str, Any]] = {}

    for f in fields:
        name = (f.get("field_name") or "").strip().lower()
        if not name:
            continue
        value = f.get("value")

        if name == "str_trend.report_year":
            report_year = _coerce_int(value)
            continue

        if not name.startswith("ttm_performance.compset."):
            continue
        rest = name[len("ttm_performance.compset.") :]
        try:
            idx_str, attr = rest.split(".", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            continue
        compset_by_idx.setdefault(idx, {})[attr] = value

    entries: list[CompSetEntry] = []
    for _idx in sorted(compset_by_idx.keys()):
        row = compset_by_idx[_idx]
        raw_name = row.get("name")
        if raw_name is None:
            continue
        nm = str(raw_name).strip()
        if not nm:
            continue
        entries.append(CompSetEntry(name=nm, keys=_coerce_int(row.get("keys"))))

    return report_year, entries


def _diff_year_pair(
    year_from: int,
    from_entries: list[CompSetEntry],
    year_to: int,
    to_entries: list[CompSetEntry],
) -> CompSetDrift:
    """Diff two comp sets into added / removed / unchanged / uncertain.

    Algorithm:
      1. Exact (case-insensitive, whitespace-collapsed) name matches
         → `unchanged`. Both sides are then taken out of contention.
      2. Greedy fuzzy match on the leftovers: for each remaining
         from_entry, find the highest-scoring still-unmatched to_entry.
         If that score >= 0.80 → record an ``uncertain_match`` and
         retire both sides. Otherwise leave them unmatched.
      3. Whatever's left in from = ``removed``; whatever's left in
         to = ``added``.
    """
    drift = CompSetDrift(year_from=year_from, year_to=year_to)

    # Index `to` by canonical name to find exact matches fast. We allow
    # duplicate names (unusual but possible if the extractor double-
    # counts) by storing a list.
    to_canon: dict[str, list[int]] = {}
    for i, e in enumerate(to_entries):
        to_canon.setdefault(_canonical(e.name), []).append(i)

    to_matched: set[int] = set()
    from_unmatched_idx: list[int] = []

    for i, e in enumerate(from_entries):
        candidates = to_canon.get(_canonical(e.name), [])
        # First unused candidate wins.
        hit_idx: int | None = None
        for cand in candidates:
            if cand not in to_matched:
                hit_idx = cand
                break
        if hit_idx is not None:
            to_matched.add(hit_idx)
            # Prefer the "to" copy in unchanged so any updated keys
            # carry forward. Falls back to the "from" copy if to.keys
            # is None.
            chosen = to_entries[hit_idx]
            if chosen.keys is None and e.keys is not None:
                chosen = CompSetEntry(name=chosen.name, keys=e.keys)
            drift.unchanged.append(chosen)
        else:
            from_unmatched_idx.append(i)

    # Fuzzy-match the leftovers. Greedy: pick the highest-scoring pair
    # first so a name like "Hilton SoBe" doesn't accidentally bind to a
    # weak partner before a stronger one comes around.
    pairs: list[tuple[float, int, int]] = []  # (score, from_idx, to_idx)
    for fi in from_unmatched_idx:
        for ti, te in enumerate(to_entries):
            if ti in to_matched:
                continue
            score = _similarity(from_entries[fi].name, te.name)
            if score >= FUZZY_MATCH_THRESHOLD:
                pairs.append((score, fi, ti))
    # Highest score first.
    pairs.sort(key=lambda t: t[0], reverse=True)

    from_consumed: set[int] = set()
    for score, fi, ti in pairs:
        if fi in from_consumed or ti in to_matched:
            continue
        drift.uncertain_matches.append(
            {
                "from_name": from_entries[fi].name,
                "to_name": to_entries[ti].name,
                "similarity": round(score, 4),
            }
        )
        from_consumed.add(fi)
        to_matched.add(ti)

    # Anything still on either side is true drift.
    for fi in from_unmatched_idx:
        if fi in from_consumed:
            continue
        drift.removed.append(from_entries[fi])
    for ti, te in enumerate(to_entries):
        if ti in to_matched:
            continue
        drift.added.append(te)

    return drift


# ─────────────────────────── public API ───────────────────────────


async def compute_comp_set_drift(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> CompSetDriftReport:
    """Compute the comp-set drift report for a deal.

    Queries every STR_TREND extraction belonging to (deal_id, tenant_id),
    parses out ``str_trend.report_year`` and the indexed ``compset``
    entries, then emits one ``CompSetDrift`` per consecutive year pair.

    Tenant scoping is mandatory and enforced both in the SQL (``er``
    AND ``d`` rows must match :tenant) and at the call site
    (``Depends(get_tenant_id)``). 404s leak no cross-tenant existence
    signal — see commit ``2a8ed64``.

    When fewer than 2 distinct ``report_year`` values are present we
    return an empty ``drifts`` list — no comparison is possible and the
    UI should render "awaiting multi-year history" rather than a crash.
    """
    rows = await session.execute(
        text(
            """
            SELECT er.fields, er.created_at, er.document_id
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.tenant_id = :tenant
               AND UPPER(COALESCE(d.doc_type, '')) = 'STR_TREND'
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": deal_id, "tenant": tenant_id},
    )
    materialized = [dict(r._mapping) for r in rows.fetchall()]

    # report_year → most-recent compset list. If the analyst re-extracted
    # the same year's report (fixed a typo, re-ran with a new schema)
    # the newest extraction wins because we iterate created_at DESC and
    # only set a slot the first time we see its year.
    by_year: dict[int, list[CompSetEntry]] = {}
    for row in materialized:
        fields = _parse_fields(row.get("fields"))
        report_year, entries = _extract_compset_and_year(fields)
        if report_year is None:
            # No year → can't place this on the timeline. Historical
            # extractions predating the schema update fall through here.
            logger.debug(
                "comp_set_drift: skipping extraction without "
                "str_trend.report_year (document_id=%s)",
                row.get("document_id"),
            )
            continue
        if not entries:
            # Year is known but the report had no comp set — leave it
            # in: an empty year is a valid endpoint (e.g. comp set was
            # added between years), and we want the diff against the
            # neighbor to show every property as added/removed.
            by_year.setdefault(report_year, [])
            continue
        by_year.setdefault(report_year, entries)

    drifts: list[CompSetDrift] = []
    years_sorted = sorted(by_year.keys())
    for y_from, y_to in pairwise(years_sorted):
        drifts.append(
            _diff_year_pair(
                y_from, by_year[y_from], y_to, by_year[y_to]
            )
        )

    return CompSetDriftReport(deal_id=deal_id, drifts=drifts)


def drift_report_to_pydantic(
    report: CompSetDriftReport,
) -> CompSetDriftReportOut:
    """Convert the internal dataclass report to a JSON-safe Pydantic
    model. Kept as a separate helper so callers that want the
    dataclass shape (engines, tests) don't pay the conversion cost."""
    return CompSetDriftReportOut(
        deal_id=UUID(report.deal_id),
        drifts=[
            CompSetDriftOut(
                year_from=d.year_from,
                year_to=d.year_to,
                added=[CompSetEntryOut(name=e.name, keys=e.keys) for e in d.added],
                removed=[
                    CompSetEntryOut(name=e.name, keys=e.keys) for e in d.removed
                ],
                unchanged=[
                    CompSetEntryOut(name=e.name, keys=e.keys) for e in d.unchanged
                ],
                uncertain_matches=[
                    UncertainMatch(
                        from_name=str(m["from_name"]),
                        to_name=str(m["to_name"]),
                        similarity=float(m["similarity"]),
                    )
                    for m in d.uncertain_matches
                ],
            )
            for d in report.drifts
        ],
    )
