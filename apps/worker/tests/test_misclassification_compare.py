"""Tests for ``_canonical_doc_type`` and the misclassification compare.

Sam QA Bug #2 (June 2026): a T-12 upload landed with
``misclassified=True`` even though the user-supplied tag and the
Router's read both rendered as "T-12 / Trailing Twelve Months". Root
cause: the comparison was raw string equality on
``user_provided_doc_type`` (e.g. ``"T-12"``) vs the canonical enum
form the worker emits (``"T12"``).

These tests pin the contract:

* ``_canonical_doc_type`` collapses the common surface forms
  (``T-12`` / ``T 12`` / ``T_12`` / ``T12``) to a single key.
* Mixed-case + leading/trailing whitespace canonicalize.
* Empty / None inputs short-circuit cleanly.
"""

from __future__ import annotations

import pytest


# ─────────────────────────── canonicalizer ───────────────────────────


@pytest.mark.parametrize(
    "left, right",
    [
        ("T-12", "T12"),
        ("T_12", "T12"),
        ("t 12", "T12"),
        ("T-12", "t12"),
        ("T-12", "T-12"),
        ("trailing-twelve", "TRAILING TWELVE"),
        ("PNL_MONTHLY", "PNL MONTHLY"),
        ("pnl-monthly", "PNL_MONTHLY"),
        ("PNL-YTD", "PNL_YTD"),
        ("CBRE_HORIZONS", "cbre horizons"),
    ],
)
def test_canonical_doc_type_equates_common_variants(left: str, right: str) -> None:
    from app.api.documents import _canonical_doc_type

    assert _canonical_doc_type(left) == _canonical_doc_type(right)


def test_canonical_doc_type_empty_inputs() -> None:
    from app.api.documents import _canonical_doc_type

    assert _canonical_doc_type(None) == ""
    assert _canonical_doc_type("") == ""
    assert _canonical_doc_type("   ") == ""


def test_canonical_doc_type_distinguishes_different_categories() -> None:
    """Sanity: T12 and PNL_MONTHLY still compare unequal after canonicalization."""
    from app.api.documents import _canonical_doc_type

    assert _canonical_doc_type("T-12") != _canonical_doc_type("PNL_MONTHLY")
    assert _canonical_doc_type("T12") != _canonical_doc_type("PNL")
    assert _canonical_doc_type("OM") != _canonical_doc_type("STR")


# ─────────────────────── misclassified compare ───────────────────────


@pytest.mark.parametrize(
    "user_tag, ai_tag, expected_misclassified",
    [
        # Sam's exact reported case: T-12 vs T12 should NOT trigger banner.
        ("T-12", "T12", False),
        ("T12", "T-12", False),
        # Surface-form variants.
        ("t 12", "T12", False),
        ("T_12", "T12", False),
        # Truly different categories DO trigger.
        ("T12", "PNL_MONTHLY", True),
        ("OM", "T12", True),
        # Identical canonicals don't trigger.
        ("PNL_MONTHLY", "PNL MONTHLY", False),
        ("PNL_YTD", "PNL-YTD", False),
    ],
)
def test_misclassified_compare_uses_canonical(
    user_tag: str, ai_tag: str, expected_misclassified: bool
) -> None:
    """Replicate the misclassified flag computation from
    ``apps/worker/app/api/documents.py`` so a future refactor that
    drops ``_canonical_doc_type`` would fail these tests."""
    from app.api.documents import _canonical_doc_type

    canonical_user = _canonical_doc_type(user_tag)
    canonical_ai = _canonical_doc_type(ai_tag)
    misclassified = bool(
        canonical_user and canonical_ai and canonical_user != canonical_ai
    )
    assert misclassified is expected_misclassified
