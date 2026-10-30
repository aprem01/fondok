"""Deal-type vocabulary on the worker (FON-118 / FON-44 hold default).

Mirrors ``apps/web/src/lib/dealTypes.ts`` — the one place the wizard's
``deals.deal_type`` free text is read into a closed vocabulary — so the
runner and any future engine share a single normalisation:

* ``development`` → ground-up development;
* ``adaptive_reuse`` / ``adaptive reuse`` / legacy ``redevelopment`` →
  adaptive reuse (a conversion of an existing building);
* anything else (``None``, unknown) → acquisition, the pre-FON-46 default.

The only behaviour the worker keys off it today is the labelled hold
default (Sam, 2026-10-30: "5 years for acquisitions, 6–7 for development").
"6–7" is resolved as one default per deal type — ground-up carries ~24–30
months of construction plus ramp and stabilises around Year 5, so selling
off two stabilised years lands at Year 7; a conversion typically stabilises
a year earlier → 6. Both are SUGGESTIONS: the runner badges them
``deal_type_default`` and any analyst ``hold_years`` override wins.
"""

from __future__ import annotations

from typing import Literal

DealType = Literal["acquisition", "development", "adaptive_reuse"]

#: Default hold, in years, for the development-like deal types. Acquisitions
#: keep the loader's seed (5) and are deliberately absent here.
DEV_HOLD_DEFAULT: dict[DealType, int] = {
    "development": 7,
    "adaptive_reuse": 6,
}


def normalize_deal_type(raw: object) -> DealType:
    """Read a stored ``deal_type`` into the vocabulary (never raises)."""
    value = raw.strip().lower() if isinstance(raw, str) else ""
    if value == "development":
        return "development"
    if value in ("adaptive_reuse", "adaptive reuse", "redevelopment"):
        return "adaptive_reuse"
    return "acquisition"


def is_development_like(raw: object) -> bool:
    """Development and adaptive reuse share the build / convert configuration."""
    return normalize_deal_type(raw) != "acquisition"


def development_hold_default(raw: object) -> int | None:
    """The labelled hold default for a development-like deal type; ``None``
    for an acquisition (whose hold stays on the loader's seed)."""
    return DEV_HOLD_DEFAULT.get(normalize_deal_type(raw))


__all__ = [
    "DEV_HOLD_DEFAULT",
    "DealType",
    "development_hold_default",
    "is_development_like",
    "normalize_deal_type",
]
