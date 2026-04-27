"""Partnership engine — JV waterfall, promote tiers, GP/LP returns split."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class PartnershipEngine(BaseEngine):
    name = "partnership"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: pref returns, catch-up, IRR-tier promotes."""
        return self._empty_output(payload.deal_id)


__all__ = ["PartnershipEngine"]
