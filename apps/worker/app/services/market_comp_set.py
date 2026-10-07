"""Market tab comp-set derivation + TTM comp-set blend provenance.

FON-61 external-tester findings this module answers:

* **E-009** — the STR Trend roster listed 5 hotels / 419 keys, one of them
  closed (STR prints it as ``"Closed - Blue Moon"`` with 0 rooms). The
  Market tab showed the active hotels' 344 keys under a "5 hotels" label,
  because the count came from the extracted ``comp_set.comp_set_size``
  rollup (which counts the closed hotel) while the keys were summed over
  the roster. ONE derivation (:func:`derive_comp_set`) now feeds both the
  count and the keys: the ACTIVE hotels of the roster.

* **E-007** — the "TTM · comp-set blend" occupancy / ADR had no visible
  definition. :func:`derive_ttm_blend` records the exact extraction rows
  (document, field, page) and the period behind the blend so the UI can
  print a methodology line from the actual inputs rather than prose.

Where "closed" comes from — and only from
-----------------------------------------
A hotel is marked closed when, and only when, the extraction says so
explicitly:

1. ``ttm_performance.compset.<n>.status`` carries ``closed`` (the STR
   template extractor emits this for roster rows STR labels closed; an
   LLM extraction may emit it too), or
2. the roster name AS REPORTED carries STR's own closed label — STR prints
   closed competitors as ``"Closed - <name>"`` in the Response-tab roster.
   The label is read from the extracted name verbatim; it is STR's
   statement, not Fondok's inference.

A 0-room row without either marker is NOT treated as closed — it stays an
active hotel contributing 0 keys. No other heuristic exists, and the
derivation says which marker it used (``status_source``), which document
carried it (``status_doc_name``), or that it found none
(``status_available=False``).

The roster is the UNION of every STR / STR_TREND extraction on the deal
(live case: the May trend report lists "Blue Moon Hotel · 75 rooms" with
no marker while the July daily report lists "Closed - Blue Moon Hotel · 0
rooms" — reading only the newest report could never see the closure).
Hotels are matched across reports in this order (first rule that applies):

1. **STR id** — when BOTH the row and a unioned hotel carry one, that id
   decides (two different ids never merge, whatever the names say);
2. **normalised name** — the closed label stripped, lower-cased,
   punctuation / whitespace collapsed;
3. **alias rule** (conservative) — the row has the SAME positive key
   count as the hotel AND the shorter normalised name's first two tokens
   are a prefix of the other's ("the betsy hotel" ≡ "the betsy south
   beach"; live case: the Dec 2023 report named the Betsy differently and
   the union listed it twice). Every other name the hotel was listed under
   is recorded in ``merged_names`` so the UI can show the alias.

A hotel is closed if ANY report marks it; its keys come from the most
recent report (by report period, below) that lists a positive room count
and are excluded from the active totals when closed.

Which STR report is "most recent" — by REPORT PERIOD, never by upload
----------------------------------------------------------------------
Live (2026-10-07): three STR files were re-extracted within three seconds
(the May 2025 trend at 21:03:31, the Dec 2023 trend at 21:03:33, the July
2025 daily at 21:03:34) and the TTM blend switched to the Dec 2023 report,
because "newest" meant ``extraction_results.created_at`` — whichever file
was re-extracted last won. :func:`build_str_inputs` now orders the STR
extractions by the REPORT's period end, resolved per extraction from, in
order (:func:`resolve_report_period_end`):

1. an extracted period-end field (``ttm_performance.period_end``,
   ``*.period_ending``, ``str_trend.report_date`` …);
2. the last month of the subject monthly series
   (``ttm_performance.subject.monthly.<YYYY_MM>.*``);
3. an extracted ``period_start`` plus ``months``;
4. the document's ``report_as_of`` date (``documents.report_as_of``);
5. a date token in the filename (``ANG-20250500`` → 2025-05,
   ``56387-20250713`` → 2025-07-13, ``… Jun-2026.xlsx`` → 2026-06);
6. a YEAR-ONLY answer any of the above gave (a year-precision
   ``report_as_of`` — the STR family's "31 December of report_year" — a bare
   year in the filename, ``str_trend.report_year``), kept behind every
   month-precise answer because it cannot order within a year;
7. and only then ``created_at`` (the rows' arrival order, newest first).

The subject TTM and the penetration indices of the blend come from ONE
report — the first in that order carrying them — and the blend's
``report_year`` / ``documents`` describe that report. The resolved period
end and its basis are exposed per report (``ordering``) and for the blend
(``period_end_used`` / ``ordering_basis``) so the choice is auditable.

Pure functions — no DB, no I/O.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Literal

from .market_fields import FieldRef, FieldRow, coerce_float, coerce_int

# STR's roster label for a closed competitor: "Closed - <name>" (also seen
# with an en/em dash or a colon). Anchored at the start of the name.
STR_CLOSED_LABEL_RE = re.compile("^\\s*closed\\s*[-\\u2013\\u2014:]\\s*", re.IGNORECASE)

_CLOSED_STATUS_VALUES = frozenset(
    {"closed", "close", "closed_hotel", "permanently_closed", "permanently closed", "shut"}
)
_OPEN_STATUS_VALUES = frozenset({"open", "active", "operating", "open_hotel", "operational"})

HotelStatus = Literal["active", "closed"]
StatusSource = Literal["extracted_status_field", "str_closed_label"]
Basis = Literal["active_roster", "reported_rollup", "none"]

# Extraction field paths (STR / STR_TREND) the blend reads — exact names.
COMPSET_PREFIX = "ttm_performance.compset."
SUBJECT_MONTHLY_PREFIX = "ttm_performance.subject.monthly."
SUBJECT_OCC = "ttm_performance.subject.occupancy_pct"
SUBJECT_ADR = "ttm_performance.subject.adr_usd"
SUBJECT_REVPAR = "ttm_performance.subject.revpar_usd"
INDEX_MPI = "ttm_performance.indices.mpi_occupancy_index"
INDEX_ARI = "ttm_performance.indices.ari_adr_index"
INDEX_RGI = "ttm_performance.indices.rgi_revpar_index"
ROLLUP_SIZE = "comp_set.comp_set_size"
ROLLUP_KEYS = "comp_set.total_keys"
REPORT_YEAR = "str_trend.report_year"

TTM_BLEND_METHOD = (
    "Comp-set Occupancy = subject TTM Occupancy ÷ MPI; comp-set ADR = subject "
    "TTM ADR ÷ ARI; comp-set RevPAR = subject TTM RevPAR ÷ RGI — the STR "
    "penetration indices are subject ÷ comp set, so the blend is recovered "
    "exactly. STR's comp-set figures are room-night totals across the active "
    "comp-set hotels (larger hotels weigh more); Fondok applies no weighting "
    "of its own."
)


# ───────────────────────────── comp set (E-009) ─────────────────────────────


@dataclass(frozen=True)
class CompSetHotel:
    index: int
    #: Display name — STR's ``"Closed - "`` label stripped when present.
    name: str
    #: The roster name exactly as extracted (keeps STR's label visible). When
    #: a report marks the hotel closed this is THAT report's name.
    name_as_reported: str
    keys: int | None
    status: HotelStatus
    #: Which explicit marker said "closed"; None when none was found (active).
    status_source: StatusSource | None
    #: STR's property id (``ttm_performance.compset.<n>.str_id``) when extracted.
    str_id: str | None = None
    #: The document that carried the closed marker.
    status_doc_name: str | None = None
    status_doc_id: str | None = None
    status_page: int | None = None
    #: The document the key count was read from — the most recent report (by
    #: report period) listing a positive count.
    keys_doc_name: str | None = None
    #: Every roster document that lists this hotel, most recent report first.
    reports: tuple[str, ...] = ()
    #: Other names this hotel was listed under in older reports and merged
    #: here (by STR id or the alias rule) — the UI shows them as "also
    #: listed as …". Empty when every report used the same name.
    merged_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompSetDerivation:
    hotels: list[CompSetHotel]
    #: Hotels counted in the comp set (active only) — the headline "N hotels".
    active_count: int | None
    #: Keys of the counted hotels — the headline "N keys". Same set as the count.
    active_keys: int | None
    closed_count: int
    closed_names: list[str]
    count_basis: Basis
    keys_basis: Basis
    #: True when at least one explicit closed marker was found in the roster.
    status_available: bool
    #: The report's own rollups, surfaced for transparency — never the headline.
    reported_comp_set_size: int | None
    reported_total_keys: int | None
    source_doc_name: str | None
    source_doc_id: str | None
    source_page: int | None
    note: str
    #: Every STR roster document unioned, most recent report period first.
    documents: list[str] = field(default_factory=list)
    #: Every STR extraction on the deal in the order the union read them
    #: (most recent report period first) with the period end each resolved to.
    ordering: list[StrReportOrder] = field(default_factory=list)


@dataclass(frozen=True)
class RosterSnapshot:
    """One extraction's ``ttm_performance.compset.<n>.*`` rows."""

    rows: Mapping[int, Mapping[str, Any]]
    doc_name: str | None = None
    doc_id: str | None = None
    page: int | None = None
    extraction_id: str | None = None


