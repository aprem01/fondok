"""Canonical grid-cell number coercer (Wave 5 dedup FIX 1).

Before consolidation three extractors hand-rolled DIFFERENT cell→float
parsers: the STR-trend extractor stripped only commas, the CBRE
extractor stripped commas and ``%``, and the sibling learner stripped
``$ , %`` and parsed ``(1,234)`` negatives. The divergence was a real
bug — a cell like ``$(1,234)`` read as ``None`` in the template
extractors (aborting the whole doc to the LLM) but ``-1234.0`` in the
sibling learner. These tests pin the single shared coercer and prove
every caller now routes through it.
"""

from __future__ import annotations

import pytest

from app.extraction import numeric
from app.extraction.numeric import coerce_cell_number
from app.extraction.template_extractors import cbre_horizons, str_trend
from app.services import sibling_template


@pytest.mark.parametrize(
    "cell, expected",
    [
        ("$(1,234)", -1234.0),   # currency + parenthesized negative + commas
        ("(1,234)", -1234.0),    # parenthesized negative
        ("74%", 74.0),           # trailing percent → BARE number
        ("$1,234.56", 1234.56),  # currency + thousands
        ("1,234", 1234.0),       # thousands separator
        ("-1234", -1234.0),      # plain negative
        ("€1.234", 1.234),       # non-USD currency
        ("", None),              # blank
        ("   ", None),           # whitespace only
        ("-", None),             # dash placeholder
        ("n/a", None),           # non-numeric label
        ("2023-01-31 00:00:00", None),  # date header, not a value
    ],
)
def test_coerce_cell_number_union(cell, expected) -> None:
    assert coerce_cell_number(cell) == expected


def test_dollar_paren_negative_is_consistent_across_all_callers() -> None:
    """The exact bug: ``$(1,234)`` must be -1234.0 on EVERY path, never
    ``None`` on one and a number on another."""
    cell = "$(1,234)"
    assert coerce_cell_number(cell) == -1234.0
    # sibling learner
    assert sibling_template._numeric_cell_value(cell) == -1234.0
    # both template extractors import the identical shared function
    assert str_trend.coerce_cell_number is coerce_cell_number
    assert cbre_horizons.coerce_cell_number is coerce_cell_number


def test_percent_is_consistent_across_all_callers() -> None:
    """``74%`` → 74.0 everywhere (ratio scaling is the caller's job)."""
    assert coerce_cell_number("74%") == 74.0
    assert sibling_template._numeric_cell_value("74%") == 74.0
    assert numeric.coerce_cell_number("74%") == 74.0


def test_numeric_typed_inputs_pass_through() -> None:
    assert coerce_cell_number(1234) == 1234.0
    assert coerce_cell_number(-12.5) == -12.5
    assert coerce_cell_number(True) is None  # bool is not a number here
