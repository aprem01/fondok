"""Structural document recognizer (Sam QA Bug #3/#2 v4 — June 28 2026).

Why a structural recognizer
---------------------------

The three previous USALI-scoring fixes (v1 path aliases, v2 expanded
aliases, v3 token-aware resolver) all "worked against saved fixtures"
then **failed on fresh prod extractions**. The fundamental error:
every fix coupled itself to the namespace the LLM happened to emit
on the fixture-capture day. Two days later the LLM emits the same
P&L under different paths (``p_and_l_usali.rooms.revenue_usd``
on day 1, ``p_and_l.rooms_dept.revenue_usd`` on day 2,
``pages.p1.rooms.revenue`` on day 3) and every alias / token map
misses.

The same structural blindness also breaks the Router. Sam's QA on
Wave 4 caught a 181-field T-12 P&L getting classified as "Basic
Property Info" — because the Router only sees the filename + first
~2k chars, and when the filename is generic ("anglers_t12.xlsx" the
content-sample is mostly headers without big dollar lines) it loses
confidence and falls back to the safest catch-all.

This module solves BOTH problems with one piece of code: a
**structural** recognizer that walks the extracted-payload tree and
matches **key names** against regex patterns regardless of nesting
depth or namespace prefix. The patterns are anchored on the canonical
P&L vocabulary (``rooms`` + ``revenue``, ``property_tax``, ``gop``,
…) — none of which the LLM can credibly rename. Once the recognizer
has counted enough P&L-shaped lines AND surfaced enough canonical
keys with their values, both the Router (do not flag misclassified
when the user tagged it T-12 and the structure agrees) and the USALI
scorer (use the canonical_payload the recognizer extracted, not the
raw paths) become stable.

Design contract
---------------

* **Tree-walking** — accept any nested dict/list/scalar structure.
  Both the list-of-records extraction shape
  (``[{"field_name": "p_and_l_usali.rooms.revenue_usd", "value": 9.3M, …}, …]``)
  and the flat dotted-paths shape (``{"p_and_l_usali.rooms.revenue_usd": 9.3M, …}``)
  are supported.
* **Subordinate-namespace exclusion** — paths under ``.monthly.``,
  ``.quarterly.``, ``.page<n>.``, ``.q1.``, ``.q2.``, etc. are per-slice
  values, never period totals. The recognizer ignores them when
  picking the canonical value for a concept (it still counts them
  toward the dollar-field tally because they signal P&L-ness).
* **Regex-on-leaf-key** — patterns match against the leaf key name
  (last dotted segment) AND the full path. This catches both
  ``p_and_l_usali.rooms.revenue_usd`` (leaf=``revenue_usd``, full=
  ``...rooms.revenue_usd``) AND ``rooms_revenue_usd`` (bare leaf).
* **Concept-token discriminators** — ``revenue`` patterns reject keys
  containing ``expense`` / ``profit`` / ``margin``; ``expense`` patterns
  reject keys containing ``revenue`` / ``income`` / ``sales`` / ``profit``.
  Keeps ``rooms_dept_expense`` from being grabbed as ``rooms_revenue``.
* **Tightest-match wins** — when multiple paths match a concept, the
  shortest path (fewest extra tokens) wins. Deterministic.

Why not just patch the alias map (v1/v2) or the token resolver (v3)?
-------------------------------------------------------------------

The alias map enumerates *named paths*. The token resolver tokenizes
canonical names against payload keys. Both still depend on the LLM
emitting *something* recognizable as the canonical vocabulary.
Neither bridges the gap when the LLM emits ``p_and_l.rooms_revenue``
on one run and ``hotel_revenues.rooms_segment.gross`` on the next.

The structural recognizer doesn't enumerate paths — it enumerates
**concepts** (rooms revenue, property tax, GOP, …) and tries every
plausible spelling. The patterns below are anchored on USALI
vocabulary, which is industry-standard and the LLM cannot
meaningfully drift away from.

Public API
----------

* :func:`classify_structure` — primary entry point. Takes the
  extraction payload, returns a :class:`StructuralSignals`.
* :func:`canonical_payload_from_signals` — packs the recognizer's
  surfaced canonical keys + values into a flat dict the USALI scorer
  can score directly (no aliases / token resolver needed).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# ─────────────────────────── public dataclass ───────────────────────────


@dataclass(frozen=True)
class StructuralSignals:
    """Output of :func:`classify_structure`.

    Fields:
        is_pnl: True when the recognizer is ≥ ~70% confident the
            payload represents a P&L (T-12, annual, monthly, YTD).
        pnl_score: 0..1 numerical confidence — the per-canonical-key
            match count divided by the cap (≥ 6 distinct keys at 1.0).
        revenue_line_count: number of distinct revenue-family lines
            matched (rooms / fb / other / misc / resort_fees / total).
        expense_line_count: number of distinct expense-family lines
            matched (dept / undistributed / fixed / non-operating).
        dollar_field_count: total number of leaf keys whose value
            is numeric and whose name carries a money indicator
            (``usd`` / ``dollar`` / ``amount``) OR whose leaf name
            is a known P&L concept.
        has_rooms_revenue: any path matched the rooms-revenue pattern.
        has_fb_revenue: any path matched the F&B-revenue pattern.
        has_property_tax: any path matched the property-tax pattern.
        has_management_fee: any path matched the management-fee
            pattern.
        has_gop_or_noi: any path matched the GOP / NOI rollup pattern.
        canonical_keys_matched: ordered list of canonical concept
            names the recognizer surfaced (e.g.
            ``["rooms_revenue", "property_tax", "gop", …]``).
        canonical_values: ``{canonical_name: numeric_value}`` — the
            tightest-match value for each surfaced concept. Used by
            the USALI scorer to evaluate rules without going through
            the alias map.
        reason: human-readable explanation (printed in the deviation
            log so QA can see why the recognizer chose its label).
    """

    is_pnl: bool
    pnl_score: float
    revenue_line_count: int
    expense_line_count: int
    dollar_field_count: int
    has_rooms_revenue: bool
    has_fb_revenue: bool
    has_property_tax: bool
    has_management_fee: bool
    has_gop_or_noi: bool
    canonical_keys_matched: list[str] = field(default_factory=list)
    canonical_values: dict[str, float] = field(default_factory=dict)
    reason: str = ""

    # ── STR Trend / comp-set detection (Sam QA Bug J, June 30 2026) ──
    # Mirror of the P&L gates: ``is_str`` is the recognizer's hard
    # verdict that the payload is an STR / CoStar Trend report (subject
    # + comp set + penetration indices), and ``str_score`` is the 0..1
    # confidence (count of distinct STR-canonical concepts surfaced
    # divided by the cap). Used by ``documents.py`` to flip a Router
    # T12/PNL mis-classification onto the ``STR_TREND`` lane so the
    # comp-set / Index Analysis pipeline (which keys on STR_TREND /
    # STR) actually sees the row. The signal vocabulary is
    # unmistakably STR:
    #   * ``comp_set.*`` / ``compset.<n>.*`` (rollups + per-competitor)
    #   * ``ttm_performance.subject.*`` (subject Occ/ADR/RevPAR)
    #   * ``mpi_occupancy_index`` / ``ari_adr_index`` /
    #     ``rgi_revpar_index`` (penetration indices — STR-only)
    #   * ``weekly_performance.*`` / ``day_of_week.*`` (CoStar slice tabs)
    # None of these credibly appear on a P&L payload, so a high
    # ``str_score`` is a strong signal the Router got it wrong.
    is_str: bool = False
    str_score: float = 0.0
    str_keys_matched: list[str] = field(default_factory=list)


# ─────────────────────────── concept patterns ───────────────────────────


@dataclass(frozen=True)
class _ConceptPattern:
    """One canonical concept the recognizer tries to surface.

    ``required_any``: at least one of these regex token patterns must
    appear in the leaf-key tokens (after lowercasing + splitting on
    ``[._-]``). Patterns are matched against the *joined* token sequence.

    ``forbidden_any``: any of these tokens disqualifies the candidate.
    Used to keep e.g. ``rooms_revenue`` from grabbing
    ``rooms_dept_expense`` just because two tokens overlap.

    ``family``: ``"revenue"`` / ``"expense"`` / ``"rollup"`` / ``"kpi"``
    / ``"meta"``. Used to count per-family signal totals.
    """

    name: str
    required_all: tuple[re.Pattern[str], ...]
    forbidden_any: tuple[str, ...]
    family: str


# Each pattern is a tuple of regexes ALL of which must hit the token
# stream. Tokens are the lowercased path split on ``[._-]``.
# Forbidden tokens disqualify candidates that look superficially
# similar (rooms_revenue vs rooms_dept_expense). The pattern names
# are the canonical concept names the USALI scorer's rule catalog
# uses (revpar, rooms_revenue, total_revenue, …).
_REV_TOKENS = r"(revenue|revenues|income|sales|gross)"
_EXP_TOKENS = r"(expense|expenses|cost|costs)"
_PROFIT_TOKENS = r"(profit|margin)"
_USD_TOKENS = r"(usd|dollar|dollars|amount)"


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_CONCEPT_PATTERNS: tuple[_ConceptPattern, ...] = (
    # ── KPIs ──────────────────────────────────────────────────────────
    _ConceptPattern(
        name="revpar",
        required_all=(_re(r"\brevpar\b"),),
        forbidden_any=("yoy", "growth", "compset", "comp_set", "broker"),
        family="kpi",
    ),
    _ConceptPattern(
        name="adr",
        required_all=(_re(r"\badr\b"),),
        forbidden_any=("yoy", "growth", "compset", "comp_set", "broker"),
        family="kpi",
    ),
    _ConceptPattern(
        name="occupancy",
        required_all=(_re(r"\b(occupancy|occ)\b"),),
        forbidden_any=("yoy", "growth", "compset", "comp_set", "broker"),
        family="kpi",
    ),
    # ── Revenue lines ─────────────────────────────────────────────────
    _ConceptPattern(
        name="rooms_revenue",
        required_all=(_re(r"\brooms?\b"), _re(rf"\b{_REV_TOKENS}\b")),
        forbidden_any=(
            "expense", "expenses", "cost", "costs",
            "profit", "margin",
            # don't grab "rooms_sold" or "available_rooms"
            "sold", "available",
            "tax", "taxes",
        ),
        family="revenue",
    ),
    _ConceptPattern(
        name="fb_revenue",
        required_all=(
            _re(r"\b(fb|fnb|food|beverage|food_and_beverage)\b"),
            _re(rf"\b{_REV_TOKENS}\b"),
        ),
        forbidden_any=("expense", "expenses", "cost", "costs", "profit", "margin"),
        family="revenue",
    ),
    _ConceptPattern(
        name="other_revenue",
        required_all=(
            _re(r"\b(other|other_operated|other_operated_departments)\b"),
            _re(rf"\b{_REV_TOKENS}\b"),
        ),
        forbidden_any=("expense", "expenses", "cost", "costs", "profit", "margin"),
        family="revenue",
    ),
    _ConceptPattern(
        name="misc_revenue",
        required_all=(
            _re(r"\b(misc|miscellaneous)\b"),
            _re(rf"\b({_REV_TOKENS}|income)\b"),
        ),
        forbidden_any=("expense", "expenses", "cost", "costs", "profit", "margin"),
        family="revenue",
    ),
    _ConceptPattern(
        name="total_revenue",
        required_all=(
            _re(r"\btotal\b"),
            _re(rf"\b{_REV_TOKENS}\b"),
        ),
        forbidden_any=("expense", "expenses", "cost", "costs", "profit", "margin"),
        family="revenue",
    ),
    _ConceptPattern(
        name="resort_fees",
        required_all=(_re(r"\bresort\b"), _re(r"\bfees?\b")),
        forbidden_any=("expense", "expenses"),
        family="revenue",
    ),
    # ── Departmental expenses ─────────────────────────────────────────
    _ConceptPattern(
        name="rooms_dept_expense",
        required_all=(_re(r"\brooms?\b"), _re(rf"\b{_EXP_TOKENS}\b")),
        forbidden_any=("revenue", "revenues", "income", "sales", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="fb_dept_expense",
        required_all=(
            _re(r"\b(fb|fnb|food|beverage|food_and_beverage)\b"),
            _re(rf"\b{_EXP_TOKENS}\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "sales", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="other_dept_expense",
        required_all=(
            _re(r"\b(other|other_operated|other_operated_departments)\b"),
            _re(rf"\b{_EXP_TOKENS}\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "sales", "profit", "margin"),
        family="expense",
    ),
    # ── Undistributed expenses ────────────────────────────────────────
    _ConceptPattern(
        name="ag_expense",
        required_all=(
            _re(r"\b(administrative|admin|ag|a_and_g|administrative_and_general)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="marketing_expense",
        required_all=(
            _re(r"\b(marketing|sales_and_marketing|sales_marketing)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="rm_expense",
        required_all=(
            _re(r"\b(repairs|maintenance|property_operations|operations_and_maintenance)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="utilities_expense",
        required_all=(_re(r"\b(utilities|utility)\b"),),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="information_telecom",
        required_all=(
            _re(r"\b(information|telecom|telecommunications|information_telecom_systems)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    # ── Fixed charges / non-operating ────────────────────────────────
    _ConceptPattern(
        name="property_tax",
        required_all=(_re(r"\bproperty\b"), _re(r"\b(tax|taxes)\b")),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    _ConceptPattern(
        name="insurance_expense",
        required_all=(_re(r"\binsurance\b"),),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    # ── Fees & reserves ──────────────────────────────────────────────
    _ConceptPattern(
        name="mgmt_fee",
        required_all=(
            _re(r"\b(management|mgmt)\b"),
            _re(r"\b(fee|fees)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin", "incentive"),
        family="expense",
    ),
    _ConceptPattern(
        name="ffe_reserve",
        required_all=(
            _re(r"\b(ffe|ff|replacement)\b"),
            _re(r"\b(reserve|reserves|replacement)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "profit", "margin"),
        family="expense",
    ),
    # ── Roll-ups ─────────────────────────────────────────────────────
    _ConceptPattern(
        name="gop",
        required_all=(_re(r"\b(gop|gross_operating_profit)\b"),),
        forbidden_any=("yoy", "growth", "broker"),
        family="rollup",
    ),
    _ConceptPattern(
        name="noi",
        required_all=(_re(r"\b(noi|net_operating_income)\b"),),
        forbidden_any=("yoy", "growth", "broker"),
        family="rollup",
    ),
    _ConceptPattern(
        name="ebitda",
        required_all=(_re(r"\bebitda\b"),),
        forbidden_any=("yoy", "growth", "broker"),
        family="rollup",
    ),
)


# ─────────────────────── STR / CoStar Trend concepts ───────────────────
#
# Sam QA Bug J (June 30 2026) — when the Router mis-routes an STR Trend
# Excel file as ``T12`` (the mirror of Bug H), two downstream effects:
#
#   1. The doc never reaches the comp-set / Index Analysis pipeline,
#      which keys on ``doc_type IN ('STR', 'STR_TREND')``.
#   2. The T12 extractor runs against STR-shaped content, hangs in
#      EXTRACTING for ~6 minutes burning the post-``5507923`` retry
#      budget before any error surfaces (the LLM keeps trying to find
#      P&L lines that aren't there).
#
# These patterns are the STR / CoStar Trend vocabulary the str_trend
# extraction schema authoritatively defines (see
# ``apps/worker/app/agents/extraction_schemas/str_trend.md``):
# ``ttm_performance.subject.*`` (subject Occ/ADR/RevPAR + monthly
# slices), ``ttm_performance.compset.<n>.*`` (per-competitor names +
# rooms + Occ/ADR/RevPAR), ``ttm_performance.indices.*`` (penetration
# indices), ``comp_set.comp_set_size`` / ``comp_set.total_keys``
# (rollups), and the optional ``weekly_performance.*`` /
# ``day_of_week.*`` slice tabs CoStar emits. None of these credibly
# appear on a P&L payload, so the recognizer can use them to flip a
# Router T12/PNL miscall onto the STR_TREND lane with high confidence.
_STR_CONCEPT_PATTERNS: tuple[_ConceptPattern, ...] = (
    # ── Penetration indices (subject vs comp set; STR-only vocab) ────
    _ConceptPattern(
        name="mpi_occupancy_index",
        required_all=(_re(r"\b(mpi|occupancy_index)\b"),),
        forbidden_any=(),
        family="kpi",
    ),
    _ConceptPattern(
        name="ari_adr_index",
        required_all=(_re(r"\b(ari|adr_index)\b"),),
        forbidden_any=(),
        family="kpi",
    ),
    _ConceptPattern(
        name="rgi_revpar_index",
        required_all=(_re(r"\b(rgi|revpar_index)\b"),),
        forbidden_any=(),
        family="kpi",
    ),
    # ── Comp-set rollups ─────────────────────────────────────────────
    _ConceptPattern(
        name="comp_set_size",
        required_all=(_re(r"\bcomp_?set\b"), _re(r"\bsize\b")),
        forbidden_any=(),
        family="meta",
    ),
    _ConceptPattern(
        name="comp_set_total_keys",
        required_all=(
            _re(r"\bcomp_?set\b"),
            _re(r"\b(total_)?(keys|rooms)\b"),
        ),
        forbidden_any=("revenue", "revenues", "income", "expense"),
        family="meta",
    ),
    # ── Per-competitor rows (compset.1.*, compset.2.*, …) ────────────
    # Matches paths like ``ttm_performance.compset.3.adr_usd`` or
    # ``ttm_performance.compset.5.keys``. The numeric token discriminates
    # this from the rollup ``comp_set.*`` shape.
    _ConceptPattern(
        name="compset_competitor_rows",
        required_all=(_re(r"\bcompset\b"), _re(r"\b\d+\b")),
        forbidden_any=(),
        family="meta",
    ),
    # ── Subject TTM Occ/ADR/RevPAR (STR-shape signal — distinct from
    # the same trio appearing inside an OM where it'd be a one-off
    # underwriting input rather than a full ttm_performance tree.) ───
    _ConceptPattern(
        name="ttm_subject_occupancy",
        required_all=(
            _re(r"\bttm_performance\b"),
            _re(r"\bsubject\b"),
            _re(r"\b(occupancy|occ)\b"),
        ),
        forbidden_any=(),
        family="kpi",
    ),
    _ConceptPattern(
        name="ttm_subject_adr",
        required_all=(
            _re(r"\bttm_performance\b"),
            _re(r"\bsubject\b"),
            _re(r"\badr\b"),
        ),
        forbidden_any=(),
        family="kpi",
    ),
    _ConceptPattern(
        name="ttm_subject_revpar",
        required_all=(
            _re(r"\bttm_performance\b"),
            _re(r"\bsubject\b"),
            _re(r"\brevpar\b"),
        ),
        forbidden_any=(),
        family="kpi",
    ),
    # ── CoStar slice tabs (weekly / day-of-week) — strong STR signal ──
    _ConceptPattern(
        name="weekly_performance",
        required_all=(_re(r"\bweekly_performance\b"),),
        forbidden_any=(),
        family="meta",
    ),
    _ConceptPattern(
        name="day_of_week_breakdown",
        required_all=(_re(r"\bday_of_week\b"),),
        forbidden_any=(),
        family="meta",
    ),
)


# ─────────────────────────── subordinate-namespace filter ─────────────


_SUBORDINATE_TOKENS: tuple[str, ...] = (
    "monthly", "per_month", "permonth", "quarterly", "perquarter",
    "q1", "q2", "q3", "q4",
)

_MONTH_NAME_RE = re.compile(
    r"\b(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|"
    r"aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?)"
    r"(_?\d{2,4})?\b",
    re.IGNORECASE,
)
_PAGE_RE = re.compile(r"\.page\d+\.", re.IGNORECASE)


def _is_subordinate_path(path: str) -> bool:
    """True for per-month / per-quarter / per-page slice paths.

    These carry per-slice values, not period totals — including them in
    the canonical-key picks would yield monthly numbers under a yearly
    rule. Always tracked in the dollar-field count (signals P&L-ness),
    never picked as a canonical value.
    """
    lowered = path.lower()
    if any(f".{tok}." in f".{lowered}." for tok in _SUBORDINATE_TOKENS):
        return True
    if _PAGE_RE.search(lowered):
        return True
    # Heuristic: if a path contains a month-name token mid-path
    # (not at the very end as a period_ending label), treat as subordinate.
    segments = lowered.split(".")
    if len(segments) > 1:
        for seg in segments[:-1]:
            if _MONTH_NAME_RE.search(seg):
                return True
    return False


# ─────────────────────────── token tools ──────────────────────────────


_TOKEN_SPLIT_RE = re.compile(r"[._\-/]+")


def _tokenize_path(path: str) -> list[str]:
    """Lowercase + split on ``[._\\-/]``. Returns empty list for empty
    input. Used to feed the concept patterns."""
    if not path:
        return []
    return [t for t in _TOKEN_SPLIT_RE.split(path.lower()) if t]


def _joined_token_stream(path: str) -> str:
    """``p_and_l_usali.rooms.revenue_usd`` →
    ``"p and l usali rooms revenue usd"``. The concept regexes use
    ``\\b…\\b`` so a single space-joined token stream is sufficient
    matching surface for both prefix and suffix tokens."""
    return " ".join(_tokenize_path(path))


def _coerce_to_float(v: Any) -> float | None:
    """Numeric coerce — same conventions as the USALI scorer's
    ``_coerce_number``. ``None`` for booleans, NaN, infinities, or
    unparseable strings."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        if s.endswith("%"):
            try:
                f = float(s[:-1])
            except ValueError:
                return None
            return f / 100.0 if math.isfinite(f) else None
        try:
            f = float(s)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


