"""What an analyst may write into an extracted field, and the repair of the
display strings the old Data Room editor wrote (W5, 2026-11).

The Data Room's inline review editor pre-filled its input with the DISPLAY
value (``formatValue`` → "$10M") and the worker stored whatever came back:
prod holds eight such strings ("$10M" rooms revenue, "$1.24M" other revenue on
four copies of the Angler's 2024 workbook). No reader can use a display
string, and the precision it rounded away cannot be recovered from it.

* :func:`is_numeric_field` — a field is a figure by what it IS (a numeric
  value, a numeric unit, or a path the registry binds to a numeric concept),
  not by what it holds now.
* :func:`edited_figure` — the number an analyst's correction states, or
  ``None`` when it is not a figure (abbreviations, percent signs, booleans).
* :func:`display_strings` / :func:`repair_display_string_field` — the data
  repair: a stored display string is turned back into a number ONLY when the
  same field record holds the exact numeric value it was displayed from;
  otherwise the value is cleared and the field marked for review. A number is
  never guessed from "$10M".
"""

from __future__ import annotations

import re
from typing import Any

from .numeric import coerce_cell_number

#: Units that make an extracted field a figure (worker + registry vocabulary).
NUMERIC_FIELD_UNITS: frozenset[str] = frozenset(
    {
        "usd", "$", "usd_whole", "dollars", "us$", "pct", "percent", "ratio", "count",
        "keys", "rooms", "index", "years", "x", "usd_per_key", "usd_per_occupied_room",
    }
)
#: Registry concept units that are figures (``date`` / ``text`` are not).
NUMERIC_CONCEPT_UNITS: frozenset[str] = frozenset(
    {"usd", "ratio", "count", "keys", "index", "years", "usd_per_key", "usd_per_occupied_room"}
)
#: Keys on a field record that may hold the value it had before an edit.
PRE_EDIT_VALUE_KEYS: tuple[str, ...] = ("value_before_edit", "original_value", "previous_value")

REPAIR_RULE = "display_string_edit_repair_v1"


def is_numeric_field(field: dict[str, Any]) -> bool:
    v = field.get("value")
    if isinstance(v, int | float) and not isinstance(v, bool):
        return True
    unit = str(field.get("unit") or "").strip().lower()
    if unit in NUMERIC_FIELD_UNITS:
        return True
    if unit:
        return False
    try:
        from ..ontology.registry import concept_for_path, get_registry

        hit = concept_for_path(str(field.get("field_name") or ""))
        if hit is None:
            return False
        meta = get_registry().concepts.get(hit[0])
        return meta is not None and str(meta.unit) in NUMERIC_CONCEPT_UNITS
    except Exception:  # a registry hiccup must not block an analyst's edit
        return False


def edited_figure(value: Any) -> float | None:
    """A number passes; so does a plain written figure (``"9,496,407.22"``,
    ``"$1,234"``, ``"(1,234)"``). Letters or a percent sign — "$10M",
    "83.2%" — are not a figure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str) or re.search(r"[A-Za-z%]", value):
        return None
    return coerce_cell_number(value)


def display_strings(value: float) -> set[str]:
    """Every string the Data Room has displayed ``value`` as (USD): the current
    ``formatValue`` ($X.XXM / $X.XK / $X) and ``fmtCurrency`` compact ($X.XB /
    $X.XM / $XK), and the millions at 0-2 decimals."""
    out: set[str] = set()
    a = abs(value)
    for d in (0, 1, 2):
        out.add(f"${value / 1e6:.{d}f}M")
    out.add(f"${value / 1e9:.2f}B")
    for d in (0, 1):
        out.add(f"${value / 1e3:.{d}f}K")
    out.add(f"${value:.0f}")
    if a >= 1e6:
        out.add(f"${value / 1e6:.2f}M")
    return out


def is_display_string_edit(field: dict[str, Any]) -> bool:
    """A field the old editor broke: an analyst edit on a figure whose value
    is text that is not itself a figure."""
    v = field.get("value")
    return (
        field.get("reviewed") == "edited"
        and isinstance(v, str)
        and v.strip() != ""
        and edited_figure(v) is None
        and is_numeric_field(field)
    )


def repair_display_string_field(field: dict[str, Any]) -> tuple[dict[str, Any], str, Any]:
    """``(repaired field, outcome, new value)`` — outcome ``"restored"`` when
    the field record holds the exact number the string was displayed from,
    else ``"cleared"`` (value NULL, confidence 0, marked for review)."""
    text = str(field["value"]).strip()
    out = dict(field)
    for key in PRE_EDIT_VALUE_KEYS:
        cand = field.get(key)
        if isinstance(cand, int | float) and not isinstance(cand, bool) and text in display_strings(float(cand)):
            out["value"] = float(cand)
            out["note"] = (
                f"Restored {float(cand):,.2f}: an earlier edit saved the display text {text!r}, "
                f"which is how the Data Room showed this stored value."
            )
            return out, "restored", float(cand)
    out["value"] = None
    out["confidence"] = 0.0
    out.pop("reviewed", None)
    out["note"] = (
        f"Needs review: an earlier edit saved the display text {text!r} instead of a figure, "
        "and the figure it rounded cannot be recovered from it. Enter the statement's "
        "figure or re-extract the document."
    )
    return out, "cleared", None


__all__ = [
    "NUMERIC_CONCEPT_UNITS",
    "NUMERIC_FIELD_UNITS",
    "PRE_EDIT_VALUE_KEYS",
    "REPAIR_RULE",
    "display_strings",
    "edited_figure",
    "is_display_string_edit",
    "is_numeric_field",
    "repair_display_string_field",
]
