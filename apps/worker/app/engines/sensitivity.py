"""Sensitivity engine — 2-D tables across ADR, occupancy, exit cap, debt cost.

Builds a 5x5 (or NxM) matrix by flexing two assumption variables and
re-running the returns engine for each cell. Pure deterministic — no
random sampling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseEngine
from .returns import ReturnsEngine, ReturnsEngineInputExt


SensitivityVariable = Literal[
    "exit_cap_rate",
    "revpar_growth",
    "ltv",
    "interest_rate",
    "hold_years",
    "purchase_price",
]

SensitivityMetric = Literal[
    "levered_irr",
    "unlevered_irr",
    "equity_multiple",
    "year_one_coc",
    "gross_sale_price",
]


class SensitivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    base_returns_input: ReturnsEngineInputExt
    row_variable: SensitivityVariable
    row_values: list[float] = Field(min_length=2, max_length=11)
    col_variable: SensitivityVariable
    col_values: list[float] = Field(min_length=2, max_length=11)
    metric: SensitivityMetric = "levered_irr"


class SensitivityCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_value: float
    col_value: float
    value: float
    is_base: bool = False


class SensitivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    row_variable: SensitivityVariable
    col_variable: SensitivityVariable
    metric: SensitivityMetric
    rows: list[float]
    cols: list[float]
    cells: list[SensitivityCell]


@dataclass
class _BaseSnapshot:
    exit_cap_rate: float
    revpar_growth: float
    ltv: float
    interest_rate: float
    hold_years: int
    purchase_price: float


def _snapshot(payload: ReturnsEngineInputExt) -> _BaseSnapshot:
    a = payload.assumptions
    return _BaseSnapshot(
        exit_cap_rate=a.exit_cap_rate,
        revpar_growth=a.revpar_growth,
        ltv=a.ltv,
        interest_rate=a.interest_rate,
        hold_years=a.hold_years,
        purchase_price=a.purchase_price,
    )


def _flex(
    base: ReturnsEngineInputExt,
    variable: SensitivityVariable,
    value: float,
) -> ReturnsEngineInputExt:
    """Return a new ReturnsEngineInputExt with one assumption replaced."""
    a = base.assumptions
    new_assumptions = a.model_copy(update={variable: value if variable != "hold_years" else int(value)})
    return base.model_copy(update={"assumptions": new_assumptions})


class SensitivityEngine(BaseEngine[SensitivityInput, SensitivityOutput]):
    """Flex two assumptions across a grid; re-run ReturnsEngine per cell."""

    name = "sensitivity"

    def run(self, payload: SensitivityInput) -> SensitivityOutput:
        returns_engine = ReturnsEngine()
        snap = _snapshot(payload.base_returns_input)
        base_row_value = getattr(snap, payload.row_variable)
        base_col_value = getattr(snap, payload.col_variable)

        cells: list[SensitivityCell] = []
        for r in payload.row_values:
            for c in payload.col_values:
                trial = _flex(payload.base_returns_input, payload.row_variable, r)
                trial = _flex(trial, payload.col_variable, c)
                result = returns_engine.run(trial)
                value = getattr(result, payload.metric)
                cells.append(
                    SensitivityCell(
                        row_value=r,
                        col_value=c,
                        value=value,
                        is_base=(
                            abs(r - base_row_value) < 1e-9
                            and abs(c - base_col_value) < 1e-9
                        ),
                    )
                )

        return SensitivityOutput(
            deal_id=payload.deal_id,
            row_variable=payload.row_variable,
            col_variable=payload.col_variable,
            metric=payload.metric,
            rows=list(payload.row_values),
            cols=list(payload.col_values),
            cells=cells,
        )


__all__ = [
    "SensitivityEngine",
    "SensitivityInput",
    "SensitivityOutput",
    "SensitivityCell",
    "SensitivityVariable",
    "SensitivityMetric",
]
