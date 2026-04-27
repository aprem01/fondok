"""Variance — broker-claimed vs. underwriting-actual deltas with severity."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .common import Severity


class VarianceFlag(BaseModel):
    """One field where underwriting actual diverges from broker representation."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    field: Annotated[str, Field(min_length=1, max_length=200)]
    actual: float
    broker: float
    delta: float = Field(description="actual - broker, in original units.")
    delta_pct: float | None = None
    severity: Severity
    rule_id: Annotated[str, Field(min_length=1, max_length=120)]
    source_document_id: UUID | None = None
    source_page: Annotated[int, Field(ge=1)] | None = None
    note: Annotated[str, Field(max_length=2000)] | None = None


class VarianceReport(BaseModel):
    """Bundle of variance flags for a single deal — feeds the IC memo and Analysis tab."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    flags: list[VarianceFlag] = Field(default_factory=list)
    critical_count: Annotated[int, Field(ge=0)] = 0
    warn_count: Annotated[int, Field(ge=0)] = 0
    info_count: Annotated[int, Field(ge=0)] = 0
