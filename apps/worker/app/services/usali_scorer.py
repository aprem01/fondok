"""USALI compliance scorer.

Given a flat dict of extracted P&L fields (``{revpar: 245, occupancy: 0.74,
adr: 331, ...}``) and the canonical 66-rule USALI catalog (loaded from
``apps/worker/app/usali_rules.py``), score the document on a 0-100 scale
and surface every deviation as a human-readable message.

Design rules (Wave 1, June 2026 — see ``project_fondok_wave1_decisions.md``):

* **Inconclusive threshold** — fewer than 5 applicable rules → score is
  ``None`` and ``inconclusive=True``. We have too little signal to call
  a percent; the UI shows "Inconclusive" instead of a misleading 100%
  ("4 of 4 rules passed").
* **Market-context-dependent rules** — when a rule needs context the
  deal lacks (e.g. ``INSURANCE_PER_KEY_COASTAL`` requires a coastal
  flag), the rule is excluded from the applicable count and surfaced
  as ``requires_market_context=True`` in the deviations list instead
  of being failed. Coverage-blind rules (everything else) gracefully
  skip when their inputs are missing.
* **Safe evaluator** — formulas come from a CSV that lives next to the
  code; we never run them through ``eval()``. A small AST visitor
  permits constants, the formula field names, ``+ - * /`` arithmetic,
  comparison, ``abs()``, and ``sum()`` over a list literal — and
  nothing else. Any node outside that allowlist raises and the rule
  is skipped (never failed) so an unparseable formula can't poison a
  document's score.

Rule patterns (mapped from ``evals/golden-set/usali-rules.csv``):

* **Math identities** (``CRITICAL``, threshold 0..~0.005-0.01) —
  formula evaluates a *relative drift* that should be near zero, e.g.
  ``abs(revpar - (occupancy * adr)) / revpar``. We pass when the
  drift is ≤ ``threshold_max``.
* **Ratios** (``WARN``/``INFO``) — formula like ``gop / total_revenue``
  whose value must fall in ``[threshold_min, threshold_max]``.
* **Single fields** (``WARN``/``INFO``) — formula is a bare field name
  like ``occupancy``; value must fall in ``[threshold_min, threshold_max]``.

Field-name resolution: the catalog uses canonical names
(``revpar``, ``total_revenue``, ``mgmt_fee``, …); the extraction
payload upstream can carry slightly different dotted paths for the same
line (a namespaced RevPAR, a broker pro-forma's total revenue, …). The
tolerated alternatives per canonical field live in the concept registry
(``app/ontology/concepts.yaml``) as that concept's aliases, so the scorer
doesn't fail valid documents on a path mismatch — see the
"registry-derived alias view" section below.
"""

from __future__ import annotations

import ast
import logging
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..usali_rules import USALIRule, load_usali_rules

logger = logging.getLogger(__name__)


# ─────────────────────────── public dataclasses ───────────────────────────


@dataclass(frozen=True)
class USALIDeviation:
    """One rule's evaluation result.

    Pure data — serializable straight to JSON for the ``usali_deviations``
    column. ``actual_value`` is ``None`` only when ``requires_market_context``
    is True (we couldn't evaluate because the deal lacks context) or when
    a formula short-circuited on a zero denominator.
    """

    rule_id: str
    rule_name: str
    severity: str  # CRITICAL / WARN / INFO
    message: str
    actual_value: float | None
    threshold_min: float | None
    threshold_max: float | None
    requires_market_context: bool = False


@dataclass
class USALIScore:
    """End-to-end result of scoring a P&L extraction.

    * ``score`` is a 0-100 percentage (passed / applicable × 100) OR
      ``None`` when we couldn't evaluate enough rules to call a score.
    * ``deviations`` is a flat list of every rule whose evaluation
      surfaced a finding — out-of-range values, identity violations,
      or market-context placeholders. A rule that PASSED never appears
      here; a rule that was simply skipped for missing inputs never
      appears here either.
    * ``inconclusive`` is True when ``applicable_count < 5``. Mirrors
      the Wave 1 product decision: don't show a percent on weak signal.
    """

    score: float | None
    applicable_count: int
    passed_count: int
    deviations: list[USALIDeviation] = field(default_factory=list)
    inconclusive: bool = False


# ─────────────────────────── inconclusive threshold ───────────────────────────


# Below this many applicable rules a percent score is more misleading
# than helpful ("4 of 4 passed = 100%" tells you nothing). The product
# rule (Wave 1, June 2026) is to surface "Inconclusive" instead.
_INCONCLUSIVE_FLOOR = 5


# ─────────────────────── registry-derived alias view ───────────────────────


# Phase 1.3b — the hand-maintained ``canonical → tolerated paths`` map that
# used to live here is now the concept registry
# (``app/ontology/concepts.yaml``). Every path the scorer used to enumerate is
# an alias on the concept that owns the scorer's canonical name:
#
#   * ``bindings.scorer_key``      — the concept's primary scorer canonical.
#     The registry id follows the ENGINE's key where the two disagree
#     (``property_taxes`` is the id, ``property_tax`` the scorer's name), so
#     this binding is a lookup, never a rename.
#   * ``bindings.scorer_synonyms`` — sibling canonicals that aliased each
#     other in the old map (``dept_expenses`` ↔ ``total_dept_expense``).
#   * ``bindings.scorer_variants`` — the basis-qualified canonicals
#     (``broker_noi`` / ``t12_noi``, ``broker_adr`` / ``t12_adr``, …). These
#     are NOT separate concepts: they are the same line read on a different
#     basis, so the adapter forwards the variant's ``basis`` (and ``scope``)
#     to the resolver instead of carrying a second alias list.
#
# ``_ALIASES`` survives as a read-only VIEW over the registry — same name,
# same ``{canonical: (path, …)}`` shape — because
# ``tests/test_ontology_registry.test_usali_scorer_aliases_round_trip`` and
# any other external reader still index it. Nothing inside this module walks
# it any more: ``_resolve_field`` delegates to ``registry.resolve``, which
# applies the same alias set through its own tiered matcher (tiers 1-5 are
# the old exact-path chase plus the unit-strip and tail rules the web's
# ``findField`` already had; tier 6 is this module's own token resolver,
# opt-in — see ``ontology/DRIFT_NOTES.md`` §5).


def _registry() -> Any:
    """The ontology registry module, imported lazily.

    ``registry.resolve`` reaches back into this module for its token-match
    tier, so the import is deferred to first use rather than taken at import
    time.
    """
    from ..ontology import registry as _reg

    return _reg


@dataclass(frozen=True)
class _ScorerBinding:
    """How one scorer canonical name reads a registry concept.

    ``basis`` / ``want`` are set only for ``scorer_variants`` — the
    basis-qualified names. A plain canonical asks for no particular basis and
    for the document's own annual/TTM total, which is what the old map's
    unqualified paths meant.
    """

    concept: str
    basis: str | None = None
    want: str = "annual"


