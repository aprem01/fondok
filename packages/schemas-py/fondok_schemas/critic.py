"""Critic — cross-field narrative review of broker proforma vs T-12 vs market.

The Critic agent runs *after* the Variance agent has fired its per-field
deltas. Where Variance flags a single broker-vs-T12 number gap, the
Critic ties multiple fields together — the kind of story that requires
reading the proforma the way a senior IC reviewer would:

  * "Broker held insurance flat in a Florida coastal property — invisible
    at the field level, obvious in aggregate at $2,800/key vs $1,851/key."
  * "Broker assumed RevPAR up 8%, ADR flat, occupancy +500bps — these
    don't reconcile mathematically."
  * "Broker projected NOI margin expansion while OpEx ratio holds — needs
    a labor or revenue source that isn't in the narrative."

Every emitted ``CriticFinding`` MUST cite a ``rule_id`` from the USALI
catalog OR a ``MULTI_FIELD_*`` rule (the cross-field rules added to the
catalog alongside this agent). A grounding validator rejects any
finding pointing at an unknown rule_id (fail-closed).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .common import Severity


class CriticFinding(BaseModel):
    """One cross-field finding from the Critic.

    A finding always grounds in a known ``rule_id`` (USALI catalog or
    a MULTI_FIELD_* cross-field rule). ``cited_fields`` enumerates the
    canonical USALI field names involved — useful for the UI to draw
    chips and to deduplicate similar findings across re-runs.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    rule_id: Annotated[str, Field(min_length=1, max_length=120)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    narrative: Annotated[str, Field(min_length=1, max_length=2000)]
    severity: Severity
    cited_fields: list[str] = Field(default_factory=list)
    cited_pages: list[int] = Field(default_factory=list)
    cited_document_ids: list[UUID] = Field(default_factory=list)
    impact_estimate_usd: float | None = None


class CriticReport(BaseModel):
    """Bundle of CriticFindings for a single deal — feeds the Critic Review tab."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    findings: list[CriticFinding] = Field(default_factory=list)
    summary: Annotated[str, Field(max_length=2000)] | None = None
    critical_count: Annotated[int, Field(ge=0)] = 0
    warn_count: Annotated[int, Field(ge=0)] = 0
    info_count: Annotated[int, Field(ge=0)] = 0


__all__ = [
    "CriticFinding",
    "CriticReport",
]
