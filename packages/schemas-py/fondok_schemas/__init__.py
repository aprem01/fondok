"""Fondok shared schemas.

These Pydantic models are the single source of truth for engine and agent
boundaries across the Fondok hotel underwriting platform. Every engine
input/output, every gate decision, every memo and variance flag round-trips
through one of these types.

The TypeScript equivalents live in `packages/schemas-ts`. They must stay
in lockstep — changing one without the other is a build break.
"""

from .analysis import (
    AnalysisReport,
    Insight,
    RiskAssessment,
    RiskCategory,
    RiskCategoryName,
    RiskTier,
    ScenarioSummary,
)
from .common import Money, ModelCall, Risk, Severity, TenantScoped
from .confidence import ConfidenceReport
from .cost import AgentCost, DealCostReport
from .deal import (
    Deal,
    DealStage,
    DealStatus,
    DealSummary,
    PositioningTier,
    ReturnProfile,
    Service,
)
from .document import (
    Document,
    DocumentStatus,
    DocType,
    ExtractionField,
)
from .financial import (
    DepartmentalExpenses,
    FixedCharges,
    ModelAssumptions,
    USALIFinancials,
    UndistributedExpenses,
)
from .gates import Gate1Decision, Gate2Decision, GateDecision
from .market import (
    BuyerType,
    CompSet,
    MarketData,
    MarketDataSource,
    TransactionComp,
)
from .memo import (
    Citation,
    InvestmentMemo,
    MemoSection,
    MemoSectionId,
)
from .partnership import (
    PartnerReturn,
    PartnershipInput,
    PartnershipOutput,
    WaterfallTier,
)
from .underwriting import (
    CashFlowEngineInput,
    CashFlowEngineOutput,
    CashFlowYear,
    DebtEngineInput,
    DebtEngineOutput,
    DebtServiceYear,
    InvestmentEngineInput,
    InvestmentEngineOutput,
    PLEngineInput,
    PLEngineOutput,
    RevenueEngineInput,
    RevenueEngineOutput,
    RevenueProjectionYear,
    ReturnsEngineInput,
    ReturnsEngineOutput,
    ScenarioName,
    SourceUseLine,
)
from .variance import VarianceFlag, VarianceReport

__all__ = [
    # analysis
    "AnalysisReport",
    "Insight",
    "RiskAssessment",
    "RiskCategory",
    "RiskCategoryName",
    "RiskTier",
    "ScenarioSummary",
    # common
    "ModelCall",
    "Money",
    "Risk",
    "Severity",
    "TenantScoped",
    # confidence
    "ConfidenceReport",
    # cost
    "AgentCost",
    "DealCostReport",
    # deal
    "Deal",
    "DealStage",
    "DealStatus",
    "DealSummary",
    "PositioningTier",
    "ReturnProfile",
    "Service",
    # document
    "DocType",
    "Document",
    "DocumentStatus",
    "ExtractionField",
    # financial
    "DepartmentalExpenses",
    "FixedCharges",
    "ModelAssumptions",
    "USALIFinancials",
    "UndistributedExpenses",
    # gates
    "Gate1Decision",
    "Gate2Decision",
    "GateDecision",
    # market
    "BuyerType",
    "CompSet",
    "MarketData",
    "MarketDataSource",
    "TransactionComp",
    # memo
    "Citation",
    "InvestmentMemo",
    "MemoSection",
    "MemoSectionId",
    # partnership
    "PartnerReturn",
    "PartnershipInput",
    "PartnershipOutput",
    "WaterfallTier",
    # underwriting
    "CashFlowEngineInput",
    "CashFlowEngineOutput",
    "CashFlowYear",
    "DebtEngineInput",
    "DebtEngineOutput",
    "DebtServiceYear",
    "InvestmentEngineInput",
    "InvestmentEngineOutput",
    "PLEngineInput",
    "PLEngineOutput",
    "RevenueEngineInput",
    "RevenueEngineOutput",
    "RevenueProjectionYear",
    "ReturnsEngineInput",
    "ReturnsEngineOutput",
    "ScenarioName",
    "SourceUseLine",
    # variance
    "VarianceFlag",
    "VarianceReport",
]

__version__ = "0.1.0"