#: Built once on first use: ``({canonical: _ScorerBinding}, {canonical: paths})``.
_SCORER_INDEX: tuple[dict[str, _ScorerBinding], dict[str, tuple[str, ...]]] | None = None
_SUBORDINATE_NAMESPACES: frozenset[str] | None = None


def _alias_key_order(reg: Any) -> list[str]:
    """Alias-map keys in the order a doc-type-agnostic read walks them.

    Mirrors the resolver's own ordering when no document type is known: the
    ``"*"`` bucket first, then each document type, then the family keys.
    """
    keys: list[str] = ["*"]
    for group in (reg.doc_types, reg.families):
        keys.extend(k for k in group if k not in keys)
    return keys


def _build_scorer_index() -> tuple[dict[str, _ScorerBinding], dict[str, tuple[str, ...]]]:
    reg = _registry().get_registry()
    order = _alias_key_order(reg)
    bindings: dict[str, _ScorerBinding] = {}
    aliases: dict[str, tuple[str, ...]] = {}
    for cid, concept in reg.concepts.items():
        b = concept.bindings
        named: list[tuple[str, _ScorerBinding]] = []
        if b.scorer_key:
            named.append((b.scorer_key, _ScorerBinding(cid)))
        named.extend((syn, _ScorerBinding(cid)) for syn in b.scorer_synonyms)
        named.extend(
            (name, _ScorerBinding(cid, v.basis, v.scope or "annual"))
            for name, v in b.scorer_variants.items()
        )
        if not named:
            continue
        keys = order + [k for k in concept.aliases if k not in order]
        paths: list[str] = []
        for key in keys:
            for alias in concept.aliases.get(key, ()):
                # Wildcard patterns (``historical_performance.{year}.noi``)
                # are matched by the resolver, never by a literal lookup —
                # they are not part of the flat view.
                if "{" in alias.path or alias.path in paths:
                    continue
                paths.append(alias.path)
        for name, binding in named:
            bindings[name] = binding
            aliases[name] = tuple(p for p in paths if p != name)
    return bindings, aliases


def _scorer_index() -> tuple[dict[str, _ScorerBinding], dict[str, tuple[str, ...]]]:
    global _SCORER_INDEX
    if _SCORER_INDEX is None:
        _SCORER_INDEX = _build_scorer_index()
    return _SCORER_INDEX


def _subordinate_namespaces() -> frozenset[str]:
    global _SUBORDINATE_NAMESPACES
    if _SUBORDINATE_NAMESPACES is None:
        _SUBORDINATE_NAMESPACES = frozenset(
            _registry().get_registry().subordinate_namespaces
        )
    return _SUBORDINATE_NAMESPACES


class _AliasView(Mapping):
    """Read-only ``{scorer canonical: (alias path, …)}`` over the registry.

    Lazy: the registry is read on the first mapping operation, so importing
    this module costs nothing extra.
    """

    __slots__ = ()

    def __getitem__(self, key: str) -> tuple[str, ...]:
        return _scorer_index()[1][key]

    def __iter__(self) -> Iterator[str]:
        return iter(_scorer_index()[1])

    def __len__(self) -> int:
        return len(_scorer_index()[1])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_AliasView({_scorer_index()[1]!r})"


#: Canonical field name → tolerated alternative paths, derived from the
#: registry. Kept under the historical name for external readers.
_ALIASES: Mapping[str, tuple[str, ...]] = _AliasView()


# Rule families that require an explicit market-context flag the deal
# may not carry. When the flag is absent we emit a
# ``requires_market_context=True`` placeholder INSTEAD of failing the
# rule, and exclude it from ``applicable_count``.
#
# Each entry is ``rule_id → (context_key, human_phrase)``. The scorer
# probes ``context_key`` on the fields dict (False/None → context
# absent) and on a few common alternate spellings. If absent, the
# placeholder is emitted.
_MARKET_CONTEXT_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "INSURANCE_PER_KEY_COASTAL": (
        ("coastal", "is_coastal", "coastal_market", "market.coastal"),
        "coastal market designation",
    ),
    "MULTI_FIELD_INSURANCE_COASTAL_RISK": (
        ("coastal", "is_coastal", "coastal_market", "market.coastal"),
        "coastal market designation",
    ),
    "MULTI_FIELD_SEASONAL_PATTERN_MISSING": (
        ("seasonal_market", "is_seasonal", "market.seasonal"),
        "seasonal market designation",
    ),
}


# ─────────────────────────── field resolution ───────────────────────────


# Sam QA Bug #3 v3 (June 28 2026) — token-aware fallback resolver
# ─────────────────────────────────────────────────────────────────
#
# Background. v1 expanded the alias map against schema docs. v2 expanded
# it against the saved-fixture prod paths. Both still left Sam's
# day-of-QA upload at "Inconclusive" because the Extractor LLM emits a
# slightly different namespace on every run (``<pnl>.rooms.revenue_usd``
# one day, ``<pnl>.revenues.rooms_usd`` the next, ``<pnl>.rooms_dept.revenue_usd``
# the day after — see the divergence between the T-12 and annual
# fixtures already saved under ``tests/fixtures/real_payloads/``).
#
# A path-by-path alias chase is a losing battle. v3 instead generalizes
# by tokenizing both the canonical name and every flat-payload key on
# ``.``/``_``, expanding canonical tokens through a small synonym map
# (``rooms`` ↔ ``rooms_dept``, ``mgmt`` ↔ ``management``, ``fb`` ↔
# ``food_beverage``, …) and finding the flat key whose token bag is the
# tightest match. Three scoring rules, in order:
#
#   1. Every canonical concept-token must appear in the candidate key's
#      tokens (after synonym expansion). Otherwise the candidate is
#      discarded.
#   2. Discriminator tokens (``revenue`` vs ``expense`` vs ``profit``,
#      ``rooms`` vs ``fb`` vs ``other``) act as REQUIRED filters AND as
#      forbidden filters — a canonical with ``revenue`` rejects keys
#      containing ``expense`` / ``profit``, and vice versa.
#   3. Tie-break: prefer the candidate with the fewest extra tokens,
#      then the shortest path string. Deterministic.
#
# Monthly / page / quarterly / per-month namespaces are dropped entirely
# (they're subordinate slices, never the period total — same exclusion
# logic ``flatten_extraction_fields`` already applies).


