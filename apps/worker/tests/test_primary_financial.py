"""FON-22 — primary-financial-source ranking (_mark_primary_financial).

Verifies the derived "Primary source" flag the DataRoom badges: full-year
sources beat partial periods, the most recent period wins within full-year,
and a detailed statement beats a summary of the same period.
"""

from datetime import datetime, timezone
from uuid import uuid4

from app.api.documents import (
    DOC_STATUS_EXTRACTED,
    DOC_STATUS_UPLOADED,
    DocumentRecord,
    _mark_primary_financial,
)


def _doc(
    doc_type: str,
    *,
    status: str = DOC_STATUS_EXTRACTED,
    year: int | None = None,
    score: float | None = None,
) -> DocumentRecord:
    return DocumentRecord(
        id=uuid4(),
        deal_id=uuid4(),
        tenant_id=uuid4(),
        filename=f"{doc_type}.xlsx",
        doc_type=doc_type,
        status=status,
        uploaded_at=datetime.now(timezone.utc),
        extracted_period_year=year,
        structural_pnl_score=score,
    )


def _primary(records: list[DocumentRecord]) -> DocumentRecord | None:
    _mark_primary_financial(records)
    flagged = [r for r in records if r.primary_financial_source]
    assert len(flagged) <= 1, "at most one primary source"
    return flagged[0] if flagged else None


def test_no_financials_flags_nothing():
    recs = [_doc("OM"), _doc("STR")]
    assert _primary(recs) is None


def test_full_year_beats_partial_period():
    monthly = _doc("PNL_MONTHLY", year=2025, score=0.9)
    annual = _doc("T12", year=2024, score=0.5)
    winner = _primary([monthly, annual])
    assert winner is annual, "annual T-12 outranks a monthly P&L even if newer/richer"


def test_most_recent_full_year_wins():
    old = _doc("PNL", year=2023, score=0.9)
    new = _doc("T12", year=2025, score=0.4)
    winner = _primary([old, new])
    assert winner is new


def test_detailed_beats_summary_same_year():
    summary = _doc("PNL", year=2024, score=0.3)
    detailed = _doc("PNL", year=2024, score=0.85)
    winner = _primary([summary, detailed])
    assert winner is detailed


def test_only_extracted_docs_eligible():
    still_processing = _doc("T12", status=DOC_STATUS_UPLOADED, year=2025, score=0.9)
    done = _doc("PNL", status=DOC_STATUS_EXTRACTED, year=2024, score=0.5)
    winner = _primary([still_processing, done])
    assert winner is done, "an un-extracted doc can't be the source of truth"
