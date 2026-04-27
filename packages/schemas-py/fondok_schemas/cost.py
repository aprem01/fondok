"""Per-deal LLM cost reporting.

These models are surfaced by ``GET /deals/{id}/costs`` and consumed by
the web ``CostPanel`` component. Numbers are in USD; tokens are raw
counts (no normalization to 1K/1M). The ``timeline`` is a bounded
slice of the most recent ``ModelCall`` records — the worker keeps the
canonical history in the ``model_calls`` table.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .common import ModelCall


class AgentCost(BaseModel):
    """Rolled-up usage and spend for a single agent (or model) bucket."""

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=80)
    calls: int = Field(ge=0, default=0)
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_creation_tokens: int = Field(ge=0, default=0)
    cost_usd: Decimal = Field(default=Decimal("0"))
    avg_latency_ms: float = Field(ge=0.0, default=0.0)


class DealCostReport(BaseModel):
    """Aggregated cost dashboard payload for a single deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    total_cost_usd: Decimal = Field(default=Decimal("0"))
    budget_usd: Decimal = Field(default=Decimal("20"))
    cache_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    by_agent: list[AgentCost] = Field(default_factory=list)
    by_model: dict[str, AgentCost] = Field(default_factory=dict)
    timeline: list[ModelCall] = Field(default_factory=list)
    generated_at: datetime


__all__ = ["AgentCost", "DealCostReport"]
