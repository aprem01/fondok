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


class SensitivitySpec(BaseModel):
    """One named sensitivity: a metric flexed across two assumption axes.

    FON-53 — the Scenario Analysis tab lets the investor pick from several of
    these. Each spec re-runs the returns engine across its own row×col grid.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    row_variable: SensitivityVariable
    row_values: list[float] = Field(min_length=2, max_length=11)
    col_variable: SensitivityVariable
    col_values: list[float] = Field(min_length=2, max_length=11)
    metric: SensitivityMetric = "levered_irr"


class SensitivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    base_returns_input: ReturnsEngineInputExt
    row_variable: SensitivityVariable
    row_values: list[float] = Field(min_length=2, max_length=11)
    col_variable: SensitivityVariable
    col_values: list[float] = Field(min_length=2, max_length=11)
    metric: SensitivityMetric = "levered_irr"
    # FON-53 — when provided, the engine computes each spec into ``matrices``.
    # The top-level row/col/metric above still mirror the primary matrix so the
    # existing single-matrix consumers keep working.
    specs: list[SensitivitySpec] | None = None


class SensitivityCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_value: float
    col_value: float
    value: float
    is_base: bool = False


class SensitivityMatrix(BaseModel):
    """A single named grid — one entry in the Scenario Analysis dropdown."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    metric: SensitivityMetric
    row_variable: SensitivityVariable
    col_variable: SensitivityVariable
    rows: list[float]
    cols: list[float]
    cells: list[SensitivityCell]


class SensitivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    row_variable: SensitivityVariable
    col_variable: SensitivityVariable
    metric: SensitivityMetric
    rows: list[float]
    cols: list[float]
    cells: list[SensitivityCell]
    # FON-53 — the full set of named sensitivities (includes the primary one).
    matrices: list[SensitivityMatrix] = Field(default_factory=list)


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

    def _compute_cells(
        self,
        returns_engine: ReturnsEngine,
        base_input: ReturnsEngineInputExt,
        snap: _BaseSnapshot,
        row_variable: SensitivityVariable,
        row_values: list[float],
        col_variable: SensitivityVariable,
        col_values: list[float],
        metric: SensitivityMetric,
    ) -> list[SensitivityCell]:
        base_row_value = getattr(snap, row_variable)
        base_col_value = getattr(snap, col_variable)
        cells: list[SensitivityCell] = []
        for r in row_values:
            for c in col_values:
                trial = _flex(base_input, row_variable, r)
                trial = _flex(trial, col_variable, c)
                result = returns_engine.run(trial)
                cells.append(
                    SensitivityCell(
                        row_value=r,
                        col_value=c,
                        value=getattr(result, metric),
                        is_base=(
                            abs(r - base_row_value) < 1e-9
                            and abs(c - base_col_value) < 1e-9
                        ),
                    )
                )
        return cells

    def run(self, payload: SensitivityInput) -> SensitivityOutput:
        returns_engine = ReturnsEngine()
        snap = _snapshot(payload.base_returns_input)

        # Primary matrix — the top-level fields the legacy consumers read.
        primary_cells = self._compute_cells(
            returns_engine, payload.base_returns_input, snap,
            payload.row_variable, payload.row_values,
            payload.col_variable, payload.col_values, payload.metric,
        )

        # FON-53 — full set of named matrices for the Scenario Analysis dropdown.
        matrices: list[SensitivityMatrix] = []
        specs = payload.specs or []
        for spec in specs:
            cells = self._compute_cells(
                returns_engine, payload.base_returns_input, snap,
                spec.row_variable, spec.row_values,
                spec.col_variable, spec.col_values, spec.metric,
            )
            matrices.append(
                SensitivityMatrix(
                    key=spec.key,
                    label=spec.label,
                    metric=spec.metric,
                    row_variable=spec.row_variable,
                    col_variable=spec.col_variable,
                    rows=list(spec.row_values),
                    cols=list(spec.col_values),
                    cells=cells,
                )
            )
        # Even with no specs, expose the primary as a matrix so the UI has a
        # uniform surface to render.
        if not matrices:
            matrices.append(
                SensitivityMatrix(
                    key="primary",
                    label="Levered IRR — Exit Cap × RevPAR Growth",
                    metric=payload.metric,
                    row_variable=payload.row_variable,
                    col_variable=payload.col_variable,
                    rows=list(payload.row_values),
                    cols=list(payload.col_values),
                    cells=primary_cells,
                )
            )

        return SensitivityOutput(
            deal_id=payload.deal_id,
            row_variable=payload.row_variable,
            col_variable=payload.col_variable,
            metric=payload.metric,
            rows=list(payload.row_values),
            cols=list(payload.col_values),
            cells=primary_cells,
            matrices=matrices,
        )


__all__ = [
    "SensitivityEngine",
    "SensitivityInput",
    "SensitivityOutput",
    "SensitivityCell",
    "SensitivityMatrix",
    "SensitivitySpec",
    "SensitivityVariable",
    "SensitivityMetric",
]
