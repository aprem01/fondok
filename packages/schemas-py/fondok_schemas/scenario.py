"""Named scenarios — save/load/diff what-if scenarios per deal.

Wave 3 W3.2. Every IC committee asks "what's the upside? what's the
downside?" Today Fondok models a single point estimate; analysts copy
the deal, mess with assumptions, screenshot the outputs, and paste
them into PowerPoint. A scenario is a *named* layer of overrides on
top of the deal's persisted ``field_overrides``; the engine runs with
the scenario applied without disturbing the base deal.

The override shape mirrors ``FieldOverrideRecord``
(apps/worker/app/api/deals.py) so a scenario flexing ``exit_cap_rate``
or ``pip_displacement.brand`` flows through the exact same
engine_runner routing the persisted deal overrides already use. A
scenario's *reason* lives at the scenario level (``description``), not
per-field, so an analyst can drop a "downside" scenario without
writing a note for every override row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioOverride(BaseModel):
    """One per-field override inside a scenario.

    ``field_path`` is the same canonical extractor path the deal-level
    ``field_overrides`` use (``exit_cap_rate``, ``starting_occupancy``,
    ``pip_displacement.brand``, ``segments.transient_ota.adr`` …).
    ``source`` defaults to ``analyst_override`` — the assumption-source
    badge keeps reading "analyst override" when a scenario is active.
    """

    model_config = ConfigDict(extra="forbid")

    field_path: Annotated[str, Field(min_length=1, max_length=200)]
    # Scalars + JSON-compatible structures (list for the PIP monthly
    # schedule). Mirrors the per-key value shape the engine_runner
    # routing already understands.
    value: Any
    source: Annotated[str, Field(min_length=1, max_length=40)] = "analyst_override"


class Scenario(BaseModel):
    """A named what-if layer on top of a deal.

    Every deal has exactly one ``is_base=True`` scenario (auto-created
    on deal insert) plus any number of analyst-defined scenarios.
    Running the engine with ``scenario_id`` set merges the scenario's
    overrides on top of the deal's persisted ``field_overrides`` and
    feeds the result to the engine chain.

    ``last_run_id`` is the ``engine_outputs.run_id`` of the most recent
    engine run that applied this scenario. The UI uses it to deep-link
    back to the run results without re-running the math.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    deal_id: str
    tenant_id: str
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    is_base: bool = False
    overrides: list[ScenarioOverride] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    last_run_id: str | None = None


__all__ = ["Scenario", "ScenarioOverride"]
