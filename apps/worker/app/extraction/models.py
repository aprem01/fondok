"""Pydantic envelopes for the parser layer.

These shapes are private to the worker — agents downstream consume
them via ``ParsedDocument``. Heavier ``fondok_schemas.Document`` /
``ExtractionField`` types live one tier up (after the LLM Extractor
pulls structured fields out of these raw page texts).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParsedPage(BaseModel):
    """A single page extracted from a source PDF.

    ``tables`` is a list of 2D string arrays — each table is a list of
    rows, each row a list of cell strings. We keep it stringly-typed at
    the parser tier and let the LLM Extractor coerce to numbers.
    """

    model_config = ConfigDict(extra="forbid")

    page_num: int = Field(ge=1)
    text: str = ""
    tables: list[list[list[str]]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    """The output of ``parse_pdf`` — pages plus provenance."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    total_pages: int = Field(ge=0)
    pages: list[ParsedPage] = Field(default_factory=list)
    content_hash: str = Field(
        description="sha256 hex digest of the original file bytes."
    )
    parsed_at: datetime
    parser: str = Field(
        default="pymupdf",
        description="Identifier of the parser that produced this document "
        "(e.g. 'llamaparse', 'pymupdf', 'pymupdf+pdfplumber').",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseError(RuntimeError):
    """Raised when a PDF cannot be parsed by any available backend."""


__all__ = ["ParsedDocument", "ParsedPage", "ParseError"]
