"""Document as-of date — "what date is this document current as of?".

Phase 2.2. Every uploaded document gets a ``report_as_of`` date plus a
``report_as_of_precision`` saying how much of that date the document
actually STATED (``day`` | ``month`` | ``quarter`` | ``year``). A P&L
that says "for the period ending May 31, 2025" is dated to the day; a
CBRE Horizons report headed "Q3 2024" is dated to the quarter; an STR
Trend report that only carries ``report_year: 2024`` is dated to the
year (31 December).

The one rule: **never guess a date.** If the document states nothing we
can read, ``derive_report_as_of`` returns ``(None, None)`` and the
document stays undated — the registry already carries an
``as_of_unknown`` refusal reason for exactly that state. An invented
as-of date would silently mis-order the deal's evidence, which is worse
than an honest gap.

Where the dates come from
-------------------------
The registry (``app/ontology/concepts.yaml``) already says which
concept dates a value: every concept carries an ``as_of`` pointer to
another concept id (``period_ending`` for the P&L family,
``str_report_year`` for the STR comp-set block). This module reads
those pointers and resolves the pointed-at concept's own alias paths on
the extraction payload, so a new alias added to the registry is picked
up here for free.

Two real shapes on this corpus have no registry concept yet, so they
are handled explicitly and documented here:

* ``cbre_horizons.publication_date`` — the CBRE report header
  (``"Q3 2024"`` or an ISO date), per
  ``agents/extraction_schemas/cbre_horizons.md``.
* ``transaction_comps.<n>.sale_date`` — the OM's Comparable Sales
  table, per ``agents/extraction_schemas/om.md``. The NEWEST comp sale
  is the OM's freshest stated fact, so it dates the OM; when the OM
  states no comp date we fall back to the period end of its own
  historical / TTM operating statement, and below that to the newest
  year in its ``om_history.<year>.*`` block — on the live Angler's OM
  that year-tagged namespace is the ONLY place the document's vintage
  survives extraction (page 40's "Year Ended December 31, 2024" column
  arrives as ``om_history.2024.*`` with no period field beside it).

Per document type
-----------------
==================== ===================================================
Doc type             As-of source (first hit wins)
==================== ===================================================
T12 / PNL /          ``p_and_l_usali.period_ending`` → the registry's
PNL_MONTHLY /        other ``period_ending`` aliases
PNL_YTD              (``property_overview.statement_period[_end]``) →
                     the prod variants below. Precision ``day`` (or
                     ``month`` when only a month is stated).
STR / STR_TREND /    ``str_trend.report_year`` /
STR_SEGMENTATION     ``str_segmentation.report_year`` → 31 December of
                     that year, precision ``year``.
CBRE_HORIZONS        ``cbre_horizons.publication_date`` → quarter end,
                     precision ``quarter`` (``day`` for an ISO date).
OM                   newest ``transaction_comps.<n>.sale_date`` →
                     else the OM's own stated period end → else the
                     newest year in its ``om_history.<year>.*`` block
                     (precision ``year``; never the broker pro forma).
everything else      any ``period_ending`` alias the document happens
                     to state; otherwise ``(None, None)``.
==================== ===================================================

This module is read-only over the registry and pure over its inputs —
no DB, no network, no LLM. The caller
(``api/documents.py::_persist_report_as_of``) persists the result.
"""

from __future__ import annotations

import calendar
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from ..ontology.registry import get_registry
from .coverage_audit import _coerce_year, _parse_iso_date

logger = logging.getLogger(__name__)

__all__ = ["derive_report_as_of"]


#: The four precisions the ``documents.report_as_of_precision`` column
#: accepts. Ordered most → least precise.
PRECISIONS: tuple[str, ...] = ("day", "month", "quarter", "year")

_PNL_FAMILY: frozenset[str] = frozenset({"T12", "PNL", "PNL_MONTHLY", "PNL_YTD"})
_STR_FAMILY: frozenset[str] = frozenset({"STR", "STR_TREND", "STR_SEGMENTATION"})

#: Concepts whose value is a bare YEAR, not a date. These date to
#: 31 December with precision ``year`` even when the extractor happens
#: to surface a full date — the concept only claims a year.
_YEAR_CONCEPTS: frozenset[str] = frozenset({"str_report_year"})