def classify_hotel_status(
    name_as_reported: str, status_raw: Any = None
) -> tuple[HotelStatus, StatusSource | None]:
    """Explicit markers only — see the module docstring."""
    if isinstance(status_raw, str):
        s = status_raw.strip().lower().replace("-", "_")
        if s in _CLOSED_STATUS_VALUES:
            return "closed", "extracted_status_field"
        if s in _OPEN_STATUS_VALUES:
            return "active", "extracted_status_field"
    if STR_CLOSED_LABEL_RE.match(name_as_reported or ""):
        return "closed", "str_closed_label"
    return "active", None


def display_name(name_as_reported: str) -> str:
    return STR_CLOSED_LABEL_RE.sub("", name_as_reported or "").strip() or (name_as_reported or "").strip()


_NAME_NOISE_RE = re.compile(r"[^a-z0-9]+")


def normalized_hotel_name(name_as_reported: str) -> str:
    """Union key for a hotel without an STR id: the closed label stripped,
    lower-cased, punctuation and whitespace collapsed."""
    return _NAME_NOISE_RE.sub(" ", display_name(name_as_reported).lower()).strip()


def names_are_aliases(norm_a: str, norm_b: str) -> bool:
    """The conservative alias rule for two DIFFERENT normalised roster names
    (the caller has already checked key counts match): the shorter name's
    first two tokens are a prefix of the other's. ``"the betsy hotel"`` ≡
    ``"the betsy south beach"``; ``"the tony hotel of south beach"`` is
    not an alias of ``"the betsy south beach"`` (second token differs); a
    one-token name never aliases anything.
    """
    if not norm_a or not norm_b or norm_a == norm_b:
        return False
    a, b = norm_a.split(), norm_b.split()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 2:
        return False
    return longer[:2] == shorter[:2]


