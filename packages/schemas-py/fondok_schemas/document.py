"""Documents — uploaded artifacts and the structured fields extracted from them."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocType(str, Enum):
    OM = "OM"
    T12 = "T12"
    STR = "STR"
    PNL = "PNL"
    RENT_ROLL = "RENT_ROLL"
    MARKET_STUDY = "MARKET_STUDY"
    CONTRACT = "CONTRACT"


class DocumentStatus(str, Enum):
    PENDING = "Pending"
    PROCESSING = "Processing"
    EXTRACTED = "Extracted"
    FAILED = "Failed"


class ExtractionField(BaseModel):
    """One field extracted from a source document with its grounding."""

    model_config = ConfigDict(extra="forbid")

    field_name: Annotated[str, Field(min_length=1, max_length=200)]
    value: str | float | int | bool | None = None
    unit: str | None = None
    source_page: Annotated[int, Field(ge=1)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    raw_text: Annotated[str, Field(max_length=4000)] | None = None


class Document(BaseModel):
    """An uploaded artifact attached to a deal."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    filename: Annotated[str, Field(min_length=1, max_length=300)]
    doc_type: DocType
    status: DocumentStatus
    size: Annotated[int, Field(ge=0, description="File size in bytes.")]
    uploaded_at: datetime
    fields_extracted: Annotated[int, Field(ge=0)] = 0
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    populates: list[str] = Field(
        default_factory=list,
        description="Engine identifiers this document feeds (e.g. 'Investment', 'P&L').",
    )
    fields: list[ExtractionField] = Field(default_factory=list)
