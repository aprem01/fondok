"""Modeled-value provenance & calculation rationale (FON-25 / FON-27).

Sibling to the assumption ``__sources__`` sidecar (see
``app/services/engine_runner.py``). Where ``__sources__`` records which
``SOURCE_*`` label a given *input assumption* came from, this module records —
for a given *modeled output value* — the formula that produced it, the named
inputs that fed that formula, and, for values read straight from a source
rather than computed, the terminal ``SOURCE_*`` label.

Design goal: let an analyst click any number in the projection and see exactly
where it came from and why. Engines emit a :data:`ProvenanceMap` —
``{output_path: ValueTrace}`` keyed by a dotted path into their typed output
(e.g. ``"years[0].rooms_revenue"``) — *alongside* the output. This keeps the
engine Output schemas flat (no per-scalar provenance pollution) and gives the
UI one uniform structure to read for every value, mirroring exactly how the
existing assumption badge reads ``__sources__``.

An input is one of three things, and the fields below form the complete
chaining vocabulary:

  * an **assumption** — set ``assumption_key`` (the ``__sources__`` key) so the
    UI can chain a modeled value back to its assumption badge, and ``source``
    (a ``SOURCE_*`` label) when known directly;
  * **another computed value** — set ``traces_to`` to that value's dotted path
    in the same map, so the provenance graph is navigable end-to-end;
  * a **leaf constant** — leave all three unset.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─────────────────────────── FON-65 state vocabulary ──────────────────────
#
# A per-value ``state`` tag classifies *how grounded* a modeled value is, so
# the UI can render one consistent badge (document-sourced vs assumption vs
# calculated …) across every engine. It is derived — never hand-authored —
# from data the trace already carries (its formula/inputs, its terminal
# SOURCE_* label, its cross-engine ``traces_to`` edges). See
# :func:`classify_state`.

State = Literal[
    "document_sourced",  # read from the subject's own docs / market comps
    "linked",            # pulled from another engine's canonical value
    "assumption",        # a seed / benchmark / analyst-set assumption
    "calculated",        # computed by a formula over named inputs
    "awaiting_data",     # no value yet — waiting on an upload / input
    "needs_review",      # low-confidence or a conflicting override
]

# Terminal SOURCE_* labels (see engine_runner.SOURCE_*) that mean the value
# was read from a document the analyst uploaded about THIS asset / market.
# ``om_*`` (offering memorandum) and ``str_*`` (STR reports) match by prefix.
_DOCUMENT_LABELS: frozenset[str] = frozenset(
    {"t12_actual", "portfolio_pnl", "om_comps", "om_broker", "pnl_benchmark"}
)
# Labels that mean the value is an assumption / benchmark default rather than
# a document-grounded actual. ``*_default`` matches by suffix.
_ASSUMPTION_LABELS: frozenset[str] = frozenset(
    {"seed", "analyst_override", "cbre_horizons", "deal_row"}
)
# Labels that force a review badge (a flagged conflict / low-confidence read).
_NEEDS_REVIEW_LABELS: frozenset[str] = frozenset(
    {"needs_review", "conflicting_override", "low_confidence"}
)
# Engine names — a ``traces_to`` whose first dotted segment is one of these is
# a cross-engine link (e.g. "expense.years[0].noi"); a bare "years[0].noi" is a
# same-engine reference. Kept local so this module stays import-cycle-free.
_ENGINE_NAMES: frozenset[str] = frozenset(
    {
        "revenue",
        "fb",
        "expense",
        "capital",
        "debt",
        "returns",
        "sensitivity",
        "partnership",
        "cash_flow",
    }
)


class ValueInput(BaseModel):
    """One named input that fed a modeled value's formula."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float
    # When this input is an underwriting assumption, the ``__sources__`` key
    # it maps to — lets the UI chain modeled-value provenance back to the
    # assumption badge (seed vs t12_actual vs analyst_override …).
    assumption_key: str | None = None
    # Terminal provenance label (one of the ``SOURCE_*`` constants) when known
    # directly for this input.
    source: str | None = None
    # Dotted path to another :class:`ValueTrace` in the same map when this
    # input is itself a computed value (e.g. ``"years[0].rooms_revenue"``).
    traces_to: str | None = None


