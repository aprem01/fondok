"""Sensitivity engine — 2-D tables across ADR, occupancy, exit cap, debt cost."""

from __future__ import annotations

from .base import BaseEngine, EngineInput, EngineOutput


class SensitivityEngine(BaseEngine):
    name = "sensitivity"

    async def run(self, payload: EngineInput) -> EngineOutput:
        """Stub. Real engine: parameter sweeps + IRR / break-even surface."""
        return self._empty_output(payload.deal_id)


__all__ = ["SensitivityEngine"]
