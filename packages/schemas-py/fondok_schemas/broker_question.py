"""Broker questions — YoY variance-driven follow-ups for the seller broker.

These rows are produced by the deterministic ``HistoricalVariance`` engine
(``apps/worker/app/engines/historical_variance.py``) — distinct from the
LLM-orchestrated ``variance.py`` agent that compares broker proforma vs
T-12 actuals. The historical engine walks consecutive years of the
property's OWN financials and emits a question per line-item whose YoY
swing crosses a USALI-aware threshold.

State machine
-------------

::

    pending → dismissed | sent → answered

``dismissed`` and ``answered`` are terminal; ``sent`` is the only state
that can advance further (to ``answered`` once the broker replies).
``broker_response`` is populated by the Q&A re-ingestion loop
(roadmap item #5) and only makes sense when state == ``answered``.

Eshan's June 25 framing (verbatim) was the spec for the question text
template: *"Look at this 15% swing in F&B month over month or year
over year, that could be a question that goes down to a broker."*
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BrokerQuestion(BaseModel):
    """One auto-generated broker question driven by a YoY variance.

    The deterministic engine emits one row per (line_item, period_key)
    pair whose absolute YoY change crosses ``threshold_pct``. The
    severity ladder is:

    * ``CRITICAL`` — ``abs(variance_pct) > 2 × threshold_pct``
    * ``WARN``     — ``abs(variance_pct) > threshold_pct``
    * ``INFO``     — reserved; not currently emitted (the engine drops
      sub-threshold rows entirely rather than persist noise)

    ``question_text`` is copy-paste-ready for the analyst to drop into
    a broker email; the same string is also rendered in the Validation
    tab. ``period_key`` is a stable id of the year pair the engine
    compared — typically ``"{prior_year}_vs_{current_year}"`` — and is
    part of the dedupe key used by the ``/refresh`` endpoint so re-runs
    don't double up rows.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    line_item: Annotated[str, Field(min_length=1, max_length=120)]
    period_key: Annotated[str, Field(min_length=1, max_length=60)]
    variance_pct: float
    actual_prior: float | None = None
    actual_current: float | None = None
    threshold_pct: float = Field(ge=0.0, le=1.0)
    severity: Literal["CRITICAL", "WARN", "INFO"]
    question_text: Annotated[str, Field(min_length=1, max_length=2000)]
    state: Literal["pending", "dismissed", "sent", "answered"] = "pending"
    dismissal_reason: Annotated[str, Field(max_length=2000)] | None = None
    broker_response: Annotated[str, Field(max_length=4000)] | None = None
    created_at: datetime
    updated_at: datetime


__all__ = ["BrokerQuestion"]
