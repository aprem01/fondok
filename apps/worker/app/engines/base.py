"""Base class shared by every deterministic underwriting engine.

Engines are the non-LLM half of the platform — pure-Python math run on
the normalized spread + market inputs. They produce typed outputs the
Analyst agent can cite without hallucinating.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class EngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    engine: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class BaseEngine(ABC):
    """All concrete engines subclass this and implement ``run``."""

    name: str = "base"

    @abstractmethod
    async def run(self, payload: EngineInput) -> EngineOutput:
        """Execute the engine. Must be deterministic and side-effect free."""

    def _empty_output(self, deal_id: str) -> EngineOutput:
        """Helper for stub subclasses."""
        return EngineOutput(deal_id=deal_id, engine=self.name)


__all__ = ["BaseEngine", "EngineInput", "EngineOutput"]