class ValueTrace(BaseModel):
    """Provenance + calculation rationale for one modeled output value."""

    model_config = ConfigDict(extra="forbid")

    value: float
    # Human-readable calculation rationale, e.g.
    # "rooms_revenue = occupied_rooms × ADR". None for directly-sourced values.
    formula: str | None = None
    # The named inputs that fed ``formula``, in evaluation order.
    inputs: list[ValueInput] = Field(default_factory=list)
    # For values read straight from a source (not computed) — the SOURCE_* label.
    source: str | None = None
    # Optional extra rationale: caveats, which branch was taken, assumptions.
    note: str | None = None
    # FON-65 — per-value grounding classification. Optional + defaults to None
    # so every pre-existing trace stays valid; engines set it via
    # :func:`classify_state` (or, for the cash_flow view, directly from a row's
    # linked/calc kind). The UI reads it for a single consistent badge.
    state: State | None = None


def _has_cross_engine_link(trace: ValueTrace) -> bool:
    """True when any input traces to a value in *another* engine's map.

    A ``traces_to`` like ``"expense.years[0].noi"`` (first dotted segment is a
    known engine name) is cross-engine; a bare ``"years[0].noi"`` is not.
    """
    for inp in trace.inputs:
        ref = inp.traces_to
        if not ref or "." not in ref:
            continue
        head = ref.split(".", 1)[0].split("[", 1)[0]
        if head in _ENGINE_NAMES:
            return True
    return False


def classify_state(
    trace: ValueTrace, source_label: str | None = None
) -> State:
    """Derive a :data:`State` for ``trace`` from data it already carries.

    ``source_label`` (when given) is the terminal SOURCE_* label / assumption
    key the caller associates with the value; it falls back to ``trace.source``.
    Precedence, most-specific first:

      1. an explicit conflict / low-confidence label → ``needs_review``;
      2. a document-grounded label (``t12_actual``, ``om_*``, ``str_*``,
         ``portfolio_pnl`` …) → ``document_sourced``;
      3. an assumption / benchmark label (``seed``, ``analyst_override``,
         ``cbre_horizons``, ``*_default``) → ``assumption``;
      4. an input that links to another engine's value → ``linked``;
      5. a value computed by a formula over named inputs → ``calculated``;
      6. no value yet → ``awaiting_data``;
      7. otherwise a bare leaf constant → ``assumption``.
    """
    label = (source_label if source_label is not None else trace.source) or ""
    label = label.strip().lower()

    if label in _NEEDS_REVIEW_LABELS:
        return "needs_review"
    if label and (
        label in _DOCUMENT_LABELS
        or label.startswith("om_")
        or label.startswith("str_")
    ):
        return "document_sourced"
    if label and (label in _ASSUMPTION_LABELS or label.endswith("_default")):
        return "assumption"
    if _has_cross_engine_link(trace):
        return "linked"
    if trace.formula or trace.inputs:
        return "calculated"
    # ``value`` is a required float today; guard for a future optional so a
    # never-populated value reads as awaiting data rather than a bare constant.
    if getattr(trace, "value", None) is None:
        return "awaiting_data"
    return "assumption"


def apply_states(prov: dict[str, ValueTrace]) -> dict[str, ValueTrace]:
    """Set ``state`` on every trace in ``prov`` via :func:`classify_state`.

    Convenience for the engines that emit a provenance sidecar — one call
    tags the whole map in place (and returns it for chaining).
    """
    for trace in prov.values():
        if trace.state is None:
            trace.state = classify_state(trace)
    return prov


# Sidecar map emitted by engines: dotted output path → trace.
# e.g. {"years[0].rooms_revenue": ValueTrace(...)}
ProvenanceMap = dict[str, ValueTrace]


__all__ = [
    "ValueInput",
    "ValueTrace",
    "ProvenanceMap",
    "State",
    "classify_state",
    "apply_states",
]