def _str_id(raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    s = str(raw).strip()
    return s if s and s.lower() not in {"none", "nan"} else None


@dataclass
class _UnionHotel:
    index: int
    name: str
    name_as_reported: str
    str_id: str | None = None
    keys: int | None = None
    keys_doc_name: str | None = None
    status: HotelStatus = "active"
    status_source: StatusSource | None = None
    status_doc_name: str | None = None
    status_doc_id: str | None = None
    status_page: int | None = None
    explicit_marker: bool = False
    reports: list[str] = field(default_factory=list)
    #: Every normalised name this hotel has been listed under.
    norms: set[str] = field(default_factory=set)
    merged_names: list[str] = field(default_factory=list)

    def freeze(self) -> CompSetHotel:
        return CompSetHotel(
            index=self.index, name=self.name, name_as_reported=self.name_as_reported,
            keys=self.keys, status=self.status, status_source=self.status_source,
            str_id=self.str_id, status_doc_name=self.status_doc_name,
            status_doc_id=self.status_doc_id, status_page=self.status_page,
            keys_doc_name=self.keys_doc_name, reports=tuple(self.reports),
            merged_names=tuple(self.merged_names),
        )


def _ids_compatible(sid: str | None, hotel: _UnionHotel) -> bool:
    """Two different STR ids are two hotels, whatever the names say."""
    return not (sid and hotel.str_id and hotel.str_id != sid)


def derive_comp_set(
    roster: Mapping[int, Mapping[str, Any]],
    *,
    reported_comp_set_size: Any = None,
    reported_total_keys: Any = None,
    source_doc_name: str | None = None,
    source_doc_id: str | None = None,
    source_page: int | None = None,
) -> CompSetDerivation:
    """The ONE comp-set derivation for a single roster — see
    :func:`derive_comp_set_union` for the multi-report form."""
    return derive_comp_set_union(
        [RosterSnapshot(rows=roster, doc_name=source_doc_name, doc_id=source_doc_id, page=source_page)],
        reported_comp_set_size=reported_comp_set_size,
        reported_total_keys=reported_total_keys,
    )


def derive_comp_set_union(
    snapshots: Sequence[RosterSnapshot],
    *,
    reported_comp_set_size: Any = None,
    reported_total_keys: Any = None,
) -> CompSetDerivation:
    """The ONE comp-set derivation: count AND keys over the active roster,
    unioned across every STR report on the deal.

    ``snapshots`` are most-recent-report-period first (``build_str_inputs``
    orders them; see the module docstring). Each carries ``{n: {"name",
    "keys", "status", "str_id"}}`` as bucketed from
    ``ttm_performance.compset.<n>.*`` rows of ONE extraction. A row joins a
    unioned hotel by (1) ``str_id`` when both carry one, else (2) the
    normalised name, else (3) the alias rule — same positive key count and
    the shorter name's first two tokens prefix the other's (see
    :func:`names_are_aliases`); names a hotel was merged under are kept in
    ``merged_names``. A hotel is closed if ANY report marks it (status field
    or STR's "Closed - " label — a 0-room row alone never does); keys come
    from the first report in that order listing a positive count. Rows with
    neither a name nor a positive key count are ignored. Falls back to the
    report's rollups only when no roster was extracted at all, and says so.
    """
    size_reported = coerce_int(reported_comp_set_size)
    keys_reported = coerce_int(reported_total_keys)
    if size_reported is not None and size_reported <= 0:
        size_reported = None
    if keys_reported is not None and keys_reported <= 0:
        keys_reported = None

    union: list[_UnionHotel] = []
    by_id: dict[str, _UnionHotel] = {}
    by_name: dict[str, _UnionHotel] = {}
    documents: list[str] = []
    for snap in snapshots:
        if snap.doc_name and snap.doc_name not in documents and snap.rows:
            documents.append(snap.doc_name)
        for idx in sorted(snap.rows):
            entry = snap.rows[idx]
            raw_name = entry.get("name")
            name_as_reported = str(raw_name).strip() if raw_name is not None else ""
            keys = coerce_int(entry.get("keys"))
            if keys is not None and keys < 0:
                keys = None
            sid = _str_id(entry.get("str_id"))
            if not name_as_reported and not (keys and keys > 0):
                continue
            norm = normalized_hotel_name(name_as_reported) if name_as_reported else ""
            # 1. STR id on both sides.
            hotel = by_id.get(sid) if sid else None
            # 2. Normalised name (unless the ids contradict it).
            if hotel is None and norm:
                candidate = by_name.get(norm)
                if candidate is not None and _ids_compatible(sid, candidate):
                    hotel = candidate
            # 3. Alias rule: same positive key count + two-token prefix.
            if hotel is None and norm and keys and keys > 0:
                hotel = next(
                    (
                        h for h in union
                        if h.keys == keys
                        and _ids_compatible(sid, h)
                        and any(names_are_aliases(norm, n) for n in h.norms)
                    ),
                    None,
                )
            if hotel is None:
                hotel = _UnionHotel(
                    index=len(union) + 1,
                    name=display_name(name_as_reported) if name_as_reported else f"Hotel {idx}",
                    name_as_reported=name_as_reported,
                    str_id=sid,
                )
                union.append(hotel)
            elif norm and norm not in hotel.norms:
                # Merged under another name (by id or by the alias rule):
                # keep the alias visible.
                alias = display_name(name_as_reported)
                if alias and alias != hotel.name and alias not in hotel.merged_names:
                    hotel.merged_names.append(alias)
            if sid and sid not in by_id:
                by_id[sid] = hotel
                hotel.str_id = hotel.str_id or sid
            if norm:
                hotel.norms.add(norm)
                if norm not in by_name:
                    by_name[norm] = hotel
            if snap.doc_name and snap.doc_name not in hotel.reports:
                hotel.reports.append(snap.doc_name)
            status, source = classify_hotel_status(name_as_reported, entry.get("status"))
            if source is not None:
                hotel.explicit_marker = True
            if status == "closed" and hotel.status != "closed":
                hotel.status = "closed"
                hotel.status_source = source
                hotel.status_doc_name = snap.doc_name
                hotel.status_doc_id = snap.doc_id
                hotel.status_page = snap.page
                hotel.name_as_reported = name_as_reported or hotel.name_as_reported
            elif status == "active" and hotel.status == "active" and source is not None:
                hotel.status_source = hotel.status_source or source
            if keys and keys > 0 and hotel.keys is None:
                hotel.keys = keys
                hotel.keys_doc_name = snap.doc_name

    hotels = [h.freeze() for h in union]
    closed = [h for h in hotels if h.status == "closed"]
    active = [h for h in hotels if h.status == "active"]
    status_available = any(h.explicit_marker for h in union)
    # The first roster in report-period order is the derivation's source.
    newest = next((s for s in snapshots if s.rows), None) or (snapshots[0] if snapshots else None)
    source_doc_name = newest.doc_name if newest else None
    source_doc_id = newest.doc_id if newest else None
    source_page = newest.page if newest else None

    if not hotels:
        note = (
            "No per-hotel roster was extracted from the STR report, so the "
            "report's own comp-set rollup is shown; closed hotels cannot be "
            "identified from a rollup."
        )
        return CompSetDerivation(
            hotels=[],
            active_count=size_reported,
            active_keys=keys_reported,
            closed_count=0,
            closed_names=[],
            count_basis="reported_rollup" if size_reported is not None else "none",
            keys_basis="reported_rollup" if keys_reported is not None else "none",
            status_available=False,
            reported_comp_set_size=size_reported,
            reported_total_keys=keys_reported,
            source_doc_name=source_doc_name,
            source_doc_id=source_doc_id,
            source_page=source_page,
            note=note,
            documents=documents,
        )

    active_keys_sum = sum(h.keys for h in active if h.keys and h.keys > 0)
    keys_basis: Basis
    if active_keys_sum > 0:
        active_keys: int | None = active_keys_sum
        keys_basis = "active_roster"
    elif keys_reported is not None and not closed:
        # Roster names came through without room counts; the report's own
        # total is the only key figure available and no hotel is closed.
        active_keys = keys_reported
        keys_basis = "reported_rollup"
    else:
        active_keys = None
        keys_basis = "none"

    if closed:
        def _marker(h: CompSetHotel) -> str:
            kind = (
                "an explicit status field"
                if h.status_source == "extracted_status_field"
                else "STR's \"Closed - \" roster label"
            )
            return f"{h.name}: {kind}{f' in {h.status_doc_name}' if h.status_doc_name else ''}"

        note = (
            f"{len(closed)} closed hotel{'s' if len(closed) != 1 else ''} "
            f"({', '.join(h.name for h in closed)}) excluded from the count and "
            f"the keys — marked closed by {'; '.join(_marker(h) for h in closed)}."
        )
    elif status_available:
        note = "Every hotel in the roster is marked open; all are counted."
    else:
        note = (
            "No hotel in the roster carries a closed marker (status field or "
            "STR \"Closed - \" label), so every listed hotel is counted as active."
        )
    if len(documents) > 1:
        note += f" Roster unioned across {len(documents)} STR reports ({', '.join(documents)})."
    aliased = [h for h in hotels if h.merged_names]
    if aliased:
        note += " Same hotel under different roster names: " + "; ".join(
            f"{h.name} (also listed as {', '.join(h.merged_names)})" for h in aliased
        ) + "."
    if keys_basis == "reported_rollup":
        note += " Keys are the report's rollup (the roster carried no room counts)."

    return CompSetDerivation(
        hotels=hotels,
        active_count=len(active),
        active_keys=active_keys,
        closed_count=len(closed),
        closed_names=[h.name for h in closed],
        count_basis="active_roster",
        keys_basis=keys_basis,
        status_available=status_available,
        reported_comp_set_size=size_reported,
        reported_total_keys=keys_reported,
        source_doc_name=source_doc_name,
        source_doc_id=source_doc_id,
        source_page=source_page,
        note=note,
        documents=documents,
    )


# ───────────────────────── report period (ordering) ─────────────────────────

PeriodEndBasis = Literal[
    "extracted_period_end",
    "subject_monthly_series",
    "period_start_plus_months",
    "document_report_as_of",
    "filename_token",
    "report_year",
    "created_at",
]

#: Leaf names of an extracted period-end field, matched on the last path
#: segment of any STR namespace outside the roster and the monthly series
#: (``ttm_performance.period_end``, ``ttm_performance.subject.period_ending``,
#: ``str_trend.report_date`` …).
PERIOD_END_LEAVES = frozenset(
    {
        "period_end", "period_ending", "period_end_date", "period_to",
        "report_date", "report_period_end", "as_of_date", "as_of",
    }
)
PERIOD_START_LEAVES = frozenset({"period_start", "period_beginning", "period_start_date", "period_from"})
PERIOD_MONTHS_LEAVES = frozenset({"months", "period_months", "months_covered"})

#: The six rows the blend reads — the report that supplies them is the
#: first in report-period order that carries at least one.
BLEND_FIELDS = frozenset({SUBJECT_OCC, SUBJECT_ADR, SUBJECT_REVPAR, INDEX_MPI, INDEX_ARI, INDEX_RGI})

_MONTHS: dict[str, int] = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
_MONTHS["sept"] = 9

_YEAR = r"((?:19|20)\d{2})"
_MON = r"(0[1-9]|1[0-2])"
_DAY = r"(0[1-9]|[12]\d|3[01])"
# Stated values: ISO / compact dates, ISO months, US dates, month names.
_VALUE_ISO_DATE_RE = re.compile(rf"^\s*{_YEAR}[-/._]?{_MON}[-/._]?{_DAY}(?:[T\s].*)?$")
_VALUE_ISO_MONTH_RE = re.compile(rf"^\s*{_YEAR}[-/._]?{_MON}(?:[-/._]?00)?\s*$")
_VALUE_US_DATE_RE = re.compile(rf"^\s*(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/{_YEAR}\s*$")
_VALUE_US_MONTH_RE = re.compile(rf"^\s*(0?[1-9]|1[0-2])/{_YEAR}\s*$")
_VALUE_MONTH_DAY_YEAR_RE = re.compile(
    rf"^\s*([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+{_YEAR}\s*$"
)
_VALUE_DAY_MONTH_YEAR_RE = re.compile(rf"^\s*(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?,?\s+{_YEAR}\s*$")
_VALUE_MONTH_YEAR_RE = re.compile(rf"^\s*([A-Za-z]{{3,9}})\.?,?[\s\-_/]+{_YEAR}\s*$")
_VALUE_YEAR_RE = re.compile(rf"^\s*{_YEAR}\s*$")
# Filename tokens: ``20250713`` / ``20250500`` (STR's month-only day 00) /
# ``202505`` / ``2025-05`` / ``Jun-2026`` / a bare year.
_FILE_YMD_RE = re.compile(rf"(?<!\d){_YEAR}{_MON}(\d{{2}})(?!\d)")
_FILE_YM_RE = re.compile(rf"(?<!\d){_YEAR}[-_.]?{_MON}(?!\d)")
_FILE_MONTH_NAME_RE = re.compile(rf"(?<![A-Za-z])([A-Za-z]{{3,9}})\.?[\s\-_/]*{_YEAR}(?!\d)")
_FILE_YEAR_RE = re.compile(rf"(?<!\d){_YEAR}(?!\d)")


@dataclass(frozen=True)
class StrReportOrder:
    """One STR extraction's place in the report-period ordering."""

    extraction_id: str | None
    doc_name: str | None
    doc_id: str | None
    #: ISO period end — ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY`` by how much
    #: the source stated; None when nothing dated the report.
    period_end: str | None
    period_end_basis: PeriodEndBasis
    #: The field path / document attribute / filename the period end was read from.
    period_end_source: str | None = None
    #: The extraction's ``created_at`` (the final tiebreak), when known.
    created_at: str | None = None


def _fmt_period(year: int, month: int | None = None, day: int | None = None) -> str | None:
    if not 1900 <= year <= 2100:
        return None
    if month is None:
        return f"{year:04d}"
    if day is None:
        return f"{year:04d}-{month:02d}"
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _month_number(name: str) -> int | None:
    return _MONTHS.get(name.strip().lower())


def parse_period_end(value: Any) -> str | None:
    """A stated period end → ISO ``YYYY-MM-DD`` / ``YYYY-MM`` / ``YYYY``.

    Accepts dates (``2025-05-31``, ``20250531``, ``5/31/2025``, ``May 31,
    2025``, ``31 May 2025``), months (``2025-05``, ``2025_05``, ``202505``,
    ``05/2025``, ``May 2025``) and bare years; None otherwise. The result
    keeps the precision the source stated — a year-only answer is kept
    behind every month-precise one by :func:`resolve_report_period_end`.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        year = int(value)
        return _fmt_period(year) if float(value) == year else None
    text = str(value).strip()
    if not text:
        return None
    if m := _VALUE_ISO_DATE_RE.match(text):
        return _fmt_period(int(m[1]), int(m[2]), int(m[3]))
    if m := _VALUE_ISO_MONTH_RE.match(text):
        return _fmt_period(int(m[1]), int(m[2]))
    if m := _VALUE_US_DATE_RE.match(text):
        return _fmt_period(int(m[3]), int(m[1]), int(m[2]))
    if m := _VALUE_US_MONTH_RE.match(text):
        return _fmt_period(int(m[2]), int(m[1]))
    if m := _VALUE_MONTH_DAY_YEAR_RE.match(text):
        month = _month_number(m[1])
        return _fmt_period(int(m[3]), month, int(m[2])) if month else None
    if m := _VALUE_DAY_MONTH_YEAR_RE.match(text):
        month = _month_number(m[2])
        return _fmt_period(int(m[3]), month, int(m[1])) if month else None
    if m := _VALUE_MONTH_YEAR_RE.match(text):
        month = _month_number(m[1])
        return _fmt_period(int(m[2]), month) if month else None
    if m := _VALUE_YEAR_RE.match(text):
        return _fmt_period(int(m[1]))
    return None


def period_end_from_filename(filename: str | None) -> str | None:
    """The report period a filename states: ``ANG-20250500-USD-E.xlsx`` →
    ``2025-05`` (STR's month-only ``00`` day), ``56387-20250713-USD-E.xlsx``
    → ``2025-07-13``, ``STR Trend Jun-2026.xlsx`` → ``2026-06``, ``… 2025
    …`` → ``2025``; None when the name carries no date token."""
    if not filename:
        return None
    name = filename.rsplit("/", 1)[-1]
    for m in _FILE_YMD_RE.finditer(name):
        year, month, day = int(m[1]), int(m[2]), int(m[3])
        if day == 0:
            return _fmt_period(year, month)
        parsed = _fmt_period(year, month, day)
        if parsed:
            return parsed
    if m := _FILE_YM_RE.search(name):
        return _fmt_period(int(m[1]), int(m[2]))
    for m in _FILE_MONTH_NAME_RE.finditer(name):
        month = _month_number(m[1])
        if month:
            return _fmt_period(int(m[2]), month)
    if m := _FILE_YEAR_RE.search(name):
        return _fmt_period(int(m[1]))
    return None


def _period_sort_key(period_end: str | None) -> tuple[int, int, int, int]:
    """Most recent first; undated last; a known month beats an unknown one."""
    if not period_end:
        return (1, 0, 0, 0)
    parts = period_end.split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 0
    day = int(parts[2]) if len(parts) > 2 else 0
    return (0, -year, -month, -day)


def _add_months(period: str, months: int) -> str | None:
    """``YYYY-MM`` plus ``months - 1`` → the last month covered."""
    year, month = int(period[:4]), int(period[5:7])
    idx = year * 12 + (month - 1) + (months - 1)
    return _fmt_period(idx // 12, idx % 12 + 1)


def _outside_roster_and_series(row: FieldRow) -> bool:
    return not row.lname.startswith((COMPSET_PREFIX, SUBJECT_MONTHLY_PREFIX))


def _leaf(row: FieldRow) -> str:
    return row.lname.rsplit(".", 1)[-1]


def resolve_report_period_end(
    rows: Sequence[FieldRow],
) -> tuple[str | None, PeriodEndBasis, str | None]:
    """``(period_end, basis, source)`` of ONE STR extraction's rows.

    The precedence is the module docstring's: an extracted period-end field,
    the last month of the subject monthly series, ``period_start`` +
    ``months``, the document's ``report_as_of``, a filename token — the
    first MONTH-precise answer wins; a year-only answer from any of them
    (incl. ``str_trend.report_year``) is kept only when nothing finer
    exists; ``(None, "created_at", None)`` when nothing dated the report.
    """
    year_only: list[tuple[str, PeriodEndBasis, str]] = []

    def month_or_finer(parsed: str | None, basis: PeriodEndBasis, source: str) -> str | None:
        if parsed is None:
            return None
        if len(parsed) == 4:
            year_only.append((parsed, basis, source))
            return None
        return parsed

    # 1. An extracted period-end field.
    for r in rows:
        if _outside_roster_and_series(r) and _leaf(r) in PERIOD_END_LEAVES:
            parsed = month_or_finer(parse_period_end(r.value), "extracted_period_end", r.field_name)
            if parsed:
                return parsed, "extracted_period_end", r.field_name
    # 2. The subject monthly series.
    periods: set[str] = set()
    for r in rows:
        if r.lname.startswith(SUBJECT_MONTHLY_PREFIX):
            rest = r.lname[len(SUBJECT_MONTHLY_PREFIX):]
            period = _normalize_period(rest.split(".", 1)[0]) if rest else None
            if period:
                periods.add(period)
    if periods:
        return max(periods), "subject_monthly_series", SUBJECT_MONTHLY_PREFIX + "<YYYY_MM>"
    # 3. period_start + months.
    start_row = next(
        (r for r in rows if _outside_roster_and_series(r) and _leaf(r) in PERIOD_START_LEAVES), None
    )
    months_row = next(
        (r for r in rows if _outside_roster_and_series(r) and _leaf(r) in PERIOD_MONTHS_LEAVES), None
    )
    if start_row is not None and months_row is not None:
        start = parse_period_end(start_row.value)
        months = coerce_int(months_row.value)
        if start and len(start) >= 7 and months and months > 0:
            end = _add_months(start[:7], months)
            if end:
                source = f"{start_row.field_name} + {months_row.field_name}"
                return end, "period_start_plus_months", source
    # 4. The document's report_as_of (a year-precision one only orders years).
    as_of_row = next((r for r in rows if r.report_as_of), None)
    if as_of_row is not None:
        parsed = parse_period_end(as_of_row.report_as_of)
        if parsed and (as_of_row.report_as_of_precision or "") == "year":
            parsed = parsed[:4]
        parsed = month_or_finer(parsed, "document_report_as_of", "documents.report_as_of")
        if parsed:
            return parsed, "document_report_as_of", "documents.report_as_of"
    # 5. A date token in the filename.
    doc_name = next((r.doc_name for r in rows if r.doc_name), None)
    if doc_name:
        parsed = month_or_finer(period_end_from_filename(doc_name), "filename_token", doc_name)
        if parsed:
            return parsed, "filename_token", doc_name
    # 6. Year-only answers, in the precedence order they were found.
    report_year_row = next((r for r in rows if r.lname == REPORT_YEAR), None)
    if report_year_row is not None:
        year = coerce_int(report_year_row.value)
        if year and (parsed := _fmt_period(year)):
            year_only.append((parsed, "report_year", report_year_row.field_name))
    if year_only:
        return year_only[0]
    return None, "created_at", None


def order_str_extractions(rows: Sequence[FieldRow]) -> list[StrReportOrder]:
    """Every extraction in ``rows`` ordered most-recent-report-period first.

    Ties (same period end, or no period end at all) keep the rows' arrival
    order — the DB read is ``created_at DESC``, so that is the newest
    extraction first: the only place upload time still decides.
    """
    by_ext: dict[str | None, list[FieldRow]] = {}
    for r in rows:
        by_ext.setdefault(r.extraction_id, []).append(r)
    orders: list[tuple[int, StrReportOrder]] = []
    for arrival, (ext, ext_rows) in enumerate(by_ext.items()):
        period_end, basis, source = resolve_report_period_end(ext_rows)
        first = ext_rows[0]
        orders.append(
            (
                arrival,
                StrReportOrder(
                    extraction_id=ext,
                    doc_name=first.doc_name,
                    doc_id=first.doc_id,
                    period_end=period_end,
                    period_end_basis=basis,
                    period_end_source=source,
                    created_at=first.created_at,
                ),
            )
        )
    orders.sort(key=lambda t: (_period_sort_key(t[1].period_end), t[0]))
    return [o for _, o in orders]


# ───────────────────────────── STR inputs ─────────────────────────────


@dataclass
class StrMarketInputs:
    """What the Market overview reads off the deal's STR extractions.

    ``extractions`` is every STR extraction on the deal ordered by REPORT
    PERIOD (most recent first; see the module docstring), each with the
    period end it resolved to. ``flat`` is first-hit-wins over the rows in
    that order (rollups read from it); ``fields_by_extraction`` keeps each
    report's rows apart so the blend reads ONE report. ``rosters`` holds
    EVERY extraction's ``ttm_performance.compset.<n>.*`` rows in the same
    order — the comp set is their union; ``roster`` (+ ``roster_doc_*``) is
    the first one. ``blend_extraction`` is the first report in the order
    carrying a blend row (subject TTM or an index); ``monthly_periods`` are
    the ``YYYY-MM`` periods of ITS subject monthly series.
    """

    flat: dict[str, FieldRef] = field(default_factory=dict)
    roster: dict[int, dict[str, Any]] = field(default_factory=dict)
    roster_doc_name: str | None = None
    roster_doc_id: str | None = None
    roster_page: int | None = None
    rosters: list[RosterSnapshot] = field(default_factory=list)
    monthly_periods: set[str] = field(default_factory=set)
    extractions: list[StrReportOrder] = field(default_factory=list)
    fields_by_extraction: dict[str | None, dict[str, FieldRef]] = field(default_factory=dict)
    blend_extraction: StrReportOrder | None = None


def _normalize_period(raw: str) -> str | None:
    """``YYYY_MM`` / ``YYYY-MM`` → ``YYYY-MM``; None when it doesn't parse."""
    norm = (raw or "").strip().replace("_", "-")
    parts = norm.split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12) or not (1900 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}"


def build_str_inputs(rows: list[FieldRow]) -> StrMarketInputs:
    out = StrMarketInputs()
    out.extractions = order_str_extractions(rows)
    by_ext: dict[str | None, list[FieldRow]] = {}
    for r in rows:
        by_ext.setdefault(r.extraction_id, []).append(r)
    ordered_rows = [r for o in out.extractions for r in by_ext.get(o.extraction_id, [])]

    roster_rows: dict[str | None, dict[int, dict[str, Any]]] = {}
    roster_meta: dict[str | None, tuple[str | None, str | None, int | None]] = {}
    roster_order: list[str | None] = []
    monthly_by_ext: dict[str | None, set[str]] = {}
    for r in ordered_rows:
        lname = r.lname
        if lname.startswith(COMPSET_PREFIX):
            rest = lname[len(COMPSET_PREFIX):]
            try:
                idx_str, attr = rest.split(".", 1)
                idx = int(idx_str)
            except (ValueError, IndexError):
                continue
            if r.extraction_id not in roster_rows:
                roster_rows[r.extraction_id] = {}
                roster_meta[r.extraction_id] = (r.doc_name, r.doc_id, r.page)
                roster_order.append(r.extraction_id)
            roster_rows[r.extraction_id].setdefault(idx, {}).setdefault(attr, r.value)
            continue
        if lname.startswith(SUBJECT_MONTHLY_PREFIX):
            rest = lname[len(SUBJECT_MONTHLY_PREFIX):]
            period = _normalize_period(rest.split(".", 1)[0]) if rest else None
            if period:
                monthly_by_ext.setdefault(r.extraction_id, set()).add(period)
            continue
        if lname not in out.flat:
            out.flat[lname] = FieldRef.of(r)
        per_ext = out.fields_by_extraction.setdefault(r.extraction_id, {})
        if lname not in per_ext:
            per_ext[lname] = FieldRef.of(r)
    # The blend reads ONE report: the first in report-period order carrying
    # a subject TTM figure or a penetration index.
    out.blend_extraction = next(
        (
            o for o in out.extractions
            if any(name in out.fields_by_extraction.get(o.extraction_id, {}) for name in BLEND_FIELDS)
        ),
        None,
    )
    if out.blend_extraction is not None:
        out.monthly_periods = set(monthly_by_ext.get(out.blend_extraction.extraction_id, set()))
    else:
        # No blend row anywhere: keep the first monthly series in report-period
        # order for callers that only want the period.
        for o in out.extractions:
            if o.extraction_id in monthly_by_ext:
                out.monthly_periods = set(monthly_by_ext[o.extraction_id])
                break
    # Rosters, most recent report period first.
    for ext in roster_order:
        doc_name, doc_id, page = roster_meta[ext]
        out.rosters.append(
            RosterSnapshot(rows=roster_rows[ext], doc_name=doc_name, doc_id=doc_id, page=page, extraction_id=ext)
        )
    if out.rosters:
        first = out.rosters[0]
        out.roster = dict(first.rows)
        out.roster_doc_name, out.roster_doc_id, out.roster_page = first.doc_name, first.doc_id, first.page
    return out


def derive_comp_set_from_inputs(inputs: StrMarketInputs) -> CompSetDerivation:
    size = inputs.flat.get(ROLLUP_SIZE)
    keys = inputs.flat.get(ROLLUP_KEYS)
    rollup_ref = size or keys
    snapshots = list(inputs.rosters) or [
        RosterSnapshot(
            rows={},
            doc_name=rollup_ref.doc_name if rollup_ref else None,
            doc_id=rollup_ref.doc_id if rollup_ref else None,
            page=rollup_ref.page if rollup_ref else None,
        )
    ]
    derived = derive_comp_set_union(
        snapshots,
        reported_comp_set_size=size.value if size else None,
        reported_total_keys=keys.value if keys else None,
    )
    return replace(derived, ordering=list(inputs.extractions))


# ───────────────────────────── TTM blend (E-007) ─────────────────────────────


@dataclass(frozen=True)
class TtmBlend:
    #: Comp-set blend — occupancy in whole percent, ADR / RevPAR in USD.
    occupancy_pct: float | None
    adr_usd: float | None
    revpar_usd: float | None
    subject_occupancy_pct: float | None
    subject_adr_usd: float | None
    subject_revpar_usd: float | None
    #: Penetration indices as ratios (1.00 = parity).
    mpi: float | None
    ari: float | None
    rgi: float | None
    period_start: str | None
    period_end: str | None
    months: int | None
    period_basis: Literal["subject_monthly_series", "report_year", "none"]
    #: ``str_trend.report_year`` of the report that supplied the inputs.
    report_year: int | None
    #: Every extraction row the blend read, in the order it was used.
    inputs: list[FieldRef]
    #: The document that supplied the inputs (one report; see module docstring).
    documents: list[str]
    method: str
    #: Which STR report supplied the subject TTM and the indices …
    source_doc_name: str | None = None
    source_doc_id: str | None = None
    source_extraction_id: str | None = None
    #: … and why it ranked first: the period end it resolved to and how.
    period_end_used: str | None = None
    ordering_basis: PeriodEndBasis | None = None
    #: Every STR extraction on the deal in report-period order.
    ordering: list[StrReportOrder] = field(default_factory=list)


def _ratio(idx: float | None) -> float | None:
    """STR indices arrive as a ratio (1.098) or index points (109.8)."""
    if idx is None or idx <= 0:
        return None
    return idx / 100.0 if idx > 3 else idx


def _occ_points(occ: float | None) -> float | None:
    """Occupancy arrives as a 0..1 fraction or a whole percent."""
    if occ is None or occ <= 0:
        return None
    return occ * 100.0 if occ <= 1.5 else occ


def derive_ttm_blend(inputs: StrMarketInputs) -> TtmBlend | None:
    """The blend the Market tiles show, with the rows and period behind it.

    Every input comes from ONE STR report — ``inputs.blend_extraction``, the
    first in report-period order carrying a subject TTM figure or an index —
    so a subject figure is never divided by another report's index, and
    ``report_year`` / ``documents`` describe that report. Returns None when
    no STR extraction carries a subject TTM or an index (nothing to
    define). A blend metric is None when its subject figure or its index is
    missing from that report — never substituted from an older one.
    """
    refs: list[FieldRef] = []
    source = inputs.blend_extraction
    fields = (
        inputs.flat if source is None
        else inputs.fields_by_extraction.get(source.extraction_id, inputs.flat)
    )

    def take(name: str) -> float | None:
        ref = fields.get(name)
        if ref is None:
            return None
        v = coerce_float(ref.value)
        if v is None:
            return None
        refs.append(ref)
        return v

    subj_occ = _occ_points(take(SUBJECT_OCC))
    subj_adr = take(SUBJECT_ADR)
    subj_revpar = take(SUBJECT_REVPAR)
    mpi = _ratio(take(INDEX_MPI))
    ari = _ratio(take(INDEX_ARI))
    rgi = _ratio(take(INDEX_RGI))
    if not refs:
        return None

    def div(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None or b <= 0 else a / b

    occ = div(subj_occ, mpi)
    adr = div(subj_adr, ari)
    revpar = div(subj_revpar, rgi)
    if revpar is None and occ is not None and adr is not None:
        revpar = occ / 100.0 * adr

    periods = sorted(inputs.monthly_periods)
    report_year_ref = fields.get(REPORT_YEAR)
    report_year = coerce_int(report_year_ref.value) if report_year_ref else None
    if periods:
        period_basis: Literal["subject_monthly_series", "report_year", "none"] = "subject_monthly_series"
        period_start, period_end, months = periods[0], periods[-1], len(periods)
    elif report_year:
        period_basis = "report_year"
        period_start = period_end = None
        months = None
        refs.append(report_year_ref)  # type: ignore[arg-type]
    else:
        period_basis = "none"
        period_start = period_end = None
        months = None

    documents: list[str] = [source.doc_name] if source is not None and source.doc_name else []
    for ref in refs:
        if ref.doc_name and ref.doc_name not in documents:
            documents.append(ref.doc_name)

    return TtmBlend(
        occupancy_pct=round(occ, 4) if occ is not None else None,
        adr_usd=round(adr, 4) if adr is not None else None,
        revpar_usd=round(revpar, 4) if revpar is not None else None,
        subject_occupancy_pct=round(subj_occ, 4) if subj_occ is not None else None,
        subject_adr_usd=subj_adr,
        subject_revpar_usd=subj_revpar,
        mpi=mpi,
        ari=ari,
        rgi=rgi,
        period_start=period_start,
        period_end=period_end,
        months=months,
        period_basis=period_basis,
        report_year=report_year,
        inputs=refs,
        documents=documents,
        method=TTM_BLEND_METHOD,
        source_doc_name=source.doc_name if source else refs[0].doc_name,
        source_doc_id=source.doc_id if source else refs[0].doc_id,
        source_extraction_id=source.extraction_id if source else None,
        period_end_used=source.period_end if source else None,
        ordering_basis=source.period_end_basis if source else None,
        ordering=list(inputs.extractions),
    )


__all__ = [
    "BLEND_FIELDS",
    "COMPSET_PREFIX",
    "PERIOD_END_LEAVES",
    "STR_CLOSED_LABEL_RE",
    "TTM_BLEND_METHOD",
    "CompSetDerivation",
    "CompSetHotel",
    "PeriodEndBasis",
    "RosterSnapshot",
    "StrMarketInputs",
    "StrReportOrder",
    "TtmBlend",
    "build_str_inputs",
    "classify_hotel_status",
    "derive_comp_set",
    "derive_comp_set_from_inputs",
    "derive_comp_set_union",
    "derive_ttm_blend",
    "display_name",
    "names_are_aliases",
    "normalized_hotel_name",
    "order_str_extractions",
    "parse_period_end",
    "period_end_from_filename",
    "resolve_report_period_end",
]
