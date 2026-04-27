"""Shared primitives reused across every Fondok domain."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    CRITICAL = "Critical"
    WARN = "Warn"
    INFO = "Info"


class Risk(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class TenantScoped(BaseModel):
    """Mixin enforcing tenant isolation at the schema level."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: Annotated[str, Field(min_length=1, description="Tenant identifier; RLS predicate.")]


class Money(BaseModel):
    """USD-denominated value. Cents kept as float for spreadsheet parity."""

    model_config = ConfigDict(extra="forbid")

    amount: float
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"


class ModelCall(BaseModel):
    """Audit record for a single LLM call."""

    model_config = ConfigDict(extra="forbid")

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    trace_id: str
    started_at: datetime
    completed_at: datetime