# Token synonyms — canonical token → set of acceptable variants. The
# canonical token itself is always a match; the set lists alternate
# spellings the LLM might pick. Conservative — every entry is grounded
# in observed prod payloads or USALI vocabulary.
_TOKEN_SYNONYMS: dict[str, frozenset[str]] = {
    # Departments
    "rooms": frozenset({"rooms", "room"}),
    "fb": frozenset({"fb", "food", "beverage", "fnb"}),
    "other": frozenset({"other", "operated", "miscellaneous", "misc"}),
    "telecom": frozenset({"telecom", "telecommunications", "information"}),
    # Functional groups
    "dept": frozenset({"dept", "departmental", "department"}),
    "undistributed": frozenset({"undistributed", "undist"}),
    "fixed": frozenset({"fixed", "nonoperating", "non"}),
    # Money tokens
    "revenue": frozenset({"revenue", "revenues", "income", "sales"}),
    "expense": frozenset({"expense", "expenses", "cost", "costs"}),
    "profit": frozenset({"profit", "profits", "margin"}),
    # Operations / KPIs
    "occupancy": frozenset({"occupancy", "occ"}),
    "adr": frozenset({"adr"}),
    "revpar": frozenset({"revpar"}),
    # Fees / reserves
    "mgmt": frozenset({"mgmt", "management"}),
    "ffe": frozenset({"ffe", "ff", "replacement", "fixturesandequipment"}),
    "reserve": frozenset({"reserve", "reserves", "replacement"}),
    "fee": frozenset({"fee", "fees"}),
    "incentive": frozenset({"incentive"}),
    "royalty": frozenset({"royalty"}),
    "franchise": frozenset({"franchise"}),
    "marketing": frozenset({"marketing", "sales"}),
    # Charges
    "insurance": frozenset({"insurance"}),
    "property": frozenset({"property"}),
    "tax": frozenset({"tax", "taxes"}),
    "utilities": frozenset({"utilities", "utility"}),
    "labor": frozenset({"labor", "payroll", "wages"}),
    # Roll-ups
    "gop": frozenset({"gop", "grossoperatingprofit"}),
    "noi": frozenset({"noi", "netoperatingincome"}),
    "total": frozenset({"total", "summary", "grand"}),
    # Repairs & maintenance / A&G
    "rm": frozenset({"rm", "repairs", "maintenance", "operations"}),
    "ag": frozenset({"ag", "administrative", "admin", "general"}),
    "resort": frozenset({"resort"}),
    # Variance pairs (broker/t12 references — handled by direct alias).
}


# Each canonical name → ordered list of "concept tokens" it represents
# (after splitting on _, lowercase). Used by the token-match resolver.
# The order is meaningful only for documentation — matching is bag-based.
#
# Concept tokens are STRICT: every entry must appear in the candidate
# key (after synonym expansion). "Discriminator" tokens are listed
# under ``_TOKEN_DISCRIMINATORS`` and disqualify candidates that don't
# carry the right discriminator — e.g. ``rooms_revenue`` rejects keys
# with ``expense`` / ``profit`` even though the rest of the tokens
# match.
def _split_tokens(name: str) -> list[str]:
    """Tokenize a flat-path key like ``pnl.rooms.revenue_usd`` into
    ``['pnl', 'rooms', 'revenue', 'usd']`` — split on ``.`` and ``_``,
    lowercased, empties dropped."""
    return [t for t in name.replace(".", "_").lower().split("_") if t]


# Forbidden-token rules. When the canonical name contains a token from
# the left column, candidate flat keys MUST NOT contain any token from
# the right column — keeps ``rooms_revenue`` from matching
# ``rooms_dept_expense`` just because two tokens overlap.
_TOKEN_FORBIDDEN: dict[str, frozenset[str]] = {
    "revenue": frozenset({"expense", "expenses", "cost", "costs", "profit", "margin"}),
    "expense": frozenset({"revenue", "revenues", "income", "profit", "margin", "sales"}),
    "profit": frozenset({"revenue", "revenues", "income", "expense", "expenses", "cost"}),
    "fee": frozenset({"profit", "margin"}),
    "reserve": frozenset({"profit", "margin"}),
    # GOP / NOI dollar-canonicals must reject margin / pct / ratio
    # candidates. Without this, the token-match v3 fallback prefers
    # `<pnl>.gop.gop_margin_pct` (shorter path, "gop" token appears
    # twice) over `<pnl>.gop.gross_operating_profit_usd`
    # — Sam QA 2026-06-29 saw the broker engine emit
    # "GOP $4.85M → $0" because 0.40 is a valid float and slipped past
    # the dict/list/NaN guard (commit 287f602).
    "gop": frozenset({"margin", "pct", "percent", "percentage", "ratio"}),
    "noi": frozenset({"margin", "pct", "percent", "percentage", "ratio"}),
}


def _expand_with_synonyms(token: str) -> frozenset[str]:
    """Token → its synonym set (always includes the token itself)."""
    syns = _TOKEN_SYNONYMS.get(token)
    if syns is None:
        return frozenset({token})
    return syns


def _has_subordinate_namespace(key: str) -> bool:
    """``True`` for a slice namespace that must never be matched as a period
    total — monthly / quarterly / YTD / per-month / numbered page / weekly /
    daily / MTD / QTD / prior-year / day-of-week, plus month-name segments.

    The namespace list is the registry's ``subordinate_namespaces`` (the union
    of the five hand-maintained lists this used to duplicate — see
    ``ontology/DRIFT_NOTES.md`` §3.6) and the segment test is the registry's
    own, so a path is a slice here exactly when ``registry.resolve`` treats it
    as one. A basis namespace (``.budget.`` / ``.forecast.`` / ``.plan.`` /
    ``.adjusted.``) is a basis, not a slice, and is not excluded here.
    """
    return _registry()._subordinate_scope(key.lower(), _subordinate_namespaces())[0]


# "Soft" concept tokens. When the canonical contains one of these, the
# candidate match doesn't strictly require it — a candidate with the
# right discriminator tokens but no explicit ``expense`` token still
# counts IF the candidate has a money indicator (``usd``/``dollar``/
# ``amount``) instead. The LLM sometimes emits a money line under an
# expense-flavored bucket without repeating the ``expense`` word, e.g.
# ``p_and_l.utilities_usd`` or ``p_and_l.admin_general_usd`` — both
# carry implicit expense semantics.
#
# Soft tokens still participate in the FORBIDDEN check (we still reject
# candidates with ``revenue`` / ``profit``).
_SOFT_CANONICAL_TOKENS: frozenset[str] = frozenset({
    "expense",
    "revenue",
    "fee",
    "reserve",
    "cost",
})

# Money indicator tokens — when a candidate is missing the soft token
# but has one of these, count it as a soft-match. Keeps ``rooms_sold``
# from matching ``rooms_revenue`` (no money indicator).
_MONEY_INDICATOR_TOKENS: frozenset[str] = frozenset({
    "usd",
    "dollar",
    "dollars",
    "amount",
    "value",
})


