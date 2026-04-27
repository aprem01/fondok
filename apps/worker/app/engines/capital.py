"""Capital engine — sources & uses, PIP, capex schedule."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class CapitalEngine(BaseEngine):
    name = "capital"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: PIP timing, key-money, sponsor equity, holdbacks."""
        return self._empty_output(payload.deal_id)


__all__ = ["CapitalEngine"]
