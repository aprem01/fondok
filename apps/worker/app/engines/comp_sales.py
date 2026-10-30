"""Comparable Sales engine — derive an exit cap rate from OM comps.

Wave 3 W3.1. C12 (go-live audit, 2026-10-30): this module owns the ONE
derivation of an exit cap rate from comparable sales —
:func:`exit_cap_from_comps`. The engine loader
(``engine_runner._load_om_transaction_comps_cap_rate``, which sets the
model's ``exit_cap_rate`` and badges it :data:`EXIT_CAP_COMPS_SOURCE`) and
the Comparable Sales tab (:func:`build_comp_set`) both call it, so the
median the tab shows is computed by the same rules — plausibility band,
minimum comp count, median — as the figure the model runs on. Fewer than
:data:`EXIT_CAP_MIN_COMPS` plausible comps derive nothing (the model then
keeps its labelled ``seed`` default and says so).

This module is the upgrade Sam asked for at the 2026-06-25 call: a
*transparent* comp-set derivation showing every transaction the engine
considered, every comp it filtered out, every weight it applied, and
the two derived numbers (median + weighted) the analyst can pick
between. The output is a ``CompSalesSet`` Pydantic model — JSON-
serializable, asyncpg-safe, and read-only with respect to the engine
chain.

Derivation rules
----------------

1. **Lookback filter** — drop comps with a ``sale_date`` older than
   ``lookback_years`` (default 5). Comps with no ``sale_date`` are
   *kept* (we can't prove they're stale) but tagged in
   ``weighting_notes``.

2. **Cap-rate presence** — drop comps with no ``cap_rate_pct`` value;
   we cannot derive a cap rate from a row that doesn't have one.

3. **Analyst exclude list** — drop any row whose ``transaction_id``
   appears in ``exclude_transaction_ids``. The analyst made an
   explicit "this comp doesn't reflect the deal" call.

4. **Plausibility + median** — :func:`exit_cap_from_comps`: comps whose
   cap rate falls outside :data:`EXIT_CAP_PLAUSIBLE_RANGE` (4–11%) are
   dropped as broken data (an IRR or debt yield read as a cap rate); with
   at least :data:`EXIT_CAP_MIN_COMPS` plausible comps the median of their
   cap rates is the derived exit cap. The same function sets the model's
   ``exit_cap_rate`` in the engine loader.

5. **Weighted** — weight each surviving comp by
   ``0.7 * recency_score + 0.2 * market_match + 0.1 * chain_match``.
   Only emitted when ``subject_market`` or ``subject_chain_scale`` was
   provided; otherwise the weighting collapses to recency-only and we
   report ``median`` as the method (no information gain over the
   simple median).

   - ``recency_score``: 1.0 if ≤ 2 yrs, 0.7 if ≤ 4 yrs, 0.4 if ≤ 6 yrs,
     0.0 beyond. (Comps with no sale_date get 0.4 — middle bucket.)
   - ``market_match``: 1.0 if same MSA (city match), 0.5 if same state,
     0.0 otherwise.
   - ``chain_match``: 1.0 if same chain_scale, 0.5 if adjacent
     (upscale ↔ upper-upscale, midscale ↔ upper-midscale, etc.),
     0.0 otherwise.

   Weighted cap = Σ(cap_rate * weight) / Σ(weight).

6. **Coverage quality** — count of comps that survived (1) + (2) + (3):
   high ≥ 8, medium 4-7, low < 4.

The function is **pure**: no DB, no LLM, no I/O. It takes a list of
``CompTransaction`` (the engine_runner extracts these from OM payloads
before calling in) and a small config and returns the structured set.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Generic, TypeVar

from fondok_schemas.comp_sales import CompSalesSet, CompTransaction


# ────────────────────────── tunables ─────────────────────────────────


# Recency weight thresholds — keep these on the module so tests can
# import + assert the exact bucket boundaries.
RECENCY_LE_2YR: float = 1.0
RECENCY_LE_4YR: float = 0.7
RECENCY_LE_6YR: float = 0.4
RECENCY_UNKNOWN: float = 0.4   # missing sale_date → middle bucket

# Component weights — the three terms that combine into per-comp weight.
W_RECENCY: float = 0.7
W_MARKET: float = 0.2
W_CHAIN: float = 0.1

# Coverage-quality thresholds (qualifying comps after filter).
COVERAGE_HIGH_MIN: int = 8
COVERAGE_MEDIUM_MIN: int = 4


# ─────────────── C12 — the ONE exit-cap derivation from comps ───────────────

#: Fewest plausible comps that derive an exit cap rate. Below this the comp
#: set is too thin to anchor the exit on, so nothing is derived.
EXIT_CAP_MIN_COMPS: int = 3
#: Plausible hotel exit cap rates, as 0..1 fractions (FON-67). Anything
#: outside is broken data — an IRR (~15-20%) or a debt yield read as a "cap
#: rate" — and would silently collapse (or inflate) the exit value.
EXIT_CAP_PLAUSIBLE_RANGE: tuple[float, float] = (0.04, 0.11)
#: The provenance label the model's ``exit_cap_rate`` carries when this
#: derivation set it (``engine_runner.SOURCE_OM_COMPS`` is this string).
EXIT_CAP_COMPS_SOURCE: str = "om_comps"

_T = TypeVar("_T")


def cap_rate_fraction(value: object) -> float | None:
    """A published cap rate as a 0..1 fraction (the extractor and the comp
    schema emit either ``7.25`` or ``0.0725``); ``None`` when not a positive
    number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v <= 0:
        return None
    return v / 100.0 if v > 1.0 else v


