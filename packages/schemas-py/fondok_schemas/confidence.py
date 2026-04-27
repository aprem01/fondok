"""Confidence reporting — drives HITL gating and 'requires_human_review'."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ConfidenceReport(BaseModel):
    """Per-field extraction confidence. <0.85 routes to HITL."""

    model_config = ConfigDict(extra="forbid")

    overall: Annotated[float, Field(ge=0.0, le=1.0)]
    by_field: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    low_confidence_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False

    @property
    def is_demo_quality(self) -> bool:
        return self.overall >= 0.95 and not self.low_confidence_fields