def _token_match_candidates(
    canonical: str,
    fields: dict[str, Any],
) -> list[tuple[int, int, str, Any]]:
    """Find every flat-key candidate that satisfies the canonical's
    token bag and the forbidden-token rules. Returns
    ``[(extras, len_path, key, value)]`` so a caller can pick the
    tightest match (fewer extras first, then shorter path)."""
    canonical_tokens = _split_tokens(canonical)
    if not canonical_tokens:
        return []
    # Expand each canonical token to its synonym set. We accept the
    # candidate if every HARD concept token has SOME synonym present
    # in the candidate tokens; SOFT concept tokens (expense/revenue/
    # fee/reserve) only enforce the forbidden filter — see
    # ``_SOFT_CANONICAL_TOKENS``.
    hard_tokens: list[str] = [
        t for t in canonical_tokens if t not in _SOFT_CANONICAL_TOKENS
    ]
    soft_tokens: list[str] = [
        t for t in canonical_tokens if t in _SOFT_CANONICAL_TOKENS
    ]
    hard_expanded: list[frozenset[str]] = [
        _expand_with_synonyms(t) for t in hard_tokens
    ]
    if not hard_expanded and not soft_tokens:
        return []
    if not hard_expanded:
        # A canonical made of only soft tokens (e.g. bare ``revenue``) is
        # too ambiguous to safely resolve via tokens.
        return []
    # Build the forbidden bag — tokens that must NOT appear in any
    # candidate (e.g. ``revenue`` forbids ``expense``). Both hard AND
    # soft canonical tokens contribute forbidden tokens.
    forbidden: set[str] = set()
    for t in canonical_tokens:
        forbidden |= _TOKEN_FORBIDDEN.get(t, frozenset())
    # Don't let synonyms of canonical's own tokens count as forbidden
    # (e.g. canonical contains ``revenue`` AND ``income`` is a synonym —
    # we still want it to match keys with ``income``).
    own_token_synonym_bag: set[str] = set()
    for syns in hard_expanded:
        own_token_synonym_bag |= syns
    for t in soft_tokens:
        own_token_synonym_bag |= _expand_with_synonyms(t)
    forbidden -= own_token_synonym_bag

    candidates: list[tuple[int, int, str, Any]] = []
    for key, value in fields.items():
        if value is None:
            continue
        if _has_subordinate_namespace(key):
            continue
        key_tokens = _split_tokens(key)
        if not key_tokens:
            continue
        key_token_set = set(key_tokens)
        # Forbidden filter — reject if any forbidden token present.
        if forbidden & key_token_set:
            continue
        # Required filter — every HARD canonical concept token must
        # have a synonym present in the candidate's token set.
        if not all(syns & key_token_set for syns in hard_expanded):
            continue
        # Soft-token gate — when the canonical has soft tokens
        # (revenue/expense/fee/reserve/cost), the candidate must EITHER
        # carry the soft token (or a synonym) OR carry a money
        # indicator (usd/dollar/amount/value). Without one of those
        # signals the candidate is too ambiguous (e.g. ``rooms_sold``
        # has the ``rooms`` token but no money signal, so it's not a
        # valid match for ``rooms_revenue``).
        soft_bonus = 0
        if soft_tokens:
            soft_hit = False
            money_hit = bool(_MONEY_INDICATOR_TOKENS & key_token_set)
            for t in soft_tokens:
                if _expand_with_synonyms(t) & key_token_set:
                    soft_hit = True
                    soft_bonus -= 2
                    break
            if not soft_hit and not money_hit:
                continue
            if not soft_hit and money_hit:
                soft_bonus -= 1
        # Score:
        #   - prefer candidates whose token set ALSO contains the soft
        #     token (or one of its synonyms) — they're a tighter match
        #     than candidates relying only on the money indicator.
        #     Encoded as a negative bonus in the "extras" tally.
        #   - fewer extra tokens (tokens not contributing to the
        #     match) → tighter.
        #   - shorter path → tighter on ties.
        match_pool = own_token_synonym_bag
        extras = sum(1 for t in key_tokens if t not in match_pool)
        # ``soft_bonus`` is negative when the soft token matches, so it
        # lowers the sort key (better candidate).
        score = extras + soft_bonus
        candidates.append((score, len(key), key, value))
    return candidates


def _resolve_via_tokens(fields: dict[str, Any], canonical: str) -> Any | None:
    """Token-match fallback — returns the value from the tightest
    candidate or ``None`` if nothing matches. Pure; safe to call on
    every rule eval."""
    cands = _token_match_candidates(canonical, fields)
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][3]


# Canonicals that the token resolver MUST NOT try — they're either
# multi-word concept phrases the LLM never emits as a path
# (``broker_noi_yoy_growth_with_flat_opex_ratio``) or context-only
# names whose tokens would over-match the payload (e.g. ``keys`` would
# match every ``rooms_sold_total`` / ``available_rooms`` line).
#
# These names are resolved only via the explicit alias map (or direct
# ``fields[name]`` hit). Listed by exact canonical name.
_TOKEN_RESOLVE_BLOCKLIST: frozenset[str] = frozenset({
    # Single-token names whose token is overly common in P&L paths.
    "keys",
    "monthly_revpar",
    # Multi-word cross-field synthetic checks — these never appear as
    # paths in any extractor flavor; they're computed inputs the
    # critic agent fills in separately.
    "broker_noi_yoy_growth_with_flat_opex_ratio",
    "coastal_insurance_yoy_increase",
    "debt_yield_growth_with_dscr_shrinkage",
    "labor_yoy_growth_vs_market_wage_growth",
    "q1_q3_revpar_swing_in_seasonal_market",
    "revenue_growth_in_flat_demand_market",
    "fb_margin_on_select_service_property",
    "year_one_noi_dip_during_pip",
    # Roll-up totals — the token resolver can't disambiguate
    # "total dept expense" from a single per-dept expense line because
    # both share the ``dept`` + ``expense`` token bag. The registry's
    # ``dept_expenses`` concept already carries the canonical TOTAL
    # paths (the T-12's total-departmental-expense line and the annual
    # P&L's departmental-expense total); when no alias hits, the
    # synthesis sums the per-dept components instead.
    # Listed here so the token resolver doesn't grab a single per-dept
    # line and mis-report it as the rollup.
    "dept_expenses",
    "total_dept_expense",
    "dept_expenses_by_line",
    "undistributed_expenses",
    "fixed_charges",
    # Cross-field math-identity drift fields used by some catalog
    # rules — none ever appear as a literal extractor path.
})


#: Phase 1.3b parity hold-out — ``{scorer canonical: (path suffix, …)}``.
#:
#: The registry's ``fixed_charges`` concept lists the statement's *Total
#: non-operating income & expenses* row ahead of *Total non-operating
#: expenses* (``ontology/DRIFT_NOTES.md`` §3b, "Decision — fixed charges"); the
#: hand-written map this adapter replaces never carried the income-inclusive
#: row at all. On the live Angler's T-12 the two rows differ by the
#: non-operating income line — 1,922,240 vs 1,699,740 — and adopting the
#: registry's answer flips ``NOI_IDENTITY`` from fail to pass on every T-12
#: fixture. That is a real scoring change, and this phase is score-parity
#: only, so the row is held out here and the case is recorded under
#: "Phase 1.3b parity exceptions" in ``DRIFT_NOTES.md``. Held out by path
#: SUFFIX so the hold-out is namespace-agnostic.
_PARITY_HOLD_OUT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "fixed_charges": ("total_non_operating_income_and_expenses_usd",),
}