@dataclass(frozen=True)
class ExitCapDerivation(Generic[_T]):
    """The result of :func:`exit_cap_from_comps`.

    ``value`` is the derived exit cap rate (0..1 fraction) or ``None`` when
    fewer than :data:`EXIT_CAP_MIN_COMPS` plausible comps exist. ``plausible``
    are the comps that passed the band, sorted by cap rate; ``median_items``
    the one (odd count) or two (even count) comps the median sits on — so a
    caller can cite the row behind the number. ``implausible_count`` is how
    many comps the band dropped.
    """

    value: float | None
    plausible: tuple[_T, ...]
    median_items: tuple[_T, ...]
    implausible_count: int
    source: str = EXIT_CAP_COMPS_SOURCE


def exit_cap_from_comps(
    items: Sequence[_T], cap_rate: Callable[[_T], object]
) -> ExitCapDerivation[_T]:
    """THE exit-cap derivation from comparable sales (C12).

    ``cap_rate(item)`` reads each comp's published cap rate (percent or
    fraction). Comps with no cap rate are ignored; comps outside
    :data:`EXIT_CAP_PLAUSIBLE_RANGE` are dropped and counted; with at least
    :data:`EXIT_CAP_MIN_COMPS` plausible comps the result is their median
    (the mean of the middle two on an even count), as a 0..1 fraction.
    """
    lo, hi = EXIT_CAP_PLAUSIBLE_RANGE
    rated: list[tuple[float, _T]] = []
    implausible = 0
    for item in items:
        v = cap_rate_fraction(cap_rate(item))
        if v is None:
            continue
        if lo <= v <= hi:
            rated.append((v, item))
        else:
            implausible += 1
    rated.sort(key=lambda t: t[0])
    plausible = tuple(item for _, item in rated)
    n = len(rated)
    if n < EXIT_CAP_MIN_COMPS:
        return ExitCapDerivation(None, plausible, (), implausible)
    if n % 2:
        v, item = rated[n // 2]
        return ExitCapDerivation(v, plausible, (item,), implausible)
    (v_lo, i_lo), (v_hi, i_hi) = rated[n // 2 - 1], rated[n // 2]
    return ExitCapDerivation((v_lo + v_hi) / 2, plausible, (i_lo, i_hi), implausible)


# Adjacent-chain-scale graph for the half-weight market match. Stored
# as a frozenset of (a, b) pairs sorted alphabetically so lookup is
# order-insensitive.
_ADJACENT_CHAIN_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        tuple(sorted(pair))  # type: ignore[arg-type]
        for pair in (
            ("upscale", "upper-upscale"),
            ("upper-upscale", "luxury"),
            ("midscale", "upper-midscale"),
            ("upper-midscale", "upscale"),
            ("economy", "midscale"),
        )
    }
)


