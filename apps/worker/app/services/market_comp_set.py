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
Hotels are keyed by STR ID when extracted, else by the name with the
closed label stripped and case / whitespace normalised; a hotel is closed
if ANY report marks it; its keys come from the newest report that lists a
positive room count and are excluded from the active totals when closed.

Pure functions — no DB, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    #: The (newest) document the key count was read from.
    keys_doc_name: str | None = None
    #: Every roster document that lists this hotel, newest first.
    reports: tuple[str, ...] = ()


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
    #: Every STR roster document unioned, newest first.
    documents: list[str] = field(default_factory=list)


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

    def freeze(self) -> CompSetHotel:
        return CompSetHotel(
            index=self.index, name=self.name, name_as_reported=self.name_as_reported,
            keys=self.keys, status=self.status, status_source=self.status_source,
            str_id=self.str_id, status_doc_name=self.status_doc_name,
            status_doc_id=self.status_doc_id, status_page=self.status_page,
            keys_doc_name=self.keys_doc_name, reports=tuple(self.reports),
        )


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

    ``snapshots`` are newest first. Each carries ``{n: {"name", "keys",
    "status", "str_id"}}`` as bucketed from ``ttm_performance.compset.<n>.*``
    rows of ONE extraction. Hotels are keyed by ``str_id`` when extracted,
    else by the normalised name; a hotel is closed if ANY report marks it
    (status field or STR's "Closed - " label — a 0-room row alone never
    does); keys come from the newest report listing a positive count.
    Rows with neither a name nor a positive key count are ignored. Falls
    back to the report's rollups only when no roster was extracted at all,
    and says so.
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
            hotel = by_id.get(sid) if sid else None
            if hotel is None and norm:
                hotel = by_name.get(norm)
            if hotel is None:
                hotel = _UnionHotel(
                    index=len(union) + 1,
                    name=display_name(name_as_reported) if name_as_reported else f"Hotel {idx}",
                    name_as_reported=name_as_reported,
                    str_id=sid,
                )
                union.append(hotel)
            if sid and sid not in by_id:
                by_id[sid] = hotel
                hotel.str_id = hotel.str_id or sid
            if norm and norm not in by_name:
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


# ───────────────────────────── STR inputs ─────────────────────────────


@dataclass
class StrMarketInputs:
    """What the Market overview reads off the deal's STR extractions.

    ``flat`` is first-hit-wins over newest-first rows (the same precedence
    the ``/market-data`` block uses, so the blend here is the blend the
    tiles show). ``rosters`` holds EVERY extraction's
    ``ttm_performance.compset.<n>.*`` rows, newest first — the comp set is
    their union; ``roster`` (+ ``roster_doc_*``) is the newest one.
    """

    flat: dict[str, FieldRef] = field(default_factory=dict)
    roster: dict[int, dict[str, Any]] = field(default_factory=dict)
    roster_doc_name: str | None = None
    roster_doc_id: str | None = None
    roster_page: int | None = None
    rosters: list[RosterSnapshot] = field(default_factory=list)
    #: ``YYYY-MM`` periods of the subject monthly series (from the extraction
    #: that supplied the subject TTM, else the newest with a monthly series).
    monthly_periods: set[str] = field(default_factory=set)


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
    roster_rows: dict[str | None, dict[int, dict[str, Any]]] = {}
    roster_meta: dict[str | None, tuple[str | None, str | None, int | None]] = {}
    roster_order: list[str | None] = []
    monthly_by_ext: dict[str | None, set[str]] = {}
    subject_ext: str | None = None
    for r in rows:
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
            if lname == SUBJECT_OCC:
                subject_ext = r.extraction_id
    if subject_ext in monthly_by_ext:
        out.monthly_periods = monthly_by_ext[subject_ext]
    elif monthly_by_ext:
        # Newest extraction with a monthly series (rows are newest-first).
        for r in rows:
            if r.extraction_id in monthly_by_ext:
                out.monthly_periods = monthly_by_ext[r.extraction_id]
                break
    # Rosters, newest first (rows arrive newest-first, so first seen = newest).
    for ext in roster_order:
        doc_name, doc_id, page = roster_meta[ext]
        out.rosters.append(
            RosterSnapshot(rows=roster_rows[ext], doc_name=doc_name, doc_id=doc_id, page=page, extraction_id=ext)
        )
    if out.rosters:
        newest = out.rosters[0]
        out.roster = dict(newest.rows)
        out.roster_doc_name, out.roster_doc_id, out.roster_page = newest.doc_name, newest.doc_id, newest.page
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
    return derive_comp_set_union(
        snapshots,
        reported_comp_set_size=size.value if size else None,
        reported_total_keys=keys.value if keys else None,
    )


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
    report_year: int | None
    #: Every extraction row the blend read, in the order it was used.
    inputs: list[FieldRef]
    #: Distinct source documents, in input order.
    documents: list[str]
    method: str


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

    Returns None when the STR extraction carries neither a subject TTM nor
    an index (nothing to define). A blend metric is None when its subject
    figure or its index is missing — never substituted.
    """
    refs: list[FieldRef] = []

    def take(name: str) -> float | None:
        ref = inputs.flat.get(name)
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
    report_year_ref = inputs.flat.get(REPORT_YEAR)
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

    documents: list[str] = []
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
    )


__all__ = [
    "COMPSET_PREFIX",
    "STR_CLOSED_LABEL_RE",
    "TTM_BLEND_METHOD",
    "CompSetDerivation",
    "CompSetHotel",
    "RosterSnapshot",
    "StrMarketInputs",
    "TtmBlend",
    "build_str_inputs",
    "classify_hotel_status",
    "derive_comp_set",
    "derive_comp_set_from_inputs",
    "derive_comp_set_union",
    "derive_ttm_blend",
    "display_name",
    "normalized_hotel_name",
]
