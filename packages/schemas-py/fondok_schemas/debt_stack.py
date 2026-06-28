"""Debt Stack v2 schemas — senior + mezz + pref equity tranches.

Wave 4 W4.4. Institutional hotel deals layer their capital stack:

* **Senior** loan — 55-65% LTV, first-lien, lowest rate.
* **Mezzanine** — 65-75% LTC, second-lien (or intercreditor pledge).
* **Preferred equity** — 75-80% LTC, structured-equity tranche, no lien.

Each tranche carries its own rate, IO-stub, amortization, upfront /
exit fees and priority rank. The :class:`DebtStackInput` is a self-
contained engine input that feeds the v2 debt engine
(``apps/worker/app/engines/debt.py``) and is consumed by the Returns
engine as ``total_ds_by_year`` rather than a single senior DS.

Backward compatibility: a single-tranche stack with a senior-only
loan reproduces the byte-identical output of the pre-W4.4 single-
loan engine (verified by
``test_single_senior_tranche_matches_legacy_single_loan``).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Tranche type names — kept in lockstep with the OverridePanel UI.
TrancheName = Literal["senior", "mezz", "pref_equity"]


class DebtTranche(BaseModel):
    """One slice of the institutional capital stack.

    * ``priority_rank`` enforces senior=1, mezz=2, pref_equity=3 in
      the stack-level validator (rank is what an intercreditor
      agreement uses; senior cash-flow waterfall = lower rank first).
    * ``io_period_months`` is the stub period of interest-only
      payments before P&I amortization begins. Mezzanine and pref
      equity are typically IO for the entire term (set
      ``amortization_months`` ≥ term_months and ``io_period_months ==
      amortization_months`` to model that).
    * ``upfront_fee_pct`` is paid at funding and added to the
      sources-and-uses basis (not capitalized to the loan balance —
      modeled as a closing cost). ``exit_fee_pct`` is paid at
      maturity / refinance and lands on debt service in the final
      month of the term.
    """

    model_config = ConfigDict(extra="forbid")

    name: TrancheName
    label: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    principal_usd: Annotated[float, Field(gt=0)]
    rate_pct: Annotated[float, Field(ge=0.0, le=1.0)]
    io_period_months: Annotated[int, Field(ge=0, le=360)] = 0
    amortization_months: Annotated[int, Field(ge=0, le=480)] = 360
    upfront_fee_pct: Annotated[float, Field(ge=0.0, le=10.0)] = 0.0
    exit_fee_pct: Annotated[float, Field(ge=0.0, le=10.0)] = 0.0
    is_senior: bool
    priority_rank: Annotated[int, Field(ge=1, le=3)]

    @model_validator(mode="after")
    def _check_io_not_longer_than_amort(self) -> "DebtTranche":
        """IO can equal but not exceed the amortization horizon."""
        if self.amortization_months > 0 and self.io_period_months > self.amortization_months:
            raise ValueError(
                "io_period_months "
                f"({self.io_period_months}) must not exceed "
                f"amortization_months ({self.amortization_months})"
            )
        return self

    @model_validator(mode="after")
    def _check_senior_rank_alignment(self) -> "DebtTranche":
        """Senior tranche must carry priority_rank=1 and vice versa."""
        if self.is_senior and self.priority_rank != 1:
            raise ValueError(
                "is_senior=True requires priority_rank=1; "
                f"got {self.priority_rank}"
            )
        if not self.is_senior and self.priority_rank == 1:
            raise ValueError(
                "priority_rank=1 is reserved for the senior tranche"
            )
        if self.name == "senior" and not self.is_senior:
            raise ValueError("name='senior' requires is_senior=True")
        return self


class DebtStackInput(BaseModel):
    """Self-contained input to the v2 debt engine.

    The engine computes one amortization schedule per tranche, then
    aggregates debt service / DSCR / debt yield at the stack level.
    The refi test runs at ``refi_test_year`` against the market debt
    yield / DSCR floor.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    purchase_price_usd: Annotated[float, Field(gt=0)]
    keys: Annotated[int, Field(gt=0)]
    tranches: list[DebtTranche] = Field(min_length=1)
    noi_by_year: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)
    # Hold-period length the engine schedules out to (in years).
    # Defaults to the longest tranche term — keeps the schedule
    # aligned with the rest of the underwriting (cash-flow / returns).
    term_years: Annotated[int, Field(ge=1, le=40)] = 5
    refi_test_year: Annotated[int, Field(ge=1, le=20)] | None = 5
    refi_market_debt_yield_pct: Annotated[float, Field(gt=0.0, le=0.30)] = 0.09
    refi_market_dscr_min: Annotated[float, Field(gt=0.0, le=5.0)] = 1.30
    # Market cap rate used for the refi-year property value estimate
    # (``noi[refi+1] / market_cap_rate``). When ``None`` the engine
    # falls back to the deal's exit_cap_rate via ``exit_cap_rate``.
    refi_market_cap_rate: Annotated[float, Field(gt=0.0, le=0.30)] | None = None
    # Refi rate used to size DSCR at the refi test point. When
    # ``None`` we reuse the senior tranche's rate as the proxy.
    refi_market_rate_pct: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    # Exit cap rate fallback for the refi property valuation.
    exit_cap_rate: Annotated[float, Field(gt=0.0, le=0.30)] = 0.07

    @model_validator(mode="after")
    def _check_one_senior(self) -> "DebtStackInput":
        seniors = [t for t in self.tranches if t.is_senior]
        if len(seniors) != 1:
            raise ValueError(
                f"DebtStackInput requires exactly one senior tranche; "
                f"got {len(seniors)}"
            )
        # Ranks must be unique within (1, 2, 3).
        ranks = [t.priority_rank for t in self.tranches]
        if len(set(ranks)) != len(ranks):
            raise ValueError(
                f"priority_rank must be unique per tranche; got {ranks}"
            )
        return self


