"""Returns engine — unlevered/levered IRR, equity multiple, cash-on-cash."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class ReturnsEngine(BaseEngine):
    name = "returns"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: hold period, exit cap, residual value, waterfall."""
        return self._empty_output(payload.deal_id)


__all__ = ["ReturnsEngine"]
