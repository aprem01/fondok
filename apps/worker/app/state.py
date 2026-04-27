"""Shared LangGraph state for the deal pipeline.

Kept in its own module so agent files can import ``DealState`` without
pulling in the full graph wiring (and avoids circular imports).
"""

from __future__ import annotations

from typing import Any, TypedDict


class DealState(TypedDict, total=False):
    """LangGraph state passed between every node.

    ``total=False`` lets each node write incrementally — most nodes
    return a dict containing only the keys they touched.

    Real schemas land here when ``fondok_schemas`` grows the agent
    envelopes (Phase 3). For the scaffold we hold the loose shape.
    """

    # Identity
    deal_id: str
    tenant_id: str

    # Inputs
    source_uris: list[str]

    # Stage outputs
    extracted_documents: list[Any]
    normalized_spread: Any | None
    analyst_memo: Any | None
    variance_report: Any | None

    # Engine outputs (revenue, F&B, expense, capital, debt, returns, …)
    engine_results: dict[str, Any]

    # HITL gates
    gate1_decision: Any | None
    gate2_decision: Any | None

    # Bookkeeping
    model_calls: list[Any]
    errors: list[str]
