"""Chain-of-verification schemas.

The Extractor agent pulls structured numbers out of source documents
and tags each ``ExtractionField`` with a ``source_page``. The verifier
re-reads the cited page text deterministically and confirms the
extracted value actually appears there — a cheap, fast guard against
the #1 IC objection ("the model made up a number") and the LP-disclosure
risk that follows.

This module defines the *reporting* shape. The verifier logic lives in
``app.verification`` (worker side).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CitationStatus(str, Enum):
    """Outcome of re-reading a single cited number."""

    MATCH = "match"
    CLOSE = "close"
    MISMATCH = "mismatch"
    UNVERIFIABLE = "unverifiable"


class VerificationCheck(BaseModel):
    """One re-read of a single cited numeric ExtractionField."""

    model_config = ConfigDict(extra="forbid")

    field_name: Annotated[str, Field(min_length=1, max_length=200)]
    cited_value: Annotated[str, Field(max_length=200)]
    parsed_value: float | None = None
    found_in_source: float | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    status: CitationStatus
    source_doc_id: UUID | None = None
    source_page: Annotated[int, Field(ge=1)] | None = None
    excerpt: Annotated[str, Field(max_length=400)] | None = None


class VerificationReport(BaseModel):
    """Per-deal verifier output.

    Persisted to ``verification_reports`` and returned as-is from
    ``GET /deals/{deal_id}/verification``. ``pass_rate`` is computed
    on the fly so the persisted shape stays compact.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    checks: list[VerificationCheck] = Field(default_factory=list)
    generated_at: datetime

    @property
    def pass_rate(self) -> float:
        """Share of verifiable checks that matched or were within tolerance.

        Unverifiable checks (citation page had no parseable number) are
        excluded from the denominator so empty-excerpt cases don't drag
        the score for the LLM. Returns 0.0 when nothing is verifiable —
        the safe default for a deal with no grounding.
        """
        verifiable = [
            c for c in self.checks if c.status != CitationStatus.UNVERIFIABLE
        ]
        if not verifiable:
            return 0.0
        passed = [
            c
            for c in verifiable
            if c.status in (CitationStatus.MATCH, CitationStatus.CLOSE)
        ]
        return len(passed) / len(verifiable)

    @property
    def match_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CitationStatus.MATCH)

    @property
    def close_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CitationStatus.CLOSE)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CitationStatus.MISMATCH)

    @property
    def unverifiable_count(self) -> int:
        return sum(
            1 for c in self.checks if c.status == CitationStatus.UNVERIFIABLE
        )


__all__ = [
    "CitationStatus",
    "VerificationCheck",
    "VerificationReport",
]
