"""Comparable Sales — extracted hotel transaction comps + derived cap rate.

Wave 3 W3.1. OMs almost always include a "Comparable Sales" or "Recent
Hotel Transactions" table with 5–15 historical sales: property name,
location, sale date, sale price, room count, NOI, cap rate, sale price
per key, brand / chain-scale. The Extractor agent pulls those rows from
the OM; this module is the structured Pydantic boundary the engine
runner consumes and the API hands back to the web UI.

A ``CompSalesSet`` carries both the raw transactions list and the
*derived* cap rate (median + weighted) — so the analyst can see how
the headline number was built and which comps fell out of the
calculation. Sam's #1 institutional-credibility question is "where
does your cap rate come from?"; the answer is the comparable
transactions table plus a transparent derivation method.

Source provenance: each ``CompTransaction`` carries the
``source_document_id`` and ``source_page_number`` that fed it, so the
UI badge can deep-link back to the OM page the row was lifted from.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── single comp row ─────────────────────────


class CompTransaction(BaseModel):
    """One row of the OM's Comparable Sales table.

    Every numeric field is optional — OMs frequently publish 5-15 comps
    with patchy coverage (e.g. cap rate only on 6 of 10, NOI only on 4).
    The engine handles partial rows by filtering for ``cap_rate_pct``
    presence at derivation time; the row is still preserved in the
    table view so the analyst sees the full comp universe.

    ``cap_rate_pct`` is a 0..100 percent (e.g. ``7.25`` for 7.25%), NOT
    a 0..1 fraction — that matches how OMs publish the number and how
    the rest of the comp_sales engine reasons about it. Conversion to a
    0..1 fraction happens at the engine_runner boundary when the
    derived cap rate is wired to ``exit_cap_rate``.
    """

    model_config = ConfigDict(extra="forbid")

    property_name: str | None = None
    city: str | None = None
    state: str | None = None
    sale_date: date | None = None
    keys: Annotated[int, Field(gt=0)] | None = None
    sale_price_usd: Annotated[float, Field(ge=0)] | None = None
    sale_price_per_key_usd: Annotated[float, Field(ge=0)] | None = None
    noi_usd: Annotated[float, Field(ge=0)] | None = None
    cap_rate_pct: Annotated[float, Field(ge=0.0, le=30.0)] | None = None
    chain_scale: str | None = None              # e.g. "upper-upscale"
    brand_family: str | None = None             # e.g. "Marriott", "Hilton"
    flag: str | None = None                     # e.g. "Courtyard by Marriott"
    source_document_id: str
    source_page_number: int | None = None
    note: str | None = None
    # Stable per-deal identifier for the row — used by the exclude-
    # transaction-ids override so an analyst clicking "exclude this
    # comp" in the UI can pin a single row without affecting siblings.
    transaction_id: str | None = None


# ─────────────────────────── derived comp set ────────────────────────


class CompSalesSet(BaseModel):
    """The full comp universe for a deal plus the derived cap rate.

    ``total_count`` is the count of raw transactions extracted (including
    excluded / out-of-lookback rows) — the UI's "X comps" headline. The
    derivation numbers reflect only the filtered subset.

    ``derived_cap_rate_method`` is the method the engine used to produce
    the headline anchor — ``"weighted"`` when the analyst-provided
    subject market / chain scale enabled the full formula,
    ``"median"`` when only the simple median could be computed,
    ``"none"`` when no comps survived the filter.

    ``coverage_quality`` is a 3-bucket label for IC consumption:
    - ``"high"``: ≥ 8 qualifying comps — institutional-grade anchor
    - ``"medium"``: 4-7 — usable, flag as moderate confidence
    - ``"low"``: < 4 — too thin to anchor exit cap on; analyst override
      strongly recommended

    Both ``derived_cap_rate_median`` and ``derived_cap_rate_weighted``
    are emitted as 0..100 percents (matching ``CompTransaction.cap_rate_pct``).
    """

    model_config = ConfigDict(extra="forbid")

    transactions: list[CompTransaction] = Field(default_factory=list)
    total_count: Annotated[int, Field(ge=0)] = 0
    derived_cap_rate_median: float | None = None
    derived_cap_rate_weighted: float | None = None
    derived_cap_rate_method: Literal["median", "weighted", "none"] = "none"
    weighting_notes: list[str] = Field(default_factory=list)
    coverage_quality: Literal["high", "medium", "low"] = "low"
    # Echo of the subject inputs the engine used to derive the weights
    # — gives the UI enough context to explain "weighted: same-MSA +
    # same chain-scale match dominated" without re-deriving.
    subject_market: str | None = None
    subject_chain_scale: str | None = None
    lookback_years: Annotated[int, Field(ge=1, le=20)] = 5


__all__ = [
    "CompSalesSet",
    "CompTransaction",
]