# ─────────────────────────── tree walker ──────────────────────────────


@dataclass
class _Leaf:
    """One leaf key in the extraction payload — path + numeric value."""

    path: str
    value: float
    is_subordinate: bool


def _flatten_payload(
    payload: Any,
    *,
    prefix: str = "",
) -> Iterable[_Leaf]:
    """Yield every numeric-leaf node in ``payload`` as ``_Leaf(path,
    value)``. Handles three shapes:

    1. List-of-records (extractor output):
       ``[{"field_name": "p_and_l_usali.rooms.revenue_usd", "value": 9.3M, …}, …]``.
       Each record yields one leaf at its ``field_name``.
    2. Nested dict:
       ``{"p_and_l_usali": {"rooms": {"revenue_usd": 9.3M}}}``.
       Walks recursively, building the dotted path.
    3. Flat dotted-paths dict:
       ``{"p_and_l_usali.rooms.revenue_usd": 9.3M}``. Keys are taken
       at face value.

    Other-shaped payloads (top-level scalar, mixed dict+list-of-records,
    etc.) yield nothing rather than raising — the recognizer treats
    silent emptiness as "not a P&L" rather than failing the upload.
    """
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            # Heuristic: extractor list-of-records carry "field_name" / "value".
            name = entry.get("field_name") if "field_name" in entry else None
            value = entry.get("value")
            if isinstance(name, str) and name:
                num = _coerce_to_float(value)
                if num is None:
                    continue
                full_path = f"{prefix}{name}" if prefix else name
                yield _Leaf(
                    path=full_path,
                    value=num,
                    is_subordinate=_is_subordinate_path(full_path),
                )
                continue
            # Otherwise: a list of nested dicts; recurse with the same prefix.
            yield from _flatten_payload(entry, prefix=prefix)
    elif isinstance(payload, dict):
        for raw_key, raw_val in payload.items():
            if not isinstance(raw_key, str):
                continue
            key = raw_key
            new_prefix = f"{prefix}{key}" if prefix == "" else f"{prefix}.{key}"
            if isinstance(raw_val, (dict, list)):
                yield from _flatten_payload(raw_val, prefix=new_prefix)
            else:
                num = _coerce_to_float(raw_val)
                if num is None:
                    continue
                yield _Leaf(
                    path=new_prefix,
                    value=num,
                    is_subordinate=_is_subordinate_path(new_prefix),
                )


