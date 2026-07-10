"""Canonical grid-cell → number coercion for the extraction pipeline.

WHY THIS LIVES HERE
-------------------
Three deterministic extractors used to hand-roll their own cell→float
parsers and they DISAGREED:

* ``template_extractors/str_trend.py`` stripped only commas;
* ``template_extractors/cbre_horizons.py`` stripped commas and ``%``;
* ``services/sibling_template.py`` stripped ``$``, ``,``, ``%`` and
  parsed ``(1,234)`` parenthesized negatives.

The divergence was a real correctness bug: a cell like ``$(1,234)`` read
as ``None`` in the template extractors (which then abort the *whole*
document to the LLM) but as ``-1234.0`` in the sibling learner — the
same workbook got numbers via one path and a full LLM fallback via
another. This module is the single source of truth those callers now
share, so every path reads the same cell the same way.

The coercer is a strict SUPERSET of the three former parsers (it parses
MORE, never less): anything the old template coercers accepted, this
still accepts, so no "any anchor fails → None → LLM fallback" contract
is weakened. A cell that a stricter parser used to reject (``74%``,
``$1,234``, ``(1,234)``) now parses to a number — which is strictly
more correct, not a behavior regression.

NOT TO BE CONFUSED WITH the two OLDER coercers that intentionally live
next to their own call sites and stay out of scope here:
``usali_scorer._coerce_number`` and
``structural_recognizer._coerce_to_float``. Those serve different
concerns (USALI scoring / structural recognition), so this shared
extraction-cell coercer is deliberately a separate, third thing rather
than a sixth divergent copy.
"""

from __future__ import annotations

import re

# Full ``YYYY-MM-DD`` (optionally with a midnight timestamp) date cells
# are month/period HEADERS, not values — the sibling learner relied on
# this. A plain float() would already reject them (the separators make
# them non-numeric), but matching explicitly keeps the intent obvious.
_DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]00:00:00)?$")

# Currency symbols stripped before parsing (union of the old coercers'
# ``$`` plus the common non-USD marks a data room can carry).
_CURRENCY = str.maketrans("", "", "$€£¥")


def coerce_cell_number(value: object) -> float | None:
    """Parse a spreadsheet cell into a float, or ``None``.

    Handles (the UNION of the three former hand-rolled coercers):

    * plain floats/ints already typed as numbers;
    * currency symbols ``$ € £ ¥`` and thousands ``,`` separators;
    * a trailing/leading ``%`` — the BARE number is returned (``74%`` →
      ``74.0``); callers that need 0..1 ratio scaling do that division
      themselves, matching the current sibling-template behavior;
    * parenthesized negatives ``(1,234)`` → ``-1234.0``;
    * surrounding whitespace.

    Returns ``None`` for blanks, dates, dashes and anything else that
    is not numeric.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return None
    if _DATE_RE.match(s):
        return None

    # Strip currency / thousands / percent FIRST so parenthesized
    # negatives are detected even when a currency symbol sits outside
    # the parens (``$(1,234)`` → ``(1234)`` → -1234.0).
    s = s.translate(_CURRENCY).replace(",", "").replace("%", "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


__all__ = ["coerce_cell_number"]
