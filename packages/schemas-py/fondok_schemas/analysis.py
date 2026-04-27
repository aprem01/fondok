"""Analysis tab — risk scoring, insights, scenario summaries."""

from __future__ import annotations

from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RiskTier(str, Enum):
    LOW = "Low Risk"
    MEDIUM = "Medium Risk"
    HIGH = "High Risk"


class RiskCategoryName(str, Enum):
    OVERALL = "Overall"
    REVPAR_VOLATILITY = "RevPAR Volatility"
    MARKET_SUPPLY = "Market Supply Risk"
    OPERATOR = "Operator Risk"
    CAPITAL_NEEDS = "Capital Needs"
    DEBT = "Debt Risk"
    BRAND = "Brand Risk"


class RiskCategory(BaseModel):
    """One scored risk dimension."""

    model_config = ConfigDict(extra="forbid")

    name: RiskCategoryName
    tier: RiskTier
    score: Annotated[int, Field(ge=0, le=100)]
    note: str | None = None


class RiskAssessment(BaseModel):
    """Full risk decomposition for a deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    overall: RiskTier
    score: Annotated[int, Field(ge=0, le=100)]
    by_category: list[RiskCategory] = Field(default_factory=list)


class Insight(BaseModel):
    """One AI-surfaced insight on the Analysis tab."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1, max_length=4000)]


class ScenarioSummary(BaseModel):
    """Compact scenario row for the Analysis tab card."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    probability: Annotated[float, Field(ge=0.0, le=1.0)]
    irr: float
    coc: float
    multiple: Annotated[float, Field(ge=0)]
    exit_value: Annotated[float, Field(ge=0)]


class AnalysisReport(BaseModel):
    """Aggregate Analysis-tab payload for one deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    summary: list[str] = Field(default_factory=list)
    risks: RiskAssessment
    insights: list[Insight] = Field(default_factory=list)
    scenarios: list[ScenarioSummary] = Field(default_factory=list)
