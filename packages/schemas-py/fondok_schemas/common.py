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
    """Audit record for a single LLM call.

    Cache fields are Anthropic-specific. ``cache_creation_input_tokens``
    are billed at the normal input rate plus a 25% premium (cache write);
    ``cache_read_input_tokens`` are billed at 10% of the normal input rate
    (cache hit). Both are subsets of the input token stream — they are
    surfaced separately so the observability layer can compute the cache
    hit rate without re-reading the response payload.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    trace_id: str
    started_at: datetime
    completed_at: datetime
    # Optional Anthropic prompt-cache accounting. Older callers that
    # don't supply these still work; defaults are zero.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Which agent emitted this call. Optional so legacy callers don't break.
    agent_name: str | None = None