# ────────────────────────── helpers ──────────────────────────────────


def _normalize_chain(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if not v:
        return None
    # Tolerate the common variants buyers + brokers write: spaces, slash,
    # punctuation. All collapse to a single dash form.
    v = v.replace("/", "-").replace(" ", "-").replace("_", "-")
    while "--" in v:
        v = v.replace("--", "-")
    return v


def _recency_score(sale_date: date | None, today: date) -> float:
    """Bucket the comp's age into the four recency tiers.

    A missing sale_date can't disqualify the comp by lookback (we don't
    know it's stale) but it shouldn't carry full recency weight either —
    we park it in the middle bucket.
    """
    if sale_date is None:
        return RECENCY_UNKNOWN
    years = (today - sale_date).days / 365.25
    if years <= 2.0:
        return RECENCY_LE_2YR
    if years <= 4.0:
        return RECENCY_LE_4YR
    if years <= 6.0:
        return RECENCY_LE_6YR
    return 0.0


def _market_match(
    comp: CompTransaction,
    subject_city: str | None,
    subject_state: str | None,
) -> float:
    """Score the comp's location against the subject's.

    Same MSA → 1.0, same state → 0.5, neither → 0.0. We approximate MSA
    via city-name equality — the OM extractor doesn't currently tag MSA
    codes (the Census-Bureau MSA lookup is roadmapped). Same-city is a
    reasonable proxy for institutional comp sets.
    """
    sub_c = (subject_city or "").strip().lower() or None
    sub_s = (subject_state or "").strip().lower() or None
    comp_c = (comp.city or "").strip().lower() or None
    comp_s = (comp.state or "").strip().lower() or None
    if sub_c and comp_c and sub_c == comp_c:
        return 1.0
    if sub_s and comp_s and sub_s == comp_s:
        return 0.5
    return 0.0


def _chain_match(comp: CompTransaction, subject_chain: str | None) -> float:
    sub = _normalize_chain(subject_chain)
    cmp_cs = _normalize_chain(comp.chain_scale)
    if sub is None or cmp_cs is None:
        return 0.0
    if sub == cmp_cs:
        return 1.0
    pair = tuple(sorted((sub, cmp_cs)))
    if pair in _ADJACENT_CHAIN_PAIRS:
        return 0.5
    return 0.0


def _coverage_quality(n: int) -> str:
    if n >= COVERAGE_HIGH_MIN:
        return "high"
    if n >= COVERAGE_MEDIUM_MIN:
        return "medium"
    return "low"


def _split_city_state(subject_market: str | None) -> tuple[str | None, str | None]:
    """Parse ``"Houston, TX"`` → ``("Houston", "TX")``.

    Returns ``(None, None)`` for empty input. Single-token input is
    treated as a city (not a state) — the caller is more likely to
    supply just a city name than a bare state abbreviation.
    """
    if not subject_market:
        return None, None
    parts = [p.strip() for p in subject_market.split(",") if p.strip()]
    if len(parts) == 0:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


# ────────────────────────── main entrypoint ──────────────────────────


#: The comp-sales lookback (years) — the tab's and the model's (C12).
COMP_LOOKBACK_YEARS: int = 5


@dataclass(frozen=True)
class CompSelection:
    """The comps a derivation may use, and what was dropped and why."""

    qualifying: tuple[CompTransaction, ...]
    excluded_count: int
    too_old_count: int
    missing_date_count: int
    missing_cap_count: int


def select_comps(
    transactions: Sequence[CompTransaction],
    *,
    today: date,
    lookback_years: int = COMP_LOOKBACK_YEARS,
    exclude_transaction_ids: Sequence[str] | None = None,
) -> CompSelection:
    """THE comp selection (review fix to C12) — called by
    :func:`build_comp_set` (the Comparable Sales tab) and by the engine
    loader for the model's exit cap, so both derive from the same comps.

    Drops, in order: comps the analyst excluded (``transaction_id`` in
    ``exclude_transaction_ids``), comps sold more than ``lookback_years``
    before ``today`` (a comp with no sale date is kept and counted), and comps
    with no published cap rate.
    """
    exclude_set = set(exclude_transaction_ids or [])
    after_exclude: list[CompTransaction] = []
    excluded_count = 0
    for t in transactions:
        if t.transaction_id and t.transaction_id in exclude_set:
            excluded_count += 1
            continue
        after_exclude.append(t)
    cutoff = today - timedelta(days=int(365.25 * lookback_years))
    after_lookback: list[CompTransaction] = []
    too_old_count = 0
    missing_date_count = 0
    for t in after_exclude:
        if t.sale_date is None:
            missing_date_count += 1
            after_lookback.append(t)
            continue
        if t.sale_date < cutoff:
            too_old_count += 1
            continue
        after_lookback.append(t)
    qualifying = tuple(t for t in after_lookback if t.cap_rate_pct is not None)
    return CompSelection(
        qualifying=qualifying,
        excluded_count=excluded_count,
        too_old_count=too_old_count,
        missing_date_count=missing_date_count,
        missing_cap_count=len(after_lookback) - len(qualifying),
    )


def build_comp_set(
    deal_id: str,
    transactions: list[CompTransaction],
    *,
    subject_chain_scale: str | None = None,
    subject_market: str | None = None,
    lookback_years: int = COMP_LOOKBACK_YEARS,
    exclude_transaction_ids: list[str] | None = None,
    today: date | None = None,
) -> CompSalesSet:
    """Compute the derived comp set for a deal.

    Parameters
    ----------
    deal_id:
        Required for traceability — echoed into ``weighting_notes`` so
        the audit trail downstream shows which deal the run belonged
        to. The engine doesn't validate the format (caller does).
    transactions:
        Raw comp rows the engine_runner extracted from OMs. Pass the
        full universe — the engine filters internally.
    subject_chain_scale:
        Subject hotel's chain-scale label (e.g. ``"upper-upscale"``)
        for the chain-match weight. ``None`` collapses chain_match → 0
        for every comp.
    subject_market:
        Subject hotel's market as ``"City, ST"`` (e.g. ``"Houston, TX"``)
        for the market-match weight. ``None`` collapses
        market_match → 0 for every comp.
    lookback_years:
        Drop comps with a ``sale_date`` older than this. Default 5;
        institutional convention for hotel exit-cap derivation.
    exclude_transaction_ids:
        Per-deal analyst exclude list. Rows with a matching
        ``transaction_id`` are dropped before derivation and tagged in
        ``weighting_notes``.
    today:
        Override the "today" anchor for recency scoring. Defaults to
        :func:`date.today`. Tests pin this for determinism.

    Returns
    -------
    CompSalesSet
        Carries the full transactions list (unfiltered, for the table
        view), the derived median + weighted cap rates, the method
        actually used, the coverage-quality label and a list of
        analyst-readable filter notes.
    """
    today = today or date.today()
    total_count = len(transactions)
    # Review fix (C12) — the ONE comp selection the model's exit cap uses too.
    selection = select_comps(
        transactions,
        today=today,
        lookback_years=lookback_years,
        exclude_transaction_ids=exclude_transaction_ids,
    )
    qualifying = list(selection.qualifying)
    excluded_count = selection.excluded_count
    too_old_count = selection.too_old_count
    missing_date_count = selection.missing_date_count
    missing_cap_count = selection.missing_cap_count

    notes: list[str] = []
    if excluded_count:
        notes.append(
            f"{excluded_count} comp(s) excluded by analyst override"
        )
    if too_old_count:
        notes.append(
            f"{too_old_count} comp(s) excluded: sale > {lookback_years} yrs old"
        )
    if missing_date_count:
        notes.append(
            f"{missing_date_count} comp(s) included with no sale_date "
            f"(recency bucketed as unknown)"
        )
    if missing_cap_count:
        notes.append(
            f"{missing_cap_count} comp(s) excluded: no cap rate published"
        )

    # C12 — the plausibility band, the minimum count and the median are the
    # ONE derivation the model's exit cap uses too (``exit_cap_from_comps``).
    derivation = exit_cap_from_comps(qualifying, lambda t: t.cap_rate_pct)
    if derivation.implausible_count:
        lo, hi = EXIT_CAP_PLAUSIBLE_RANGE
        notes.append(
            f"{derivation.implausible_count} comp(s) excluded: cap rate outside "
            f"the plausible {lo:.0%}–{hi:.0%} band"
        )
    qualifying = list(derivation.plausible)
    coverage = _coverage_quality(len(qualifying))

    if derivation.value is None:
        if qualifying:
            notes.append(
                f"{len(qualifying)} comp(s) qualified — fewer than "
                f"{EXIT_CAP_MIN_COMPS}, so no exit cap rate is derived from the comp set"
            )
        else:
            notes.append("0 comps qualified — cannot derive cap rate from comp set")
        return CompSalesSet(
            transactions=transactions,
            total_count=total_count,
            derived_cap_rate_median=None,
            derived_cap_rate_weighted=None,
            derived_cap_rate_method="none",
            weighting_notes=notes,
            coverage_quality=coverage,
            subject_market=subject_market,
            subject_chain_scale=subject_chain_scale,
            lookback_years=lookback_years,
        )

    # The schema carries cap rates in percent (0..30).
    median_cap = derivation.value * 100.0

    # ── weighted derivation ──────────────────────────────────────────
    subject_city, subject_state = _split_city_state(subject_market)
    have_market = bool(subject_market)
    have_chain = bool(subject_chain_scale)

    # Without ANY subject metadata, the weighted formula degenerates to
    # recency-only — at that point analysts can't tell the weighted
    # number apart from "the median with newer comps tilted heavier",
    # and reporting it as the official method is misleading. We still
    # compute it (the UI shows it under "show weighted") but flag the
    # method as "median".
    weighted_cap: float | None = None
    total_weight = 0.0
    weighted_numerator = 0.0
    for t in qualifying:
        recency = _recency_score(t.sale_date, today)
        market = _market_match(t, subject_city, subject_state)
        chain = _chain_match(t, subject_chain_scale)
        w = W_RECENCY * recency + W_MARKET * market + W_CHAIN * chain
        if w <= 0:
            continue
        if t.cap_rate_pct is None:
            continue
        total_weight += w
        weighted_numerator += float(t.cap_rate_pct) * w

    if total_weight > 0:
        weighted_cap = weighted_numerator / total_weight

    if have_market or have_chain:
        method: str = "weighted" if weighted_cap is not None else "median"
    else:
        method = "median"
        notes.append(
            "weighted: no subject market or chain-scale supplied — "
            "recency-only weighting, reporting median as method"
        )

    if method == "weighted":
        msa_hits = sum(
            1 for t in qualifying
            if _market_match(t, subject_city, subject_state) == 1.0
        )
        state_hits = sum(
            1 for t in qualifying
            if _market_match(t, subject_city, subject_state) == 0.5
        )
        chain_hits = sum(
            1 for t in qualifying
            if _chain_match(t, subject_chain_scale) == 1.0
        )
        notes.append(
            f"weighted: {len(qualifying)} comps qualified · "
            f"{msa_hits} same-MSA · {state_hits} same-state · "
            f"{chain_hits} same-chain-scale"
        )

    return CompSalesSet(
        transactions=transactions,
        total_count=total_count,
        derived_cap_rate_median=median_cap,
        derived_cap_rate_weighted=weighted_cap,
        derived_cap_rate_method=method,  # type: ignore[arg-type]
        weighting_notes=notes,
        coverage_quality=coverage,  # type: ignore[arg-type]
        subject_market=subject_market,
        subject_chain_scale=subject_chain_scale,
        lookback_years=lookback_years,
    )


__all__ = [
    "COMP_LOOKBACK_YEARS",
    "COVERAGE_HIGH_MIN",
    "CompSelection",
    "EXIT_CAP_COMPS_SOURCE",
    "EXIT_CAP_MIN_COMPS",
    "EXIT_CAP_PLAUSIBLE_RANGE",
    "ExitCapDerivation",
    "COVERAGE_MEDIUM_MIN",
    "RECENCY_LE_2YR",
    "RECENCY_LE_4YR",
    "RECENCY_LE_6YR",
    "RECENCY_UNKNOWN",
    "W_CHAIN",
    "W_MARKET",
    "W_RECENCY",
    "build_comp_set",
    "cap_rate_fraction",
    "exit_cap_from_comps",
    "select_comps",
]
