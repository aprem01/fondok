"""Investment memo — the IC-ready output."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .confidence import ConfidenceReport


class Citation(BaseModel):
    """Pointer back to the source document/field that grounds a memo claim."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    page: Annotated[int, Field(ge=1)]
    field: Annotated[str, Field(max_length=200)] | None = None
    excerpt: Annotated[str, Field(max_length=1000)] | None = None


class MemoSectionId(str, Enum):
    EXECUTIVE_SUMMARY = "executive_summary"
    DEAL_OVERVIEW = "deal_overview"
    INVESTMENT_THESIS = "investment_thesis"
    MARKET_ANALYSIS = "market_analysis"
    FINANCIAL_ANALYSIS = "financial_analysis"
    DEBT_STRUCTURE = "debt_structure"
    RETURNS_SUMMARY = "returns_summary"
    PARTNERSHIP_TERMS = "partnership_terms"
    RISK_FACTORS = "risk_factors"
    RECOMMENDATION = "recommendation"


class MemoSection(BaseModel):
    """One section of an investment memo. Every claim cites a source."""

    model_config = ConfigDict(extra="forbid")

    section_id: MemoSectionId
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1)]
    citations: list[Citation] = Field(default_factory=list)
    analyst_edits: str | None = None


class InvestmentMemo(BaseModel):
    """Final IC-ready document."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    sections: list[MemoSection]
    generated_at: datetime
    confidence: ConfidenceReport
    analyst_id: UUID | None = None
    version: Annotated[int, Field(ge=1)] = 1