# ─────────────────────────── concept matcher ──────────────────────────


def _candidate_paths_for(
    leaves: list[_Leaf], pattern: _ConceptPattern
) -> list[_Leaf]:
    """Return every leaf whose token stream satisfies the pattern.

    Forbidden tokens disqualify; subordinate-namespace leaves are
    rejected (per-month etc.). Returns matches unsorted — caller picks
    the tightest via path length.
    """
    matches: list[_Leaf] = []
    for leaf in leaves:
        if leaf.is_subordinate:
            continue
        stream = _joined_token_stream(leaf.path)
        if not stream:
            continue
        # Forbidden check — any disqualifying token aborts.
        token_bag = set(stream.split())
        if any(forbid in token_bag for forbid in pattern.forbidden_any):
            continue
        # Required check — every required-all regex must hit the stream.
        if not all(rx.search(stream) for rx in pattern.required_all):
            continue
        matches.append(leaf)
    return matches


def _pick_tightest(
    matches: list[_Leaf], pattern_name: str
) -> _Leaf | None:
    """Pick the leaf with the fewest extra tokens / shortest path.

    Ties: shorter path wins; further ties resolved by sorted-path order
    (deterministic). Returns ``None`` for an empty list.
    """
    if not matches:
        return None

    def _score(leaf: _Leaf) -> tuple[int, int, str]:
        tokens = _tokenize_path(leaf.path)
        # "extras" = tokens that aren't part of any recognized vocabulary —
        # approximated as total token count (lower = tighter).
        return (len(tokens), len(leaf.path), leaf.path)

    return sorted(matches, key=_score)[0]


