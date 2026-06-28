"""Portfolio P&L Library — firm-level benchmark entries.

Wave 4 W4.1 (Sam's June 2026 ask: "Apollo / capital partners own hotels
in the same market and want to upload THEIR P&Ls as benchmarks").

Wave 2 P2.7 added the ``PORTFOLIO_PNL`` doc type + the op-ratio
precedence chain that puts portfolio P&L between T-12 and CBRE, but
``PORTFOLIO_PNL`` lived per-deal (uploaded into one specific data room).
That's wrong — portfolio benchmarks are FIRM-LEVEL assets: they apply
across every deal that firm underwrites.

A ``PortfolioLibraryEntry`` represents one curated firm-level benchmark
(e.g. *"Apollo Select-Service Marriott 2024 portfolio"* — a roll-up of
op-ratios across the hotels Apollo owns of that chain-scale / vintage).
The engine resolves portfolio_pnl candidates per-deal by querying every
active library entry that matches the subject's chain scale + vintage
window, computing the median ratio across them, and feeding that median
into the precedence chain (``op_ratio_precedence``).

Backward compat: per-deal ``PORTFOLIO_PNL`` docs still work — they
take precedence over the library median for the same chain scale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class PortfolioLibraryEntry(BaseModel):
    """One firm-level benchmark roll-up in the Portfolio P&L Library.

    Tenant-scoped: every entry belongs to exactly one firm (tenant).
    The library is global across the firm's deals; the engine_runner
    pulls all entries that match the subject deal's chain scale within
    the vintage window and computes a median per ratio.

    ``expense_ratios`` keys mirror the canonical engine field names
    (``rooms_dept_pct``, ``fb_dept_pct``, ``admin_pct``, ``sales_pct``,
    ``utilities_pct``, ``property_tax_pct``, ``insurance_pct``,
    ``mgmt_fee_pct``, ``ffe_reserve_pct``, ``gop_margin``, ``noi_margin``,
    etc.). Values are decimals (0..1). Partial coverage is fine — the
    engine falls through to the next-lower precedence tier per missing
    field.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    vintage_year: Annotated[int, Field(ge=1900, le=2100)]
    asset_count: Annotated[int, Field(ge=1)]
    total_rooms_modeled: Annotated[int, Field(ge=1)]
    chain_scales_covered: list[str] = Field(default_factory=list)
    msa_coverage: list[str] | None = None
    expense_ratios: dict[str, float] = Field(default_factory=dict)
    revenue_mix: dict[str, float] | None = None
    source_document_id: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


__all__ = ["PortfolioLibraryEntry"]