def _resolve_field(fields: dict[str, Any], canonical: str) -> Any | None:
    """Look up ``canonical`` on ``fields`` through the concept registry.

    Resolution order (first non-``None`` wins):

    1. Direct hit: ``fields[canonical]``.
    2. ``registry.resolve`` on the concept that owns ``canonical``. Its tiers
       1-3 are the exact-path chase the old ``_ALIASES`` walk did (same paths,
       now declared once in ``concepts.yaml``); tiers 4-5 add the unit-suffix
       strip and the guarded tail match; tier 6 is this module's own v3
       token-match resolver, opted into per call.
    3. For a name no concept owns — the synthetic cross-field checks the
       critic fills in separately — the v3 token resolver directly, so the
       behaviour of an unbound name is unchanged.

    Step 1 is deliberately kept ahead of the registry: the flat dict's own
    canonical key is what the structural-recogniser pre-pass, the roll-up
    synthesis and ``extra_context`` (deal-level ``keys`` / ``purchase_price`` /
    ``coastal``) write, and those must beat any extracted path.

    ``_TOKEN_RESOLVE_BLOCKLIST`` is now expressed as "the caller does not opt
    in": token matching is off by default in the resolver, and this adapter
    turns it on for every canonical except the blocklisted ones
    (``ontology/DRIFT_NOTES.md`` §3.16).

    Basis-qualified canonicals (``broker_noi`` / ``t12_adr`` / …) are the same
    concept as their unqualified sibling, so the binding's ``basis`` (and
    ``scope``) is forwarded as a resolver FILTER — that is what keeps
    ``t12_adr`` from resolving to a broker proforma ADR and vice versa.

    Tolerates dotted extractor paths at the top level of ``fields`` — they're
    stored as flat keys with the dot baked in, not as nested dicts.

    Returns the value (caller numeric-coerces; the resolver already coerces
    numeric concepts). ``None`` only when nothing resolved.
    """
    val = fields.get(canonical)
    if val is not None:
        return val
    binding = _scorer_index()[0].get(canonical)
    allow_tokens = canonical not in _TOKEN_RESOLVE_BLOCKLIST
    if binding is None:
        # No concept owns this name (the synthetic cross-field checks).
        return _resolve_via_tokens(fields, canonical) if allow_tokens else None
    held = _PARITY_HOLD_OUT_SUFFIXES.get(canonical)
    if held:
        fields = {k: v for k, v in fields.items() if not k.lower().endswith(held)}
    return _registry().resolve(
        fields,
        binding.concept,
        want=binding.want,
        basis=binding.basis,
        allow_token_match=allow_tokens,
    ).value


def _coerce_number(v: Any) -> float | None:
    """Best-effort numeric coerce — booleans rejected, strings stripped of
    common formatting (``$``, ``,``, trailing ``%``)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        return f / 100.0 if pct else f
    return None


# ─────────────────────────── safe evaluator ───────────────────────────


class _UnsupportedFormulaError(Exception):
    """Raised when a formula uses an AST node outside the allowlist."""


class _MissingFieldError(Exception):
    """Raised when a formula references a field name that doesn't resolve.

    Treated as "rule not applicable" by the caller (skip, don't fail).
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


# AST node types that are safe to interpret. Everything else (Attribute,
# Subscript, Lambda, Comprehension, Import, ...) raises. ``Index`` is
# pre-3.9 — we don't include it; AST nodes for ``a[0]`` are Subscript
# which we don't allow anyway.
_ALLOWED_BINOPS: tuple[type[ast.AST], ...] = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
)
_ALLOWED_UNARYOPS: tuple[type[ast.AST], ...] = (ast.UAdd, ast.USub)
_ALLOWED_CALLS: frozenset[str] = frozenset({"abs", "sum", "min", "max"})


def _evaluate(node: ast.AST, fields: dict[str, Any]) -> float:
    """Recursively interpret an AST node against the fields dict.

    Returns a float. Raises ``_MissingFieldError`` when a referenced
    field is absent (caller treats that as "skip rule"). Raises
    ``_UnsupportedFormulaError`` for any disallowed AST shape.
    """
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, fields)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise _UnsupportedFormulaError(
            f"constant {node.value!r} is not numeric"
        )
    if isinstance(node, ast.Name):
        val = _resolve_field(fields, node.id)
        num = _coerce_number(val)
        if num is None:
            raise _MissingFieldError(node.id)
        return num
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise _UnsupportedFormulaError(f"unary op {type(node.op).__name__}")
        operand = _evaluate(node.operand, fields)
        return +operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise _UnsupportedFormulaError(f"binop {type(node.op).__name__}")
        left = _evaluate(node.left, fields)
        right = _evaluate(node.right, fields)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                # Treat zero-denominator as "not applicable" rather than
                # ZeroDivisionError. The most common case is a brand-new
                # property with no revenue yet — failing every ratio rule
                # on it would be misleading.
                raise _MissingFieldError("<zero-denominator>")
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise _MissingFieldError("<zero-denominator>")
            return left // right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise _MissingFieldError("<zero-denominator>")
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise _UnsupportedFormulaError(f"binop {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        # Only bare-name calls to a tiny allowlist.
        if not isinstance(node.func, ast.Name):
            raise _UnsupportedFormulaError("call to non-name")
        fname = node.func.id
        if fname not in _ALLOWED_CALLS:
            raise _UnsupportedFormulaError(f"call to {fname!r}")
        if node.keywords:
            raise _UnsupportedFormulaError(f"{fname}() with kwargs")
        args = [_evaluate(a, fields) for a in node.args]
        if fname == "abs":
            if len(args) != 1:
                raise _UnsupportedFormulaError("abs() takes 1 arg")
            return abs(args[0])
        if fname == "sum":
            return float(sum(args)) if args else 0.0
        if fname == "min":
            if not args:
                raise _UnsupportedFormulaError("min() requires args")
            return float(min(args))
        if fname == "max":
            if not args:
                raise _UnsupportedFormulaError("max() requires args")
            return float(max(args))
    if isinstance(node, ast.Compare):
        # Not used by the current catalog but harmless to support.
        left = _evaluate(node.left, fields)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comp, fields)
            ok: bool
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            else:
                raise _UnsupportedFormulaError(f"compare op {type(op).__name__}")
            if not ok:
                return 0.0
            left = right
        return 1.0
    raise _UnsupportedFormulaError(type(node).__name__)


# ─────────────────────────── rule classification ───────────────────────────