# ─────────────────────────── classifier ────────────────────────────────


# Concepts required for "this is definitely a P&L". Rooms revenue is
# the strongest anchor (every hotel P&L has it). At least one expense +
# at least one rollup are required so a property-info doc that
# happens to mention "rooms revenue" in a description field doesn't
# get flagged as P&L.
_PNL_REQUIRED_REV_CONCEPTS: tuple[str, ...] = (
    "rooms_revenue", "fb_revenue", "other_revenue", "misc_revenue",
    "total_revenue", "resort_fees",
)
_PNL_REQUIRED_EXPENSE_CONCEPTS: tuple[str, ...] = (
    "rooms_dept_expense", "fb_dept_expense", "other_dept_expense",
    "property_tax", "insurance_expense", "mgmt_fee", "ag_expense",
    "marketing_expense", "rm_expense", "utilities_expense",
    "information_telecom", "ffe_reserve",
)
_PNL_ROLLUP_CONCEPTS: tuple[str, ...] = ("gop", "noi", "ebitda", "total_revenue")


# Minimum thresholds for a confident P&L classification.
_MIN_DOLLAR_FIELDS = 15
_MIN_DISTINCT_CANONICALS = 6


# STR Trend / CoStar confidence thresholds (Sam QA Bug J).
#
# The STR vocabulary is narrower than P&L (a typical STR Trend report
# carries ~3 indices + 5-7 compset rows + 3 subject TTM lines + maybe
# weekly/day-of-week slices). Three distinct STR concepts is the floor
# at which the recognizer is confident the payload is structurally
# STR-shaped — that's roughly "all three indices" OR "indices + subject
# TTM + compset rollup". A typical full STR Trend extraction surfaces
# 6-9 of these concepts, comfortably above the threshold.
_MIN_STR_DISTINCT_CANONICALS = 3