#: Paths the prod extractor really emits that the registry's alias list
#: does not (yet) carry. Verified against the stored payloads at
#: ``tests/fixtures/real_payloads/`` — the annual P&L emits
#: ``p_and_l_usali.period.end_date`` where the T-12 emits
#: ``p_and_l_usali.period_ending`` — and against
#: ``agents/extraction_schemas/om.md`` for the OM's own TTM block.
#: Appended AFTER the registry aliases so the registry always wins.
_EXTRA_PATHS: dict[str, tuple[str, ...]] = {
    "period_ending": (
        "p_and_l_usali.period.end_date",
        "p_and_l_usali.period_end",
        "p_and_l_usali.period_end_date",
        "ttm_summary_per_om.period_ending",
        "ttm_summary_per_om.period_end",
        "statement_period_end",
        "statement_period",
    ),
}

#: The OM's Comparable Sales table. ``om.md`` numbers the rows
#: ``transaction_comps.1..N``; ``comparable_sales.md`` uses the second
#: namespace for the same table when the comps arrive as their own
#: document.
_COMP_SALE_DATE_RE = re.compile(
    r"^(?:transaction_comps|comparable_sales)\.[^.]+\.sale_date$"
)

#: The OM's OWN historical operating statement, year-tagged in the path
#: (``om_history.2024.noi_usd``). On the live Angler's OM this is the
#: only place the document's vintage survives extraction — page 40's
#: "Year Ended December 31, 2024" column becomes an ``om_history.2024.*``
#: namespace and no ``period_ending`` field is emitted at all.
#:
#: Deliberately anchored to the HISTORY namespaces only. ``broker_proforma.*``
#: is year-tagged the same way (the Angler's OM pro forma runs 2025-2029)
#: and is a PROJECTION — dating an OM by it would push the as-of into
#: the future, which is the exact failure this module exists to prevent.
_OM_HISTORY_YEAR_RE = re.compile(
    r"^(?:om_history|historical_performance|historical)\.((?:19|20)\d{2})\."
)

_CBRE_PUBLICATION_PATHS: tuple[str, ...] = (
    "cbre_horizons.publication_date",
    "cbre_horizons.report_date",
    "publication_date",
)

# ── stated-date shapes ────────────────────────────────────────────────
# Each is anchored, so a full ISO date can never be mistaken for a
# reduced-precision one (and vice versa).
_QUARTER_RE = re.compile(
    r"^\s*(?:q\s*([1-4])\s*[-/,]?\s*((?:19|20)\d{2})"
    r"|((?:19|20)\d{2})\s*[-/,]?\s*q\s*([1-4]))\s*$",
    re.IGNORECASE,
)
_YEAR_MONTH_RE = re.compile(r"^\s*((?:19|20)\d{2})[-/](0?[1-9]|1[0-2])\s*$")
_MONTH_NAME_RE = re.compile(
    r"^\s*([A-Za-z]{3,9})\.?,?\s+((?:19|20)\d{2})\s*$"
)
_BARE_YEAR_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*$")

