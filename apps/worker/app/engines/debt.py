"""Debt engine — loan amortization, debt service coverage, refinance test."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class DebtEngine(BaseEngine):
    name = "debt"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: SOFR + spread, IO period, DSCR/debt-yield covenants."""
        return self._empty_output(payload.deal_id)


__all__ = ["DebtEngine"]
