"""F&B revenue engine — outlet-level food, beverage, banquet projections."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class FBRevenueEngine(BaseEngine):
    name = "fb_revenue"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: capture rates, average check, banquet booking pace."""
        return self._empty_output(payload.deal_id)


__all__ = ["FBRevenueEngine"]
