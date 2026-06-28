"""Deterministic underwriting engines (revenue, F&B, expense, capital, debt, …).

All engines are pure-Python, deterministic, no I/O. They take Pydantic
inputs and emit Pydantic outputs from ``fondok_schemas`` (or local
extensions thereof). The Analyst LLM agent composes them; it never
re-implements their math.
"""

from .base import BaseEngine, EngineInput, EngineOutput
from .capital import CapitalEngine, CapitalEngineInput, CapitalEngineOutput
from .debt import (
    DebtEngine,
    DebtEngineInputExt,
    DebtEngineOutputExt,
    build_amort_schedule,
    build_stack_schedule,
    pmt,
    run_refi_test,
)
from .expense import ExpenseEngine, ExpenseEngineInput, ExpenseEngineOutput
from .fb_revenue import FBRevenueEngine, FBRevenueInput, FBRevenueOutput
from .loi_generator import LOIDraft, draft_loi
from .partnership import (
    PartnershipEngine,
    PartnershipInputExt,
    PartnershipOutputExt,
)
from .price_solver import MaxPriceResult, solve_max_price
from .pricing_sensitivity import (
    SensitivityCell as PricingSensitivityCell,
)
from .pricing_sensitivity import (
    SensitivityGrid,
    run_sensitivity_grid,
)
from .returns import (
    ReturnsEngine,
    ReturnsEngineInputExt,
    ReturnsEngineOutputExt,
    irr,
    npv,
)
from .revenue import RevenueEngine
from .sensitivity import (
    SensitivityCell,
    SensitivityEngine,
    SensitivityInput,
    SensitivityOutput,
)

__all__ = [
    "BaseEngine",
    "CapitalEngine",
    "CapitalEngineInput",
    "CapitalEngineOutput",
    "DebtEngine",
    "DebtEngineInputExt",
    "DebtEngineOutputExt",
    "EngineInput",
    "EngineOutput",
    "ExpenseEngine",
    "ExpenseEngineInput",
    "ExpenseEngineOutput",
    "FBRevenueEngine",
    "FBRevenueInput",
    "FBRevenueOutput",
    "LOIDraft",
    "MaxPriceResult",
    "PartnershipEngine",
    "PartnershipInputExt",
    "PartnershipOutputExt",
    "PricingSensitivityCell",
    "ReturnsEngine",
    "ReturnsEngineInputExt",
    "ReturnsEngineOutputExt",
    "RevenueEngine",
    "SensitivityCell",
    "SensitivityEngine",
    "SensitivityGrid",
    "SensitivityInput",
    "SensitivityOutput",
    "build_amort_schedule",
    "build_stack_schedule",
    "draft_loi",
    "irr",
    "npv",
    "pmt",
    "run_refi_test",
    "run_sensitivity_grid",
    "solve_max_price",
]