def _is_math_identity(rule: USALIRule) -> bool:
    """Math identities use threshold_min=0 and a tight threshold_max
    (~0.005-0.01) on a *relative drift* expression like ``abs(... )/...``.

    We detect by category=='Math' or by the formula starting with
    ``abs(`` and the threshold_min being 0. The "cross_field" category
    also carries identities (``MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY``).
    """
    if (rule.category or "").lower() in ("math", "variance", "cross_field"):
        # Some variance rules ARE drifts (abs(a-b)/b). Confirm via formula.
        if rule.formula_or_check.strip().startswith("abs("):
            return True
        # Pure variance like "abs(broker_occupancy - t12_occupancy)" is
        # also an identity-style check (drift should be ≤ max).
        if (
            rule.threshold_min in (0, 0.0)
            and rule.threshold_max is not None
            and rule.threshold_max <= 0.5
        ):
            return True
    return False


def _has_market_context_dependency(
    rule: USALIRule, fields: dict[str, Any]
) -> tuple[bool, str]:
    """Return ``(needs_context, missing_phrase)``.

    ``needs_context`` is True when the rule is in
    ``_MARKET_CONTEXT_RULES`` AND the deal payload doesn't carry the
    flag the rule needs. ``missing_phrase`` is a short human label for
    the deviation message.
    """
    cfg = _MARKET_CONTEXT_RULES.get(rule.rule_id)
    if cfg is None:
        return False, ""
    context_keys, phrase = cfg
    for key in context_keys:
        v = fields.get(key)
        if isinstance(v, bool):
            if v:
                return False, ""
        elif v not in (None, "", 0):
            return False, ""
    return True, phrase


# ─────────────────────────── deviation messages ───────────────────────────


def _fmt_pct(v: float) -> str:
    """``0.082 → "8.2%"``. Used when the formula yields a ratio."""
    return f"{v * 100:.1f}%"


def _looks_like_ratio(rule: USALIRule) -> bool:
    """A ratio rule's formula contains ``/`` AND its threshold range sits
    within ``(0, 5)`` — wide enough to admit margins (0.10..0.45) and
    fee ratios (0.02..0.06) without catching dollar-denominated checks
    like ``insurance_expense / keys`` (which is 500..2500).
    """
    if "/" not in rule.formula_or_check:
        return False
    return (
        rule.threshold_max is not None
        and rule.threshold_max <= 5.0
    )


def _format_message(
    rule: USALIRule,
    actual: float | None,
    is_identity: bool,
) -> str:
    """Build a human-readable deviation message.

    Identity violations report the relative drift in percent; ratio
    rules report the actual value as a percent; everything else reports
    the raw number. The threshold range is always echoed back so the
    UI can show the analyst exactly what would have passed.
    """
    name = rule.name
    if actual is None:
        return f"{name}: could not evaluate (missing inputs)."
    if is_identity:
        return (
            f"{name}: drift of {_fmt_pct(actual)} exceeds "
            f"{_fmt_pct(rule.threshold_max or 0.005)} tolerance "
            f"— the reported number doesn't reconcile."
        )
    if _looks_like_ratio(rule):
        lo = _fmt_pct(rule.threshold_min) if rule.threshold_min is not None else "?"
        hi = _fmt_pct(rule.threshold_max) if rule.threshold_max is not None else "?"
        return (
            f"{name}: {_fmt_pct(actual)} falls outside typical {lo}-{hi} range."
        )
    lo = (
        f"{rule.threshold_min:g}"
        if rule.threshold_min is not None
        else "?"
    )
    hi = (
        f"{rule.threshold_max:g}"
        if rule.threshold_max is not None
        else "?"
    )
    return f"{name}: {actual:g} falls outside typical {lo}-{hi} range."


# ─────────────────────────── core scoring ───────────────────────────


def score_extraction(
    fields: dict[str, Any],
    *,
    rules: list[USALIRule] | None = None,
) -> USALIScore:
    """Score a P&L extraction against the USALI catalog.

    Args:
        fields: flat ``{name: value}`` dict of extracted fields. Keys
            may be canonical (``revpar``) or any of the alternate
            paths the registry lists for that concept (a namespaced
            ``revpar_usd``, say). Numeric strings ("$185.40", "74%")
            are coerced.
        rules: optional override of the rule catalog — defaults to
            ``load_usali_rules()`` (the canonical 66-rule CSV).

    Returns: a ``USALIScore`` whose ``score`` is ``None`` (inconclusive)
    when fewer than ``_INCONCLUSIVE_FLOOR=5`` rules were applicable,
    else a 0-100 percentage.
    """
    rules = rules if rules is not None else load_usali_rules()
    fields = fields or {}

    applicable = 0
    passed = 0
    deviations: list[USALIDeviation] = []

    for rule in rules:
        formula = (rule.formula_or_check or "").strip()
        if not formula:
            continue

        # Market-context guard: rules that explicitly need a deal-level
        # flag the payload may not carry get parked instead of failed.
        needs_ctx, missing_phrase = _has_market_context_dependency(rule, fields)
        if needs_ctx:
            deviations.append(
                USALIDeviation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity or "INFO",
                    message=(
                        f"{rule.name}: requires {missing_phrase} — "
                        "evaluate once deal context is provided."
                    ),
                    actual_value=None,
                    threshold_min=rule.threshold_min,
                    threshold_max=rule.threshold_max,
                    requires_market_context=True,
                )
            )
            continue

        try:
            tree = ast.parse(formula, mode="eval")
        except SyntaxError:
            logger.debug(
                "usali_scorer: rule %s has unparseable formula %r — skipping",
                rule.rule_id,
                formula,
            )
            continue

        try:
            value = _evaluate(tree, fields)
        except _MissingFieldError as exc:
            # Missing inputs ⇒ not applicable; don't penalize.
            # Debug-log the canonical name that didn't resolve so a
            # future QA cycle can see exactly which alias / token
            # match needs widening.
            logger.debug(
                "usali_scorer: rule %s skipped — %s not resolved on payload",
                rule.rule_id,
                exc.name,
            )
            continue
        except _UnsupportedFormulaError as exc:
            # Catalog rule references an AST shape we don't model — skip
            # rather than crash. Logged so a future catalog change is
            # observable.
            logger.debug(
                "usali_scorer: rule %s uses unsupported formula %r (%s) — skipping",
                rule.rule_id,
                formula,
                exc,
            )
            continue
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning(
                "usali_scorer: unexpected eval error on rule %s: %s",
                rule.rule_id,
                exc,
            )
            continue

        applicable += 1
        is_identity = _is_math_identity(rule)

        # Identity rules pass when |drift| ≤ threshold_max.
        if is_identity:
            tol = (
                rule.threshold_max
                if rule.threshold_max is not None
                else 0.005
            )
            ok = abs(value) <= tol + 1e-12
        else:
            lo = rule.threshold_min
            hi = rule.threshold_max
            ok = True
            if lo is not None and value < lo - 1e-12:
                ok = False
            if hi is not None and value > hi + 1e-12:
                ok = False

        if ok:
            passed += 1
            continue

        deviations.append(
            USALIDeviation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=rule.severity or "INFO",
                message=_format_message(rule, value, is_identity),
                actual_value=value,
                threshold_min=rule.threshold_min,
                threshold_max=rule.threshold_max,
                requires_market_context=False,
            )
        )

    inconclusive = applicable < _INCONCLUSIVE_FLOOR
    score: float | None
    if inconclusive or applicable == 0:
        score = None
    else:
        score = round(100.0 * passed / applicable, 2)

    return USALIScore(
        score=score,
        applicable_count=applicable,
        passed_count=passed,
        deviations=deviations,
        inconclusive=inconclusive,
    )