_MONTH_NAMES: dict[str, int] = {
    name.lower(): i
    for i, name in enumerate(calendar.month_name)
    if name
}
_MONTH_NAMES.update(
    {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
)
_MONTH_NAMES["sept"] = 9

#: Sanity bounds on a derived as-of date, matching the ``[1900, 2100]``
#: range ``coverage_audit._coerce_year`` already enforces on bare years.
#: An OCR mis-read ("0202-12-31") or a truncated cell that happens to
#: parse is not a stated date — an out-of-range result is dropped rather
#: than persisted as an obviously-wrong document age.
_MIN_YEAR = 1900
_MAX_YEAR = 2100


# ─────────────────────────── field normalisation ──────────────────────


def _iter_fields(fields: Any) -> list[tuple[str, Any]]:
    """Normalise any extraction payload shape to ``[(lowercase path, value)]``.

    Accepts the three shapes that reach us: the extractor's list of
    ``{"field_name": ..., "value": ...}`` dicts, a list of objects
    carrying those attributes, or a plain ``{path: value}`` mapping.
    Order is preserved — first occurrence of a path wins.
    """
    out: list[tuple[str, Any]] = []
    if fields is None:
        return out
    if isinstance(fields, Mapping):
        for k, v in fields.items():
            name = str(k).strip().lower()
            if name:
                out.append((name, v))
        return out
    if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
        return out
    for f in fields:
        if isinstance(f, Mapping):
            raw_name = f.get("field_name")
            value = f.get("value")
        else:
            raw_name = getattr(f, "field_name", None)
            value = getattr(f, "value", None)
        name = str(raw_name or "").strip().lower()
        if name:
            out.append((name, value))
    return out


def _lookup(flds: list[tuple[str, Any]], path: str) -> Any:
    """First value at an exact (lowercased) field path, or ``None``."""
    for name, value in flds:
        if name == path and value is not None:
            return value
    return None


# ─────────────────────────── value → (date, precision) ────────────────


def _bounded(
    result: tuple[date | None, str | None],
) -> tuple[date | None, str | None]:
    """Drop a parsed date outside ``[_MIN_YEAR, _MAX_YEAR]``."""
    parsed = result[0]
    if parsed is None or not (_MIN_YEAR <= parsed.year <= _MAX_YEAR):
        return (None, None)
    return result


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _quarter_end(year: int, quarter: int) -> date:
    return _month_end(year, quarter * 3)


def _parse_year_value(value: Any) -> tuple[date | None, str | None]:
    """A bare year (``2024`` / ``"2024"``) → 31 December, precision ``year``.

    Falls back to the year of a full date when the extractor put one in
    a year-shaped field — the concept still only claims a year, so the
    precision stays ``year``.
    """
    return _bounded(_parse_year_value_inner(value))


def _parse_year_value_inner(value: Any) -> tuple[date | None, str | None]:
    year = _coerce_year(value)
    if year is None:
        parsed = _parse_iso_date(value)
        if parsed is None:
            return (None, None)
        year = parsed.year
    return (date(year, 12, 31), "year")


def _parse_as_of(value: Any) -> tuple[date | None, str | None]:
    """Parse any stated as-of value into ``(date, precision)``.

    Returns ``(None, None)`` — never raises — for anything unreadable.
    Reduced-precision values resolve to the END of the period they name
    (a quarter's last day, a month's last day, 31 December for a year)
    because an as-of date is a closing date: the document is current as
    of the end of the period it reports.
    """
    return _bounded(_parse_as_of_inner(value))


def _parse_as_of_inner(value: Any) -> tuple[date | None, str | None]:
    if value is None or isinstance(value, bool):
        return (None, None)
    if isinstance(value, datetime):
        return (value.date(), "day")
    if isinstance(value, date):
        return (value, "day")
    if isinstance(value, (int, float)):
        return _parse_year_value(value)
    if not isinstance(value, str):
        return (None, None)

    s = value.strip()
    if not s:
        return (None, None)

    m = _QUARTER_RE.match(s)
    if m:
        quarter = int(m.group(1) or m.group(4))
        year = int(m.group(2) or m.group(3))
        return (_quarter_end(year, quarter), "quarter")

    m = _YEAR_MONTH_RE.match(s)
    if m:
        return (_month_end(int(m.group(1)), int(m.group(2))), "month")

    m = _MONTH_NAME_RE.match(s)
    if m:
        month = _MONTH_NAMES.get(m.group(1).lower())
        if month is not None:
            return (_month_end(int(m.group(2)), month), "month")

    m = _BARE_YEAR_RE.match(s)
    if m:
        return (date(int(m.group(1)), 12, 31), "year")

    parsed = _parse_iso_date(s)
    if parsed is not None:
        return (parsed, "day")
    return (None, None)


# ─────────────────────────── registry-driven resolution ───────────────


def _alias_paths(concept_id: str, doc_type: str | None) -> list[str]:
    """Ordered alias paths the registry lists for ``concept_id``.

    The document type's own key (and its families) come first, then the
    ``"*"`` any-document aliases — the same precedence
    ``registry.resolve`` uses, so the T-12's schema-canonical
    ``p_and_l_usali.period_ending`` outranks the generic
    ``property_overview.statement_period`` fallback that can sit beside
    it in the same payload.
    """
    reg = get_registry()
    concept = reg.concepts.get(concept_id)
    if concept is None:
        return []
    keys = [*reg.alias_keys_for(doc_type), "*"]
    paths: list[str] = []
    for key in keys:
        for alias in concept.aliases.get(key, []):
            path = alias.path.strip().lower()
            # Wildcard aliases ({n} / {year} segments) are matched by
            # the dedicated comp-sale scan, not by exact lookup.
            if path and "{" not in path and path not in paths:
                paths.append(path)
    for path in _EXTRA_PATHS.get(concept_id, ()):
        if path not in paths:
            paths.append(path)
    return paths


def _as_of_concept_ids(doc_type: str | None) -> list[str]:
    """Every concept the registry points ``as_of`` at for this doc type.

    Registry order is preserved, so ``period_ending`` (the P&L family's
    dater) comes before ``str_report_year`` (the market block's). The
    doc-type dispatch in :func:`derive_report_as_of` puts the right one
    first for STR / CBRE / OM.
    """
    reg = get_registry()
    keys = set(reg.alias_keys_for(doc_type)) | {"*"}
    out: list[str] = []
    for concept in reg.concepts.values():
        if not concept.as_of or concept.as_of in out:
            continue
        if not keys & set(concept.aliases):
            continue
        out.append(concept.as_of)
    return out


def _from_concept(
    concept_id: str, flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    """Resolve one as-of concept against the payload."""
    year_only = concept_id in _YEAR_CONCEPTS
    for path in _alias_paths(concept_id, doc_type):
        value = _lookup(flds, path)
        if value is None:
            continue
        got = _parse_year_value(value) if year_only else _parse_as_of(value)
        if got[0] is not None:
            return got
    return (None, None)


def _from_cbre_publication(
    flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    """``cbre_horizons.publication_date`` — ``"Q3 2024"`` or an ISO date."""
    for path in _CBRE_PUBLICATION_PATHS:
        got = _parse_as_of(_lookup(flds, path))
        if got[0] is not None:
            return got
    return (None, None)


def _from_transaction_comps(
    flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    """Newest ``transaction_comps.<n>.sale_date`` on the OM's comp table.

    The freshest transaction the broker chose to print is the newest
    fact the OM states, so it dates the OM. Ties on the date keep the
    most precise reading.
    """
    best: tuple[date, str] | None = None
    for name, value in flds:
        if not _COMP_SALE_DATE_RE.match(name):
            continue
        parsed, precision = _parse_as_of(value)
        if parsed is None or precision is None:
            continue
        if (
            best is None
            or parsed > best[0]
            # Same date twice: keep the more precise reading.
            or (
                parsed == best[0]
                and PRECISIONS.index(precision) < PRECISIONS.index(best[1])
            )
        ):
            best = (parsed, precision)
    return best if best is not None else (None, None)


def _from_om_history_year(
    flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    """Newest year in the OM's own historical operating statement.

    Last resort for an OM, below the comp table and below a stated
    period end. The year is genuinely stated — the broker printed a
    column for it — but only to the year, so the precision is ``year``.
    """
    newest: int | None = None
    for name, _value in flds:
        match = _OM_HISTORY_YEAR_RE.match(name)
        if match is None:
            continue
        year = int(match.group(1))
        if _MIN_YEAR <= year <= _MAX_YEAR and (newest is None or year > newest):
            newest = year
    if newest is None:
        return (None, None)
    return (date(newest, 12, 31), "year")


def _from_registry(
    flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    """Try every ``as_of`` concept the registry applies to this doc type."""
    for concept_id in _as_of_concept_ids(doc_type):
        got = _from_concept(concept_id, flds, doc_type)
        if got[0] is not None:
            return got
    return (None, None)


def _period_ending(
    flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    return _from_concept("period_ending", flds, doc_type)


def _str_report_year(
    flds: list[tuple[str, Any]], doc_type: str | None
) -> tuple[date | None, str | None]:
    return _from_concept("str_report_year", flds, doc_type)


#: Resolver chain per document type. First resolver to return a date
#: wins; ``_from_registry`` is the shared tail so a document that states
#: an as-of concept outside its own family still gets dated.
def _resolvers_for(doc_type: str) -> tuple[Any, ...]:
    if doc_type in _STR_FAMILY:
        return (_str_report_year, _from_registry)
    if doc_type == "CBRE_HORIZONS":
        return (_from_cbre_publication, _from_registry)
    if doc_type in _PNL_FAMILY:
        return (_period_ending, _from_registry)
    if doc_type == "OM":
        return (
            _from_transaction_comps,
            _period_ending,
            _from_registry,
            _from_om_history_year,
        )
    return (_from_registry,)


# ─────────────────────────────── public API ───────────────────────────


def derive_report_as_of(
    fields: Any, doc_type: str | None = None
) -> tuple[date | None, str | None]:
    """Date a document from what it states. ``(None, None)`` when it states nothing.

    Parameters
    ----------
    fields:
        The extraction payload — the extractor's list of
        ``{"field_name", "value"}`` dicts, objects with those
        attributes, or a ``{path: value}`` mapping.
    doc_type:
        The router's DocType (``"T12"``, ``"OM"``, ``"CBRE_HORIZONS"``,
        …). Case-insensitive; ``None`` falls through to the generic
        registry pass.

    Returns
    -------
    ``(as_of, precision)`` where ``precision`` is one of
    :data:`PRECISIONS` and both are ``None`` together. Never raises —
    a malformed date is an undated document, not an extraction failure.
    """
    try:
        flds = _iter_fields(fields)
        if not flds:
            return (None, None)
        dt = (doc_type or "").strip().upper()
        for resolver in _resolvers_for(dt):
            as_of, precision = resolver(flds, dt or None)
            if as_of is not None and precision in PRECISIONS:
                return (as_of, precision)
        return (None, None)
    except Exception:
        # Dating is additive intelligence on top of extraction, never a
        # gate on it: an unreadable payload is an undated document.
        logger.debug(
            "derive_report_as_of failed for doc_type=%s", doc_type, exc_info=True
        )
        return (None, None)
