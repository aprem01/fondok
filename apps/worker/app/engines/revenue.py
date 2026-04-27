"""Revenue engine — rooms-revenue projection (occupancy x ADR x rooms-available)."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class RevenueEngine(BaseEngine):
    name = "revenue"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: monthly occ/ADR ramps, seasonality, comp-set RevPAR."""
        return self._empty_output(payload.deal_id)


__all__ = ["RevenueEngine"]
