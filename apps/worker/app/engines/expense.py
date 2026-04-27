"""Expense engine — undistributed and fixed operating expenses by department."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class ExpenseEngine(BaseEngine):
    name = "expense"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: USALI expense lines, FF&E reserve, mgmt fees."""
        return self._empty_output(payload.deal_id)


__all__ = ["ExpenseEngine"]
