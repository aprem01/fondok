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

from pydantic import BaseModel, ConfigDict, Field


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


# Sidecar map emitted by engines: dotted output path → trace.
# e.g. {"years[0].rooms_revenue": ValueTrace(...)}
ProvenanceMap = dict[str, ValueTrace]


__all__ = ["ValueInput", "ValueTrace", "ProvenanceMap"]