# The "strong" STR markers — concept names from ``_STR_CONCEPT_PATTERNS``
# that are essentially impossible to see on a P&L payload. At least one
# of these must hit before ``is_str`` flips True; this prevents a stray
# ``adr`` or ``revpar`` field on an OM from triggering an STR_TREND
# override. The penetration indices (MPI/ARI/RGI) are STR-exclusive
# vocabulary; ``compset_*`` and ``weekly_performance`` / ``day_of_week``
# are CoStar Trend report shape signals.
_STR_STRONG_MARKERS: tuple[str, ...] = (
    "mpi_occupancy_index",
    "ari_adr_index",
    "rgi_revpar_index",
    "comp_set_size",
    "comp_set_total_keys",
    "compset_competitor_rows",
    "weekly_performance",
    "day_of_week_breakdown",
)


def classify_structure(payload: Any) -> StructuralSignals:
    """Walk the extracted payload and emit structural signals.

    The recognizer is intentionally **regex-on-key-names** at every
    depth: it does not care about the namespace (``p_and_l_usali.*``,
    ``pages.*``, ``data.*``, …) nor about which specific paths the
    LLM picks. As long as the document carries the canonical USALI
    vocabulary (rooms revenue, property tax, GOP, …) the recognizer
    surfaces it.

    Returns a :class:`StructuralSignals` even on empty / non-P&L
    payloads — the caller checks ``is_pnl`` to gate downstream
    behavior.
    """
    leaves = list(_flatten_payload(payload))

    # Dollar-field tally: any leaf that *could* carry money. We don't
    # require an explicit money token because some extractor flavors
    # emit ``rooms_revenue`` without ``_usd``; instead we count any
    # leaf whose value is numeric and whose token stream contains at
    # least one P&L vocabulary token (revenue / expense / income / etc).
    pnl_vocab_tokens = {
        "revenue", "revenues", "income", "sales", "gross",
        "expense", "expenses", "cost", "costs", "profit", "margin",
        "rooms", "fb", "fnb", "food", "beverage",
        "noi", "gop", "ebitda",
        "tax", "taxes", "insurance", "utilities", "marketing",
        "management", "mgmt", "reserve", "reserves", "ffe",
        "departmental", "undistributed", "fixed",
        "administrative", "admin",
    }
    dollar_field_count = 0
    for leaf in leaves:
        token_bag = set(_tokenize_path(leaf.path))
        if pnl_vocab_tokens & token_bag:
            dollar_field_count += 1

    canonical_values: dict[str, float] = {}
    canonical_keys_matched: list[str] = []
    for pattern in _CONCEPT_PATTERNS:
        candidates = _candidate_paths_for(leaves, pattern)
        if not candidates:
            continue
        best = _pick_tightest(candidates, pattern.name)
        if best is None:
            continue
        if pattern.name in canonical_values:
            continue
        canonical_values[pattern.name] = best.value
        canonical_keys_matched.append(pattern.name)

    revenue_line_count = sum(
        1 for n in canonical_keys_matched
        if n in {"rooms_revenue", "fb_revenue", "other_revenue",
                 "misc_revenue", "total_revenue", "resort_fees"}
    )
    expense_line_count = sum(
        1 for n in canonical_keys_matched
        if n in {"rooms_dept_expense", "fb_dept_expense",
                 "other_dept_expense", "property_tax",
                 "insurance_expense", "mgmt_fee", "ag_expense",
                 "marketing_expense", "rm_expense", "utilities_expense",
                 "information_telecom", "ffe_reserve"}
    )

    has_rooms_revenue = "rooms_revenue" in canonical_values
    has_fb_revenue = "fb_revenue" in canonical_values
    has_property_tax = "property_tax" in canonical_values
    has_management_fee = "mgmt_fee" in canonical_values
    has_gop_or_noi = any(
        n in canonical_values for n in ("gop", "noi", "ebitda")
    )

    distinct_canonicals = len(canonical_keys_matched)
    pnl_score = min(1.0, distinct_canonicals / float(_MIN_DISTINCT_CANONICALS))

    # P&L classification: needs enough $$ fields, at least one revenue,
    # at least one expense, and a rollup (GOP / NOI / EBITDA / total).
    # Total-revenue alone counts as a rollup since every P&L has one;
    # a property-info doc with a "rooms_revenue: $9M" snippet but no
    # other lines won't satisfy the expense or rollup gates.
    has_revenue_signal = revenue_line_count >= 1 and has_rooms_revenue
    has_expense_signal = expense_line_count >= 1
    has_rollup_signal = has_gop_or_noi or "total_revenue" in canonical_values
    enough_dollars = dollar_field_count >= _MIN_DOLLAR_FIELDS
    enough_distinct = distinct_canonicals >= _MIN_DISTINCT_CANONICALS

    is_pnl = bool(
        has_revenue_signal
        and has_expense_signal
        and has_rollup_signal
        and enough_dollars
        and enough_distinct
    )

    # ── STR Trend / CoStar detection (Bug J) ────────────────────────
    #
    # Mirror of the P&L gates above, against the STR concept patterns.
    # The recognizer runs the same tightest-match logic over the STR
    # pattern set; subordinate-namespace filtering is intentionally
    # the same (the monthly subject slices live under
    # ``ttm_performance.subject.monthly.<YYYY_MM>.*`` which is a
    # year/month-named subordinate path and gets filtered, but the
    # TTM rollup + indices + compset rows all live at the
    # non-subordinate root and surface cleanly).
    str_keys_matched: list[str] = []
    for pattern in _STR_CONCEPT_PATTERNS:
        candidates = _candidate_paths_for(leaves, pattern)
        if not candidates:
            continue
        best = _pick_tightest(candidates, pattern.name)
        if best is None:
            continue
        if pattern.name in str_keys_matched:
            continue
        str_keys_matched.append(pattern.name)

    str_distinct = len(str_keys_matched)
    str_score = min(1.0, str_distinct / float(_MIN_STR_DISTINCT_CANONICALS))

    # ``is_str`` requires ≥ 3 distinct STR concepts AND at least one of
    # the "strong" markers (penetration index, compset row, or CoStar
    # slice-tab signal). The strong-marker gate prevents a P&L that
    # happens to mention ADR/RevPAR in a KPI sidebar from flipping to
    # STR_TREND — those would surface ``ttm_subject_*`` but never
    # ``mpi_*`` / ``compset_*`` / ``weekly_performance``.
    has_strong_str_marker = any(
        n in str_keys_matched for n in _STR_STRONG_MARKERS
    )
    is_str = bool(
        str_distinct >= _MIN_STR_DISTINCT_CANONICALS
        and has_strong_str_marker
    )

    # Human-readable explanation. Walked through in the deviation log
    # so QA can debug recognizer misses without re-instrumenting code.
    parts: list[str] = []
    parts.append(f"$ fields={dollar_field_count}")
    parts.append(f"distinct canonicals={distinct_canonicals}")
    parts.append(f"revenue lines={revenue_line_count}")
    parts.append(f"expense lines={expense_line_count}")
    parts.append(f"rooms_rev={has_rooms_revenue}")
    parts.append(f"rollup={has_gop_or_noi or 'total_revenue' in canonical_values}")
    parts.append(f"is_pnl={is_pnl}")
    parts.append(f"str_distinct={str_distinct}")
    parts.append(f"is_str={is_str}")
    reason = ", ".join(parts)

    return StructuralSignals(
        is_pnl=is_pnl,
        pnl_score=pnl_score,
        revenue_line_count=revenue_line_count,
        expense_line_count=expense_line_count,
        dollar_field_count=dollar_field_count,
        has_rooms_revenue=has_rooms_revenue,
        has_fb_revenue=has_fb_revenue,
        has_property_tax=has_property_tax,
        has_management_fee=has_management_fee,
        has_gop_or_noi=has_gop_or_noi,
        canonical_keys_matched=canonical_keys_matched,
        canonical_values=canonical_values,
        reason=reason,
        is_str=is_str,
        str_score=str_score,
        str_keys_matched=str_keys_matched,
    )


