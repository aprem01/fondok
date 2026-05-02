"""Typed Pydantic shapes for the Deal Dossier.

Kept narrow on purpose — every field has a downstream consumer (the
Analyst prompt, the Researcher Q&A agent, or the export package).
Adding a field to the dossier means committing to keep it accurate
across consumers; resist the urge to dump everything we know.

Each numeric field carries provenance (``source``) and a confidence
hint when one exists. The dossier is what makes the deal a "Context
Data Product" — citations + confidence aren't optional metadata,
they're first-class on every value an agent can read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DossierCitation(BaseModel):
    """One pointer back to the source document + page for a value."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    document_type: str | None = None
    filename: str | None = None
    page: Annotated[int, Field(ge=1)] | None = None
    field: str | None = Field(
        default=None,
        description="Extractor field_name the value was lifted from.",
    )
    excerpt: str | None = Field(
        default=None,
        description="Verbatim excerpt grounding the value (≤500 chars).",
    )


class DossierField(BaseModel):
    """A single typed value with source + confidence."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Canonical USALI / engine field path.")
    value: float | int | str | bool | None
    unit: str | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    source: Literal[
        "deals_table",
        "extraction",
        "engine_output",
        "kimpton_seed",
        "computed",
    ] = "extraction"
    citations: list[DossierCitation] = Field(default_factory=list)


class DossierDocument(BaseModel):
    """Inventory entry for one uploaded document."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    doc_type: str | None = None
    status: str
    page_count: int | None = None
    parser: str | None = None
    field_count: int = 0
    overall_confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    excerpts_by_page: dict[int, str] = Field(
        default_factory=dict,
        description=(
            "Per-page text snapshot (truncated). Lets the Q&A agent "
            "pull supporting text without re-parsing the PDF."
        ),
    )


class DossierEngine(BaseModel):
    """Latest persisted output from a single engine."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    summary: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)
    runtime_ms: int | None = None
    completed_at: datetime | None = None


class DossierVarianceFlag(BaseModel):
    """One broker vs T-12 variance flag, dossier-flat."""

    model_config = ConfigDict(extra="forbid")

    field: str
    rule_id: str | None = None
    severity: str
    actual: float | None = None
    broker: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    note: str | None = None
    citations: list[DossierCitation] = Field(default_factory=list)


class DossierConfidenceRollup(BaseModel):
    """Aggregate confidence across the dossier — a single number a
    reviewer can ask 'is this deal trustworthy?'."""

    model_config = ConfigDict(extra="forbid")

    avg_field_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    extracted_field_count: int = 0
    docs_extracted: int = 0
    docs_total: int = 0
    variance_critical_count: int = 0
    variance_warn_count: int = 0
    variance_info_count: int = 0
    has_t12_actuals: bool = False
    has_om: bool = False


class DealDossier(BaseModel):
    """Top-level Context Data Product for one deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    tenant_id: str
    deal: dict[str, Any] = Field(
        default_factory=dict,
        description="Property metadata from the deals row (name, city, "
        "keys, brand, service, deal_stage, return_profile, "
        "purchase_price, status).",
    )
    documents: list[DossierDocument] = Field(default_factory=list)
    spread_actuals: dict[str, Any] | None = Field(
        default=None,
        description="USALIFinancials snapshot from the latest T-12 "
        "extraction (when available).",
    )
    spread_broker: dict[str, Any] | None = Field(
        default=None,
        description="USALIFinancials snapshot from the OM broker "
        "proforma (when available).",
    )
    extracted_fields: list[DossierField] = Field(
        default_factory=list,
        description="Flat list of every extracted field across all "
        "documents on the deal, with citation back to source page.",
    )
    engines: list[DossierEngine] = Field(default_factory=list)
    variance: list[DossierVarianceFlag] = Field(default_factory=list)
    confidence: DossierConfidenceRollup = Field(
        default_factory=DossierConfidenceRollup
    )
    composed_at: datetime
    composer_version: str = "1"


__all__ = [
    "DealDossier",
    "DossierCitation",
    "DossierConfidenceRollup",
    "DossierDocument",
    "DossierEngine",
    "DossierField",
    "DossierVarianceFlag",
]
