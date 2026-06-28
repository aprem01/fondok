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
from .broker_qa import (
    BrokerQAPair,
    ProposedOverride,
    ProposedOverrideConfidence,
    ResolverVerdict,
)
from .broker_question import BrokerQuestion
from .common import Money, ModelCall, Risk, Severity, TenantScoped
from .comp_sales import CompSalesSet, CompTransaction
from .confidence import ConfidenceReport
from .cost import AgentCost, DealCostReport
from .critic import CriticFinding, CriticReport
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
    FoodBeverageDetail,
    LaborByDepartment,
    ModelAssumptions,
    USALIFinancials,
    UndistributedExpenses,
    UtilitiesDetail,
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
    SourceRegion,
)
from .partnership import (
    PartnerReturn,
    PartnershipInput,
    PartnershipOutput,
    WaterfallTier,
)
from .scenario import Scenario, ScenarioOverride
from .underwriting import (
    CapexPlan,
    CapexScheduleYear,
    CashFlowEngineInput,
    CashFlowEngineOutput,
    CashFlowYear,
    DebtEngineInput,
    DebtEngineOutput,
    DebtServiceYear,
    InvestmentEngineInput,
    InvestmentEngineOutput,
    NonPIPCapex,
    PIPCapex,
    PLEngineInput,
    PLEngineOutput,
    ROICapex,
    ALLOWED_SEGMENT_NAMES,
    RevenueEngineInput,
    RevenueEngineOutput,
    RevenueProjectionYear,
    RevenueSegment,
    ReturnsEngineInput,
    ReturnsEngineOutput,
    ScenarioName,
    SegmentYear,
    SourceUseLine,
)
from .str_forecast import (
    CoverageQuality,
    STRForecastResult,
    STRForecastScenario,
    STRForecastScenarioName,
    STRMonth,
)
from .variance import VarianceFlag, VarianceReport
from .verification import (
    CitationStatus,
    VerificationCheck,
    VerificationReport,
)

__all__ = [
    # analysis
    "AnalysisReport",
    "Insight",
    "RiskAssessment",
    "RiskCategory",
    "RiskCategoryName",
    "RiskTier",
    "ScenarioSummary",
    # broker_qa (Wave 1 #5 — seller Q&A re-ingestion)
    "BrokerQAPair",
    "ProposedOverride",
    "ProposedOverrideConfidence",
    "ResolverVerdict",
    # broker_question
    "BrokerQuestion",
    # common
    "ModelCall",
    "Money",
    "Risk",
    "Severity",
    "TenantScoped",
    # comp_sales (Wave 3 W3.1 — Comparable Sales engine)
    "CompSalesSet",
    "CompTransaction",
    # confidence
    "ConfidenceReport",
    # cost
    "AgentCost",
    "DealCostReport",
    # critic
    "CriticFinding",
    "CriticReport",
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
    "FoodBeverageDetail",
    "LaborByDepartment",
    "ModelAssumptions",
    "USALIFinancials",
    "UndistributedExpenses",
    "UtilitiesDetail",
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
    "SourceRegion",
    # partnership
    "PartnerReturn",
    "PartnershipInput",
    "PartnershipOutput",
    "WaterfallTier",
    # scenario (Wave 3 W3.2 — named what-if scenarios)
    "Scenario",
    "ScenarioOverride",
    # underwriting
    "CapexPlan",
    "CapexScheduleYear",
    "CashFlowEngineInput",
    "CashFlowEngineOutput",
    "CashFlowYear",
    "DebtEngineInput",
    "DebtEngineOutput",
    "DebtServiceYear",
    "InvestmentEngineInput",
    "InvestmentEngineOutput",
    "NonPIPCapex",
    "PIPCapex",
    "PLEngineInput",
    "PLEngineOutput",
    "ROICapex",
    "ALLOWED_SEGMENT_NAMES",
    "RevenueEngineInput",
    "RevenueEngineOutput",
    "RevenueProjectionYear",
    "RevenueSegment",
    "ReturnsEngineInput",
    "ReturnsEngineOutput",
    "ScenarioName",
    "SegmentYear",
    "SourceUseLine",
    # str_forecast (Wave 3 W3.3 — STR forward forecast)
    "CoverageQuality",
    "STRForecastResult",
    "STRForecastScenario",
    "STRForecastScenarioName",
    "STRMonth",
    # variance
    "VarianceFlag",
    "VarianceReport",
    # verification
    "CitationStatus",
    "VerificationCheck",
    "VerificationReport",
]

__version__ = "0.1.0"
