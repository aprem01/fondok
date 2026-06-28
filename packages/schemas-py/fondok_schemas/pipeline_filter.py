"""Saved pipeline filters + scheduled digests (Wave 4 W4.5).

W3.5 shipped the multi-deal Pipeline view — sortable table, filter
bar, portfolio KPI strip. Analysts open it dozens of times per day to
apply the SAME filter set. And executives want a daily/weekly Slack
or email summary of pipeline state without logging in.

This module models two persisted, tenant-scoped artifacts:

* **SavedPipelineView** — a named filter the analyst can recall from
  the pipeline page in one click. ``is_owner_default=True`` pins the
  view as the actor's default landing filter on ``/pipeline``.
* **PipelineDigestSchedule** — a recurring Slack / email pipeline
  summary. Cadence is daily / weekly / monthly. Schedules can
  reference a saved view so the digest applies the same filter the
  analyst sees in the UI.

The filter shape (:class:`PipelineFilter`) is the single source of
truth for both: the pipeline page applies it client-side after the
``/deals/pipeline`` pull, and ``fondok_worker.services.pipeline_digest``
applies it server-side when composing a digest payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class PipelineFilter(BaseModel):
    """Filter predicate over a pipeline snapshot.

    Every field is optional — an empty filter matches every row in the
    snapshot. We mirror the same fields the ``/deals/pipeline`` query
    string already supports (state, min_irr, etc.) plus a couple of
    multi-select extensions (``chain_scales``, a multi-select ``state``
    list) the analyst's saved-view UI surfaces.

    ``sort`` is a token from
    ``fondok_worker.services.pipeline.SORT_KEYS``. Unknown tokens fall
    back to the default in the pipeline service so a stale saved view
    doesn't 500.
    """

    model_config = ConfigDict(extra="forbid")

    state: list[str] | None = None  # e.g. ["VALIDATING", "READY"]
    min_irr: float | None = None
    max_irr: float | None = None
    min_per_key: float | None = None
    max_per_key: float | None = None
    max_cap_rate: float | None = None
    chain_scales: list[str] | None = None
    sort: str = "last_activity_desc"


class SavedPipelineView(BaseModel):
    """A named filter + sort preset persisted per tenant.

    ``is_owner_default`` is enforced unique-per-(tenant, actor) at the
    API layer: pinning a new default unpins the previous one in the
    same transaction. ``created_by`` is the actor id (Clerk user /
    service principal). When the actor isn't available we stamp
    ``"system"`` so the row still carries a non-NULL owner.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    filter: PipelineFilter
    is_owner_default: bool = False
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class PipelineDigestSchedule(BaseModel):
    """A recurring pipeline summary delivered by Slack / email / both.

    Cadence semantics
    -----------------
    * ``daily`` — fires every day at ``hour_utc``. ``weekday`` is
      ignored.
    * ``weekly`` — fires on ``weekday`` (0 = Monday … 6 = Sunday) at
      ``hour_utc``. Missing ``weekday`` defaults to Monday.
    * ``monthly`` — fires on the 1st of each month at ``hour_utc``.

    The in-process scheduler ticks every 60 seconds and dispatches any
    schedule whose computed ``next_run_at`` is in the past. Prod
    deployments should swap the in-process loop for a real scheduler
    (Celery beat / SQS-driven cron) — see
    ``fondok_worker.services.digest_scheduler`` for the contract.

    Delivery
    --------
    * ``slack`` — POSTs the Block Kit payload to ``slack_webhook_url``.
      Falls back silently when the field is empty (so an analyst can
      pause delivery without deleting the schedule).
    * ``email`` — sends HTML email to ``email_recipients`` via the
      configured backend (``EMAIL_BACKEND``). The ``log_only`` default
      backend logs the email and returns success — useful for dev /
      CI.
    * ``both`` — fires both channels; one channel failing never blocks
      the other.

    Includes
    --------
    Four boolean toggles control payload composition:

    * ``include_kpi_summary`` — the headline KPI block (deal count,
      median IRR, deals meeting target).
    * ``include_recently_mutated`` — deals updated within the
      cadence-derived lookback (24h / 7d / 30d).
    * ``include_deals_meeting_target`` — top 5 deals by IRR that
      cleared their target.
    * ``include_full_table`` — appends the full filtered pipeline as a
      table block (Slack: a code block; email: an HTML ``<table>``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    name: Annotated[str, Field(min_length=1, max_length=120)]
    saved_view_id: str | None = None
    cadence: Literal["daily", "weekly", "monthly"] = "daily"
    weekday: Annotated[int, Field(ge=0, le=6)] | None = None
    hour_utc: Annotated[int, Field(ge=0, le=23)] = 13  # 9am ET ~ 13 UTC
    delivery: Literal["slack", "email", "both"] = "slack"
    slack_webhook_url: SecretStr | None = None
    # Plain ``str`` rather than ``EmailStr`` so the package stays free
    # of the ``email-validator`` dependency. API layer enforces a basic
    # ``a@b`` shape before persisting.
    email_recipients: list[str] = Field(default_factory=list)
    include_kpi_summary: bool = True
    include_recently_mutated: bool = True
    include_deals_meeting_target: bool = True
    include_full_table: bool = False
    is_active: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "PipelineFilter",
    "SavedPipelineView",
    "PipelineDigestSchedule",
]
