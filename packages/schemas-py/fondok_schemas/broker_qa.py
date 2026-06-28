"""Broker Q&A re-ingestion — the closed loop from a sent broker question
to the broker's emailed reply, run through the ``QA Resolver`` agent.

The agent reads (analyst's question + broker's reply + surrounding deal
context) and emits:

  * ``resolver_verdict``  — one of ``resolved`` / ``partially_resolved``
                            / ``still_concerning``
  * ``resolver_summary``  — a 1-2 sentence plain-English wrap of the
                            broker's reply (what the analyst reads next
                            to the verdict badge)
  * ``proposed_overrides`` — a list of ``ProposedOverride`` rows the
                            agent thinks should land in the engine inputs
                            (e.g. broker explained the F&B drop was due
                            to a kitchen closure → reset the FB margin
                            assumption back to pre-closure baseline)
  * ``audit_note``        — the IC memo footnote text. Surfaced from
                            ``run_analyst`` in the underwriting section
                            so a Brookfield reader can trace WHY an
                            assumption was overridden

Trust model (Wave 1 decision — never deviate):

  Proposed overrides are NEVER auto-applied. The analyst confirms each
  one through the ``apply_proposed_overrides`` endpoint, which merges
  the confirmed subset into ``deals.field_overrides`` as structured
  ``FieldOverrideRecord`` rows (note populated from the agent's
  ``rationale`` field). This preserves IC memo defensibility.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ResolverVerdict = Literal["resolved", "partially_resolved", "still_concerning"]
ProposedOverrideConfidence = Literal["high", "medium", "low"]


class ProposedOverride(BaseModel):
    """One engine-input override the QA Resolver agent proposes.

    Persists as one entry inside ``broker_qa_pairs.proposed_overrides``
    (JSONB on Postgres / TEXT on SQLite). When the analyst confirms a
    proposed override, the apply endpoint copies the chosen subset into
    ``broker_qa_pairs.applied_overrides`` AND merges a structured
    ``FieldOverrideRecord`` (path → value, with ``rationale`` as the
    note) into the deal's ``field_overrides`` JSONB so engines see it
    on the next ``/run-all`` invocation.

    ``field_path`` MUST be one of the canonical extractor field paths
    the agent is allow-listed to propose overrides on. See
    ``apps.worker.app.agents.qa_resolver.ALLOWED_OVERRIDE_PATHS`` for
    the curated set (~30 most-important assumptions).
    """

    model_config = ConfigDict(extra="forbid")

    field_path: Annotated[str, Field(min_length=1, max_length=240)]
    value: float | str
    rationale: Annotated[str, Field(min_length=1, max_length=1000)]
    confidence: ProposedOverrideConfidence


class BrokerQAPair(BaseModel):
    """One round-trip Q&A loop — analyst question + broker reply + agent verdict.

    ``analyst_question`` is a snapshot of the broker question text at
    the time the analyst sent it (so subsequent refresh runs of the
    historical-variance engine can rewrite the parent ``broker_questions``
    row without breaking provenance).

    ``broker_response`` is the raw pasted email/excerpt; we never edit
    it. ``resolver_*`` fields are populated by the QA Resolver agent.
    ``applied_overrides`` stays ``None`` until the analyst confirms a
    subset via the ``apply`` endpoint — distinct from an empty list,
    which means "the analyst explicitly skipped all proposed overrides".
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    tenant_id: UUID
    broker_question_id: UUID
    analyst_question: Annotated[str, Field(min_length=1, max_length=2000)]
    broker_response: Annotated[str, Field(min_length=1, max_length=8000)]
    resolver_verdict: ResolverVerdict | None = None
    resolver_summary: Annotated[str, Field(max_length=2000)] | None = None
    proposed_overrides: list[ProposedOverride] = Field(default_factory=list)
    applied_overrides: list[ProposedOverride] | None = None
    audit_note: Annotated[str, Field(max_length=2000)] | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "BrokerQAPair",
    "ProposedOverride",
    "ProposedOverrideConfidence",
    "ResolverVerdict",
]