class TrancheAmortYear(BaseModel):
    """One year of one tranche's amortization schedule."""

    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    interest_usd: Annotated[float, Field(ge=0)]
    principal_usd: Annotated[float, Field(ge=0)]
    debt_service_usd: Annotated[float, Field(ge=0)]
    ending_balance_usd: Annotated[float, Field(ge=0)]


class TrancheSchedule(BaseModel):
    """The per-tranche amortization roll-up (annual)."""

    model_config = ConfigDict(extra="forbid")

    name: TrancheName
    label: str | None = None
    priority_rank: Annotated[int, Field(ge=1, le=3)]
    years: list[TrancheAmortYear] = Field(default_factory=list)
    # Echoed for the UI strip + refi math.
    upfront_fee_usd: Annotated[float, Field(ge=0)] = 0.0
    exit_fee_usd: Annotated[float, Field(ge=0)] = 0.0


class RefiTestResult(BaseModel):
    """Outcome of the refinance test at ``triggered_year``.

    ``can_refi`` is True when the market can absorb the outstanding
    balance at the market debt yield AND the DSCR clears the floor
    at the refi rate. When ``can_refi`` is False, ``cash_to_close_equity``
    is the additional sponsor equity needed to bring the loan down to
    the max refinance proceeds (``max_refi_debt_usd``).
    """

    model_config = ConfigDict(extra="forbid")

    triggered_year: Annotated[int, Field(ge=1, le=20)]
    can_refi: bool
    max_refi_debt_usd: Annotated[float, Field(ge=0)]
    outstanding_balance_usd: Annotated[float, Field(ge=0)]
    cash_to_close_equity: Annotated[float, Field(ge=0)] = 0.0
    refi_dscr: Annotated[float, Field(ge=0)] | None = None
    refi_debt_yield: Annotated[float, Field(ge=0)] | None = None
    refi_property_value_usd: Annotated[float, Field(ge=0)] | None = None
    refi_rate_used_pct: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    notes: list[str] = Field(default_factory=list)


class DebtStackOutput(BaseModel):
    """Output of the v2 debt engine.

    ``total_ds_by_year`` is the aggregated debt service across every
    tranche per year — the Returns engine consumes this directly. The
    per-tranche / per-rank breakdowns let the UI render the stack
    waterfall and the IC memo cite per-tranche economics.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    total_ds_by_year: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)
    debt_service_per_tranche_by_year: dict[str, list[float]] = Field(default_factory=dict)
    debt_yield_by_year: list[float] = Field(default_factory=list)
    dscr_by_year_per_tranche: dict[str, list[float]] = Field(default_factory=dict)
    dscr_blended_by_year: list[float] = Field(default_factory=list)
    cumulative_ltc: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    cumulative_ltv: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    total_debt_usd: Annotated[float, Field(ge=0)] = 0.0
    weighted_avg_rate_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    schedules: list[TrancheSchedule] = Field(default_factory=list)
    refi_test: RefiTestResult | None = None
    # Aggregate upfront fees (sum across tranches) — feed into the
    # capital engine's sources-and-uses as a closing cost.
    total_upfront_fees_usd: Annotated[float, Field(ge=0)] = 0.0


__all__ = [
    "DebtStackInput",
    "DebtStackOutput",
    "DebtTranche",
    "RefiTestResult",
    "TrancheAmortYear",
    "TrancheName",
    "TrancheSchedule",
]