# ─────────────────────────── JSONB serializer ───────────────────────────


def deviations_to_jsonb(score: USALIScore) -> dict[str, Any]:
    """Turn a ``USALIScore`` into the JSONB shape we persist on the
    documents row.

    The persisted shape includes ``inconclusive`` and the applicable
    counts so the UI doesn't need a second query to render an
    "Inconclusive (3 of 4 rules)" badge.
    """
    return {
        "inconclusive": score.inconclusive,
        "applicable_count": score.applicable_count,
        "passed_count": score.passed_count,
        "deviations": [
            {
                "rule_id": d.rule_id,
                "rule_name": d.rule_name,
                "severity": d.severity,
                "message": d.message,
                "actual_value": d.actual_value,
                "threshold_min": d.threshold_min,
                "threshold_max": d.threshold_max,
                "requires_market_context": d.requires_market_context,
            }
            for d in score.deviations
        ],
    }


# ─────────────────────────── extraction-payload adapter ───────────────────────────


def flatten_extraction_fields(
    fields: list[dict[str, Any]],
    *,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert the extractor's list-of-records into a flat
    ``{name: value}`` dict the scorer can read.

    The extractor emits ``[{"field_name": "<pnl>.revpar_usd",
    "value": 137.2, ...}, ...]``. We also strip a few common path
    prefixes so a namespaced ``revpar_usd`` works the same as a bare
    ``revpar`` — both end up as ``revpar`` in the resolver's lookup
    chain (we keep both forms so direct alias hits still work).

    ``extra_context`` is merged in last and wins over extractor values
    when both are present — it carries deal-level fields like
    ``coastal``, ``keys`` from the deals row, ``purchase_price``, etc.

    Sam QA Bug #3 v4 (June 28 2026) — structural recognizer pre-pass:
    BEFORE the path-flattening + alias map + token resolver chain runs,
    we ask the structural recognizer (``services/structural_recognizer``)
    to walk the raw payload and surface every canonical USALI line item
    it can find via regex-on-key-names at any nesting depth. The
    recognizer's surfaced canonical values are written to the flat dict
    under their canonical names (``rooms_revenue``, ``property_tax``,
    ``gop``, …) before any other key. This is the v4 fix: the rule
    catalog's formulas reference canonical names directly — once the
    recognizer has populated them, the resolver chain becomes a
    fallback, not the main load-bearing path. The v3 token resolver is
    still wired in below to backstop any concept the structural
    recognizer's pattern catalog hasn't memorialized yet (defensive).
    """
    flat: dict[str, Any] = {}

    # ── v4 structural-recognizer pre-pass ──
    #
    # Pull every recognizable canonical line item out of the raw
    # payload BEFORE the path-flattening pass. The recognizer doesn't
    # care which namespace the LLM picked — it matches regex on key
    # names at every depth — so even when the LLM ships a freshly
    # invented namespace (e.g. ``hotel_revenues.rooms_segment.gross``)
    # the canonical concept gets surfaced. Writing the canonical
    # values FIRST means the rule catalog's formulas resolve
    # immediately without going through the alias map / token
    # resolver — the v3 chain becomes a backstop instead of the
    # main load-bearing path. Import-locally so callers that skip
    # ``flatten_extraction_fields`` don't pay the import cost.
    try:
        from .structural_recognizer import classify_structure

        signals = classify_structure(fields)
        for cname, cval in signals.canonical_values.items():
            flat[cname] = cval
    except Exception as exc:  # noqa: BLE001 - defensive
        # Recognizer failure is never a gate — the v1/v2/v3 chain still
        # runs below. Logged so a future schema change is observable.
        logger.debug("usali_scorer: structural recognizer failed: %s", exc)

    for f in fields or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip()
        if not name:
            continue
        value = f.get("value")
        if value is None:
            continue
        # Raw extractor paths (e.g. ``<pnl>.rooms.revenue_usd``)
        # are written under their literal name — the recognizer wrote
        # under the canonical name (``rooms_revenue``), so the two don't
        # collide. The dotted-path key is still needed for direct hits
        # the alias map enumerates AND for the v3 token resolver.
        flat[name] = value
        # Also expose the last path component so a payload using
        # ``<pnl>.revpar_usd`` becomes resolvable under ``revpar_usd``
        # (which the registry already lists as an alias of the
        # ``revpar`` concept).
        #
        # Sam QA Bug #3 v2: SKIP the tail-write for monthly / per-page
        # records. The real prod T-12 ships dozens of
        # ``<pnl>.monthly.jan_2025.rooms_revenue_usd`` entries
        # — tail-writing them clobbers ``rooms_revenue_usd`` with a
        # single-month figure, which then leaks through the alias map
        # and lands as the per-period ``rooms_revenue`` (1M instead of
        # the 9M actual TTM total). The monthly/page namespaces are
        # subordinate slices, never the period total.
        if "." in name:
            lowered = name.lower()
            if (
                ".monthly." in lowered
                or ".page" in lowered  # ``.page5.`` is a real prod alias
                or ".per_month." in lowered
            ):
                continue
            tail = name.rsplit(".", 1)[-1]
            # First write wins so a direct flat hit (e.g. "revpar") on
            # a later record doesn't clobber an earlier one.
            flat.setdefault(tail, value)
    if extra_context:
        for k, v in extra_context.items():
            if v is None:
                continue
            flat[k] = v

    # ─── Derive USALI roll-ups from line items ───
    #
    # Sam QA Bug #3 (June 2026): real T-12s (200+ extracted fields)
    # were scoring "Inconclusive — too few applicable rules" because
    # the catalog rules reference ``total_revenue``, ``gop``,
    # ``dept_expenses``, ``undistributed_expenses``, ``fixed_charges``,
    # and the dept-profit margins — fields the extractor does NOT
    # emit directly (it emits per-line items per
    # ``extraction_schemas/t12.md``). Synthesize them here so the
    # scorer can evaluate margin / ratio / identity rules. Each
    # derived field uses ``setdefault`` so a direct extractor emission
    # always wins.
    _derive_usali_rollups(flat)
    return flat


def _coerce_for_sum(v: Any) -> float | None:
    """Numeric coerce for the roll-up derivations (rejects booleans /
    NaN). Kept private + duplicated from ``_coerce_number`` so it's
    easy to inline in the hot path without import gymnastics."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        return f / 100.0 if pct else f
    return None


def _derive_usali_rollups(flat: dict[str, Any]) -> None:
    """Compute roll-up totals from extractor line items, in place.

    Called from ``flatten_extraction_fields`` AFTER the per-record
    flatten pass + tail-write so every derivation sees the full
    component set. Each write is ``setdefault`` so an extractor-emitted
    canonical value (rare but possible — e.g. a synthesized T-12
    workbook with a "Total Revenue" row) always wins.
    """

    def _get(*keys: str) -> float | None:
        for k in keys:
            v = _coerce_for_sum(flat.get(k))
            if v is not None:
                return v
        return None

    # ── Resolver helper that uses the FULL resolution chain ──
    #
    # ``_get`` above only walks a literal keys list. For the roll-up
    # synthesis we want to honor every alias for a canonical name AND
    # the v3 token-match fallback — otherwise the synthesis can return
    # None on a payload shape the explicit alias map doesn't cover but
    # the token resolver does. ``_via_alias`` runs the canonical →
    # aliases chain in ``_ALIASES`` and then the token resolver.
    def _via_alias(canonical: str) -> float | None:
        return _coerce_for_sum(_resolve_field(flat, canonical))

    # ``_setdefault_synth`` is the synthesis-aware setter: we only write
    # the synthesized value if the canonical name does NOT already
    # resolve through any path (direct hit / explicit alias / token
    # match). Otherwise the synthesis would mask the LLM's actual
    # emission on a non-canonical path (e.g. ``p_and_l.gop_usd`` ⇒
    # token-resolves to ``gop``; the synthesis must defer).
    #
    # NOTE: We compare against the value-presence on the canonical key
    # itself when writing — once we write it, ``flat[canonical]``
    # exists; future synthesis steps that read via ``_via_alias`` will
    # pick up the cached canonical value (so the call order matters).
    def _setdefault_synth(canonical: str, value: float | int) -> None:
        if canonical in flat:
            return
        # Check the full resolution chain: if the canonical resolves via
        # any path, don't overwrite with the synthesis. We DO still
        # write the canonical key (cached) so downstream synthesis
        # steps don't have to re-traverse the chain.
        existing = _resolve_field(flat, canonical)
        if existing is not None:
            flat[canonical] = existing
            return
        flat[canonical] = value

    # total_revenue = rooms_revenue + fb_revenue + other_revenue
    #                 + resort_fees + misc_revenue
    rooms_rev = _via_alias("rooms_revenue")
    fb_rev = _via_alias("fb_revenue")
    other_rev = _via_alias("other_revenue")
    resort_fees = _via_alias("resort_fees") or 0.0
    misc_rev = _via_alias("misc_revenue") or 0.0
    components = [v for v in (rooms_rev, fb_rev, other_rev) if v is not None]
    if len(components) >= 2:
        # We need at least two components to call a synthesized total
        # meaningful. Resort fees + misc add on when present.
        _setdefault_synth(
            "total_revenue",
            sum(components) + resort_fees + misc_rev,
        )

    # dept_expenses = rooms_dept_expense + fb_dept_expense + other_dept_expense
    rooms_dept_exp = _via_alias("rooms_dept_expense")
    fb_dept_exp = _via_alias("fb_dept_expense")
    other_dept_exp = _via_alias("other_dept_expense") or 0.0
    dept_parts = [v for v in (rooms_dept_exp, fb_dept_exp) if v is not None]
    if dept_parts:
        total_dept = sum(dept_parts) + other_dept_exp
        _setdefault_synth("dept_expenses", total_dept)
        _setdefault_synth("total_dept_expense", total_dept)
        # dept_expenses_by_line is list-typed; never extractor-emitted —
        # safe to write directly with setdefault semantics.
        flat.setdefault(
            "dept_expenses_by_line",
            [v for v in (rooms_dept_exp, fb_dept_exp, other_dept_exp)
             if v is not None and v != 0],
        )

    # undistributed_expenses = sum of the five undistributed lines
    a_g = _via_alias("ag_expense")
    it = _via_alias("information_telecom")
    sm = _via_alias("marketing_expense")
    prop_ops = _via_alias("rm_expense")
    utilities = _via_alias("utilities_expense")
    undist_parts = [v for v in (a_g, it, sm, prop_ops, utilities) if v is not None]
    if len(undist_parts) >= 2:
        _setdefault_synth("undistributed_expenses", sum(undist_parts))

    # fixed_charges = property_taxes + insurance (+ ground rent etc.)
    prop_tax = _via_alias("property_tax")
    insurance = _via_alias("insurance_expense")
    fixed_parts = [v for v in (prop_tax, insurance) if v is not None]
    if fixed_parts:
        _setdefault_synth("fixed_charges", sum(fixed_parts))

    # gop = total_revenue - dept_expenses - undistributed_expenses.
    # We honor a direct GOP emission first (real prod ships a
    # gross-operating-profit dollar line, flat or under a ``.total_usd``
    # sibling); the synthesis only fires when it isn't directly emitted.
    tr = _via_alias("total_revenue")
    de = _via_alias("dept_expenses") or _via_alias("total_dept_expense")
    ue = _via_alias("undistributed_expenses")
    if tr is not None and de is not None and ue is not None:
        _setdefault_synth("gop", tr - de - ue)

    # noi can be back-derived if gop + mgmt_fee + ffe_reserve + fixed_charges are known.
    gop_val = _via_alias("gop")
    mgmt_fee = _via_alias("mgmt_fee")
    ffe = _via_alias("ffe_reserve")
    fixed = _via_alias("fixed_charges")
    if (
        gop_val is not None
        and mgmt_fee is not None
        and ffe is not None
        and fixed is not None
    ):
        _setdefault_synth("noi", gop_val - mgmt_fee - ffe - fixed)

    # Department profits — needed for ROOMS_DEPT_MARGIN_* and FB_DEPT_MARGIN_*.
    if rooms_rev is not None and rooms_dept_exp is not None:
        _setdefault_synth("rooms_dept_profit", rooms_rev - rooms_dept_exp)
    if fb_rev is not None and fb_dept_exp is not None:
        _setdefault_synth("fb_dept_profit", fb_rev - fb_dept_exp)

    # Total labor — needed for the LABOR_PCT_REVENUE_* range rules.
    # Real prod doesn't emit a labor line directly (it's embedded in
    # the per-dept expenses), so the rule will skip when missing —
    # that's correct behavior. Kept as a placeholder synthesis in case
    # a future extractor flavor ships a ``total_labor_usd`` line.
    total_labor = _via_alias("total_labor")
    if total_labor is not None:
        _setdefault_synth("total_labor", total_labor)


__all__ = [
    "USALIDeviation",
    "USALIScore",
    "deviations_to_jsonb",
    "flatten_extraction_fields",
    "score_extraction",
]