def canonical_payload_from_signals(
    signals: StructuralSignals,
    *,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pack the recognizer's surfaced canonical values into a flat dict
    the USALI scorer can score directly.

    The returned dict uses **canonical USALI names** as keys
    (``rooms_revenue``, ``property_tax``, ``gop``, …) — exactly what
    the rule catalog's formulas reference. The scorer no longer needs
    to consult the alias map / token resolver to find them.

    ``extra_context`` (deal-level fields like ``keys``,
    ``purchase_price``, ``coastal``) is merged in last; recognizer
    canonical values take precedence over context with the same name.
    """
    out: dict[str, Any] = {}
    if extra_context:
        for k, v in extra_context.items():
            if v is None:
                continue
            out[k] = v
    for name, val in signals.canonical_values.items():
        out[name] = val
    return out


# ─────────────────────── text-level STR sniff (Bug J) ─────────────────


# Markers we look for in the RAW parsed text of an uploaded doc — used
# by the extractor's fail-fast guard (it doesn't have an extracted
# field tree yet, so the regex-on-key-name classifier above can't run).
#
# These are tokens that show up in the actual STR Trend Excel file
# text (column headers, sheet labels, glossary footers, …) but
# essentially never appear in a P&L's text dump. The threshold below
# is intentionally high — only a clear majority of these markers
# triggers the contradiction guard, so an OM that mentions "comp set"
# in a single paragraph doesn't get flagged.
_STR_TEXT_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcomp[\s_-]?set\b", re.IGNORECASE),
    re.compile(r"\bcompetitive[\s_-]?set\b", re.IGNORECASE),
    re.compile(r"\bweekly[\s_-]?performance\b", re.IGNORECASE),
    re.compile(r"\bday[\s_-]?of[\s_-]?week\b", re.IGNORECASE),
    re.compile(r"\bmpi\b", re.IGNORECASE),
    re.compile(r"\bari\b", re.IGNORECASE),
    re.compile(r"\brgi\b", re.IGNORECASE),
    re.compile(r"\bsmith[\s_-]?travel\b", re.IGNORECASE),
    re.compile(r"\bcostar\b", re.IGNORECASE),
    re.compile(r"\bstr[\s_-]?trend\b", re.IGNORECASE),
    re.compile(r"\boccupancy[\s_-]?index\b", re.IGNORECASE),
    re.compile(r"\badr[\s_-]?index\b", re.IGNORECASE),
    re.compile(r"\brevpar[\s_-]?index\b", re.IGNORECASE),
    re.compile(r"\bpenetration[\s_-]?index\b", re.IGNORECASE),
    re.compile(r"\bby[\s_-]?measure\b", re.IGNORECASE),  # CoStar tab name
    re.compile(r"\bclassic\b.*\btrend\b", re.IGNORECASE),  # CoStar tab name
)


# Tokens that, if abundant, suggest this is genuinely a P&L (so we should
# NOT fail-fast even if a stray "comp set" mention appears). Two-stage
# guard: STR markers count as STR signal AND P&L vocab counts AS P&L
# signal — fail-fast only when STR signal dominates.
_PNL_TEXT_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(rooms?[\s_-]?(revenue|income))\b", re.IGNORECASE),
    re.compile(r"\b(food[\s_-]?(and|&)[\s_-]?beverage|f&b)\b", re.IGNORECASE),
    re.compile(r"\bdepartmental[\s_-]?(expense|profit)\b", re.IGNORECASE),
    re.compile(r"\b(undistributed[\s_-]?operating[\s_-]?expense|operating[\s_-]?expense)\b", re.IGNORECASE),
    re.compile(r"\bgross[\s_-]?operating[\s_-]?profit\b", re.IGNORECASE),
    re.compile(r"\bgop\b", re.IGNORECASE),
    re.compile(r"\bnoi\b", re.IGNORECASE),
    re.compile(r"\bebitda\b", re.IGNORECASE),
    re.compile(r"\bproperty[\s_-]?tax\b", re.IGNORECASE),
    re.compile(r"\bmanagement[\s_-]?fee\b", re.IGNORECASE),
    re.compile(r"\bffe[\s_-]?reserve\b", re.IGNORECASE),
    re.compile(r"\busali\b", re.IGNORECASE),
)


# Minimum number of distinct STR text markers required before the
# extractor fail-fast guard considers the doc "structurally STR-shaped".
# 4 is conservative: a real STR Trend report's first sheet alone
# (Custom Trend / By Measure) typically hits ≥ 8 of the markers above.
_STR_TEXT_MIN_HITS = 4

# Only fail-fast when STR markers OUTWEIGH P&L markers by at least 2x.
# Belt-and-braces: a real P&L that happens to mention "comp set" once
# in a single paragraph won't trigger the guard because the P&L vocab
# would dominate.
_STR_TEXT_VS_PNL_RATIO = 2.0


@dataclass(frozen=True)
class TextSignals:
    """Lightweight text-level signal counts for the extractor's
    fail-fast guard. Computed BEFORE the LLM call, so the classifier
    above (which needs an extracted field tree) cannot be used.

    Fields:
        str_marker_hits: number of distinct STR markers matched in the
            raw text (each pattern counts at most once even if it
            appears many times).
        pnl_marker_hits: number of distinct P&L markers matched.
        looks_str: True when STR markers cross the floor AND outweigh
            P&L markers by ``_STR_TEXT_VS_PNL_RATIO``.
        str_markers_matched: ordered list of marker source patterns
            (for the deviation log).
    """

    str_marker_hits: int
    pnl_marker_hits: int
    looks_str: bool
    str_markers_matched: list[str] = field(default_factory=list)


def detect_text_signals(content: str) -> TextSignals:
    """Sniff raw extracted text for STR Trend / CoStar markers.

    Used by the extractor's fail-fast guard (Bug J): when a doc has
    been routed to T12 / PNL but the parsed text is unambiguously STR-
    shaped, refuse to run the LLM and surface a typed
    ``structural_contradiction`` failure within milliseconds rather than
    burning the 6-minute extractor retry budget.

    ``content`` is the same text the extractor will hand to the LLM
    (post-parse, post-truncation). Empty / non-string input returns a
    neutral ``TextSignals`` (``looks_str=False``).
    """
    if not isinstance(content, str) or not content:
        return TextSignals(
            str_marker_hits=0,
            pnl_marker_hits=0,
            looks_str=False,
            str_markers_matched=[],
        )

    str_hits: list[str] = []
    for rx in _STR_TEXT_MARKERS:
        if rx.search(content):
            str_hits.append(rx.pattern)

    pnl_hits = sum(1 for rx in _PNL_TEXT_MARKERS if rx.search(content))

    looks_str = (
        len(str_hits) >= _STR_TEXT_MIN_HITS
        and (
            pnl_hits == 0
            or (len(str_hits) / float(pnl_hits)) >= _STR_TEXT_VS_PNL_RATIO
        )
    )

    return TextSignals(
        str_marker_hits=len(str_hits),
        pnl_marker_hits=pnl_hits,
        looks_str=looks_str,
        str_markers_matched=str_hits,
    )


# ─────────────────────────── exports ───────────────────────────────────


__all__ = [
    "StructuralSignals",
    "TextSignals",
    "classify_structure",
    "canonical_payload_from_signals",
    "detect_text_signals",
]
