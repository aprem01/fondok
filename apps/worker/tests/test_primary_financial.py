"""FON-22 — the Data Room's FALLBACK primary-source ranking
(``_mark_primary_financial``).

The badge normally comes from the loader's own per-period choice
(``engine_runner.statement_roles``; ``tests/test_statement_roles.py``). When
the statement rows cannot be read this fallback keeps a badge on the shared
key over the ``documents`` columns: full-year sources beat partial periods,
the most recent period wins, and an equal period falls to the newest upload —
never to ``structural_pnl_score``.
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


def test_structural_score_never_ranks_an_equal_period():
    """b67bfdee: the Detailed P&L and the Summary CSV both score 1.0 — the
    score saturates — so it is not a ranking member; an equal period falls to
    the newest upload (``records`` is newest-first)."""
    newer = _doc("PNL", year=2024, score=0.3)
    older = _doc("PNL", year=2024, score=0.85)
    assert _primary([newer, older]) is newer


def test_only_extracted_docs_eligible():
    still_processing = _doc("T12", status=DOC_STATUS_UPLOADED, year=2025, score=0.9)
    done = _doc("PNL", status=DOC_STATUS_EXTRACTED, year=2024, score=0.5)
    winner = _primary([still_processing, done])
    assert winner is done, "an un-extracted doc can't be the source of truth"
