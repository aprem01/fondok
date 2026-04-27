"""Deterministic underwriting engines (revenue, F&B, expense, capital, debt, …).

All engines are pure-Python, deterministic, no I/O. They take Pydantic
inputs and emit Pydantic outputs from ``fondok_schemas`` (or local
extensions thereof). The Analyst LLM agent composes them; it never
re-implements their math.
"""

from .base import BaseEngine, EngineInput, EngineOutput
from .capital import CapitalEngine, CapitalEngineInput, CapitalEngineOutput
from .debt import DebtEngine, DebtEngineInputExt, DebtEngineOutputExt, pmt
from .expense import ExpenseEngine, ExpenseEngineInput, ExpenseEngineOutput
from .fb_revenue import FBRevenueEngine, FBRevenueInput, FBRevenueOutput
from .partnership import (
    PartnershipEngine,
    PartnershipInputExt,
    PartnershipOutputExt,
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
    "PartnershipEngine",
    "PartnershipInputExt",
    "PartnershipOutputExt",
    "ReturnsEngine",
    "ReturnsEngineInputExt",
    "ReturnsEngineOutputExt",
    "RevenueEngine",
    "SensitivityCell",
    "SensitivityEngine",
    "SensitivityInput",
    "SensitivityOutput",
    "irr",
    "npv",
    "pmt",
]
