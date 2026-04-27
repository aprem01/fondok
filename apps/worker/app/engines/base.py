"""Base class shared by every deterministic underwriting engine.

Engines are the non-LLM half of the platform — pure-Python math run on
the normalized spread + market inputs. They produce typed outputs the
Analyst agent can cite without hallucinating.

Every concrete engine subclasses :class:`BaseEngine` and exposes a single
synchronous ``run`` method that accepts a typed Pydantic input model and
returns a typed Pydantic output model. The ABC enforces:

- a ``name`` class attribute that uniquely identifies the engine
- a ``run`` method (deterministic, side-effect free)

The legacy ``EngineInput``/``EngineOutput`` dict envelopes are kept for
backward compatibility with the agent runtime that still passes opaque
dicts; new code should use the typed schemas in
``fondok_schemas.underwriting`` and ``fondok_schemas.partnership``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

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


TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


class BaseEngine(ABC, Generic[TInput, TOutput]):
    """All concrete engines subclass this and implement ``run``.

    Concrete engines should declare typed ``Input`` and ``Output`` Pydantic
    models (typically imported from ``fondok_schemas``) and bind them via
    the generic type parameters. The contract is:

    * ``name`` — short identifier used in logs, citations and provenance
    * ``run(payload)`` — pure function, deterministic, no I/O, no time

    Subclasses MUST override ``name`` and ``run``.
    """

    name: str = "base"

    @abstractmethod
    def run(self, payload: TInput) -> TOutput:
        """Execute the engine. Must be deterministic and side-effect free."""
        raise NotImplementedError

    def _empty_output(self, deal_id: str) -> EngineOutput:
        """Helper for legacy dict-envelope callers."""
        return EngineOutput(deal_id=deal_id, engine=self.name)


__all__ = ["BaseEngine", "EngineInput", "EngineOutput"]
