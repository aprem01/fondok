"""Tests for ``_canonical_doc_type`` and the misclassification compare.

Sam QA Bug #2 v1 (June 2026): a T-12 upload landed with
``misclassified=True`` even though the user-supplied tag and the
Router's read both rendered as "T-12 / Trailing Twelve Months". Root
cause: the comparison was raw string equality on
``user_provided_doc_type`` (e.g. ``"T-12"``) vs the canonical enum
form the worker emits (``"T12"``).

Sam QA Bug #2 v2 (June 2026): even after v1 fixed the canonical
comparison, the banner STILL rendered "T-12 vs T-12" because the
banner read both sides from ``doc.doc_type`` — and the worker
INTENTIONALLY keeps ``doc_type`` equal to the user tag when
``misclassified=true``. v2 adds an ``ai_proposed_doc_type`` column
the worker writes the Router's read to; the banner reads it for the
AI label. Pre-v2 legacy data (no column) renders nothing.

These tests pin the contract:

* ``_canonical_doc_type`` collapses the common surface forms
  (``T-12`` / ``T 12`` / ``T_12`` / ``T12``) to a single key.
* Mixed-case + leading/trailing whitespace canonicalize.
* Empty / None inputs short-circuit cleanly.
* Bug #2 v2: a misclassified API response carries DISTINCT
  ``doc_type`` ≠ ``ai_proposed_doc_type`` so the banner can render
  both sides.
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


# ─────────────────── Bug #2 v2 — DocumentRecord round-trip ──────────────────


def test_document_record_carries_ai_proposed_doc_type_on_misclassified_row() -> None:
    """Sam QA Bug #2 v2: when ``misclassified=true``, the row mapper
    must surface ``ai_proposed_doc_type`` as the Router's read — and
    it must be DISTINCT from ``doc_type`` (which carries the user tag).

    This is the load-bearing assertion for the banner — without it,
    ``userLabel`` and ``aiLabel`` resolve from the same persisted
    column and render "T-12 vs T-12" (Sam's repro).
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.api.documents import _row_to_record

    row = {
        "id": str(uuid4()),
        "deal_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "filename": "sam_anglers_t12.xlsx",
        "doc_type": "T12",  # user tag (worker keeps it sticky)
        "status": "EXTRACTED",
        "uploaded_at": datetime(2026, 6, 28, tzinfo=timezone.utc),
        "content_hash": None,
        "storage_key": None,
        "size_bytes": 0,
        "page_count": None,
        "parser": None,
        "extraction_data": None,
        "usali_score": None,
        "usali_deviations": None,
        "user_provided_doc_type": "T12",
        "fiscal_year": None,
        "misclassified": True,
        # The Router's read — the worker thought it was a monthly P&L
        # even though the user tagged it as T-12.
        "ai_proposed_doc_type": "PNL_MONTHLY",
        "year_mismatch": False,
        "extracted_period_year": 2025,
    }
    record = _row_to_record(row)

    assert record.misclassified is True
    assert record.doc_type == "T12", (
        "doc_type must keep the user's tag — engines route on this"
    )
    assert record.ai_proposed_doc_type == "PNL_MONTHLY", (
        "ai_proposed_doc_type must surface the Router's read so the "
        "banner can render aiLabel distinctly from userLabel"
    )
    # The headline contract: they're distinct.
    assert record.ai_proposed_doc_type != record.doc_type


def test_document_record_carries_null_ai_proposal_on_clean_extraction() -> None:
    """A correctly-categorized extraction leaves
    ``ai_proposed_doc_type`` NULL — the banner short-circuits and
    renders nothing, even if a stale ``misclassified=true`` somehow
    lingers (which the v2 migration cleans up on startup).
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.api.documents import _row_to_record

    row = {
        "id": str(uuid4()),
        "deal_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "filename": "sam_anglers_t12.xlsx",
        "doc_type": "T12",
        "status": "EXTRACTED",
        "uploaded_at": datetime(2026, 6, 28, tzinfo=timezone.utc),
        "content_hash": None,
        "storage_key": None,
        "size_bytes": 0,
        "page_count": None,
        "parser": None,
        "extraction_data": None,
        "usali_score": None,
        "usali_deviations": None,
        "user_provided_doc_type": "T12",
        "fiscal_year": None,
        "misclassified": False,
        "ai_proposed_doc_type": None,
        "year_mismatch": False,
        "extracted_period_year": 2025,
    }
    record = _row_to_record(row)

    assert record.misclassified is False
    assert record.ai_proposed_doc_type is None


def test_document_record_legacy_pre_v2_row_has_null_ai_proposal() -> None:
    """Legacy rows (extracted before the v2 migration ran) don't
    carry the ``ai_proposed_doc_type`` column at all. The row mapper
    accepts a missing key as NULL — the banner then short-circuits."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.api.documents import _row_to_record

    row = {
        "id": str(uuid4()),
        "deal_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "filename": "sam_anglers_t12.xlsx",
        "doc_type": "T12",
        "status": "EXTRACTED",
        "uploaded_at": datetime(2026, 6, 28, tzinfo=timezone.utc),
        "content_hash": None,
        "storage_key": None,
        "size_bytes": 0,
        "page_count": None,
        "parser": None,
        "extraction_data": None,
        "usali_score": None,
        "usali_deviations": None,
        "user_provided_doc_type": "T12",
        "fiscal_year": None,
        "misclassified": True,  # stale legacy flag
        # NOTE: no ai_proposed_doc_type key — simulates a pre-v2 SELECT
        # that didn't include the column.
        "year_mismatch": False,
        "extracted_period_year": None,
    }
    record = _row_to_record(row)

    # Stale flag preserved (UI guard: banner short-circuits on missing
    # ai proposal anyway).
    assert record.misclassified is True
    assert record.ai_proposed_doc_type is None
