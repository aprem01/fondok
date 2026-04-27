"""LangGraph state machine for the deal pipeline.

Nodes
-----
route          → classify the incoming work (Router agent)
extract        → pull structured data from PDFs (Extractor agent)
normalize      → map onto Fondok chart of accounts (Normalizer)
gate1_review   → HITL Gate 1 (interrupt_before)
run_engines    → deterministic engine fan-out (revenue, F&B, …)
analyze        → Analyst drafts the IC memo
variance       → Variance agent flags sponsor vs model gaps
gate2_review   → HITL Gate 2 (interrupt_before)
finalize       → persists final state (stubbed)

Compile with a Postgres checkpointer in production; tests pass an
in-memory ``MemorySaver`` via ``build_graph(checkpointer=...)``.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .agents.analyst import AnalystInput, run_analyst
from .agents.extractor import ExtractorInput, run_extractor
from .agents.normalizer import NormalizerInput, run_normalizer
from .agents.router import RouterInput, run_router
from .agents.variance import VarianceInput, run_variance
from .budget import BudgetExceededError, check_budget
from .config import get_settings
from .state import DealState

logger = logging.getLogger(__name__)


# ─────────────────────── nodes ───────────────────────


async def node_route(state: DealState) -> DealState:
    """Run the Router to classify the incoming work."""
    deal_id = state.get("deal_id")
    tenant_id = state.get("tenant_id")
    if not (deal_id and tenant_id):
        return {"errors": [*state.get("errors", []), "route: missing deal/tenant"]}
    payload = RouterInput(tenant_id=tenant_id, deal_id=deal_id)
    out = await run_router(payload)
    existing = state.get("model_calls") or []
    return {"model_calls": [*existing, *out.model_calls]}


async def node_extract(state: DealState) -> DealState:
    """Run the Extractor agent on the deal's source documents."""
    deal_id = state.get("deal_id")
    tenant_id = state.get("tenant_id")
    if not (deal_id and tenant_id):
        return {"errors": [*state.get("errors", []), "extract: missing deal/tenant"]}
    try:
        check_budget(dict(state), stage="extract")
    except BudgetExceededError as exc:
        return {"errors": [*state.get("errors", []), f"extract: {exc}"]}

    payload = ExtractorInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_uris=state.get("source_uris") or [],
    )
    out = await run_extractor(payload)
    existing = state.get("model_calls") or []
    return {
        "extracted_documents": out.extracted_documents,
        "model_calls": [*existing, *out.model_calls],
    }


async def node_normalize(state: DealState) -> DealState:
    """Run the Normalizer agent on the extracted documents."""
    deal_id = state.get("deal_id")
    tenant_id = state.get("tenant_id")
    if not (deal_id and tenant_id):
        return {"errors": [*state.get("errors", []), "normalize: missing deal/tenant"]}
    try:
        check_budget(dict(state), stage="normalize")
    except BudgetExceededError as exc:
        return {"errors": [*state.get("errors", []), f"normalize: {exc}"]}

    payload = NormalizerInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        extracted_documents=state.get("extracted_documents") or [],
    )
    out = await run_normalizer(payload)
    existing = state.get("model_calls") or []
    return {
        "normalized_spread": out.normalized_spread,
        "model_calls": [*existing, *out.model_calls],
    }


async def node_gate1_review(state: DealState) -> DealState:
    """HITL Gate 1 — analyst reviews the normalized spread.

    The graph interrupts BEFORE this node; when the API resumes with a
    decision we apply it here.
    """
    decision = state.get("gate1_decision")
    if decision is None:
        return {}
    logger.info("gate1: deal=%s decision applied", state.get("deal_id"))
    return {}


async def node_run_engines(state: DealState) -> DealState:
    """Fan out the deterministic engines on the locked spread.

    Phase 2: returns an empty results dict. Phase 3 wires the real
    engine instances under ``app/engines/`` and aggregates outputs.
    """
    deal_id = state.get("deal_id")
    if not deal_id:
        return {"errors": [*state.get("errors", []), "run_engines: missing deal_id"]}
    return {"engine_results": {}}


async def node_analyze(state: DealState) -> DealState:
    """Run the Analyst agent to draft the IC memo."""
    deal_id = state.get("deal_id")
    tenant_id = state.get("tenant_id")
    if not (deal_id and tenant_id):
        return {"errors": [*state.get("errors", []), "analyze: missing deal/tenant"]}
    try:
        check_budget(dict(state), stage="analyze")
    except BudgetExceededError as exc:
        return {"errors": [*state.get("errors", []), f"analyze: {exc}"]}

    payload = AnalystInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        normalized_spread=state.get("normalized_spread"),
        engine_results=state.get("engine_results") or {},
    )
    out = await run_analyst(payload)
    existing = state.get("model_calls") or []
    return {
        "analyst_memo": out.memo,
        "model_calls": [*existing, *out.model_calls],
    }


async def node_variance(state: DealState) -> DealState:
    """Run the Variance agent to flag sponsor vs underwriter gaps."""
    deal_id = state.get("deal_id")
    tenant_id = state.get("tenant_id")
    if not (deal_id and tenant_id):
        return {"errors": [*state.get("errors", []), "variance: missing deal/tenant"]}
    try:
        check_budget(dict(state), stage="variance")
    except BudgetExceededError as exc:
        return {"errors": [*state.get("errors", []), f"variance: {exc}"]}

    payload = VarianceInput(tenant_id=tenant_id, deal_id=deal_id)
    out = await run_variance(payload)
    existing = state.get("model_calls") or []
    return {
        "variance_report": {"flags": out.flags},
        "model_calls": [*existing, *out.model_calls],
    }


async def node_gate2_review(state: DealState) -> DealState:
    """HITL Gate 2 — analyst signs off on the memo + variance report."""
    decision = state.get("gate2_decision")
    if decision is None:
        return {}
    logger.info("gate2: deal=%s decision applied", state.get("deal_id"))
    return {}


async def node_finalize(state: DealState) -> DealState:
    """Persist final state. Stub — API layer owns DB commits for now."""
    logger.info(
        "finalize: deal=%s memo=%s variance=%s",
        state.get("deal_id"),
        state.get("analyst_memo") is not None,
        state.get("variance_report") is not None,
    )
    return {}


# ─────────────────────── graph builder ───────────────────────


def build_graph(checkpointer: Any | None = None) -> Any:
    """Build and compile the deal graph.

    Pass ``checkpointer=MemorySaver()`` for tests; production callers
    use ``run_deal`` which wires the Postgres saver.
    """
    workflow: StateGraph = StateGraph(DealState)
    workflow.add_node("route", node_route)
    workflow.add_node("extract", node_extract)
    workflow.add_node("normalize", node_normalize)
    workflow.add_node("gate1_review", node_gate1_review)
    workflow.add_node("run_engines", node_run_engines)
    workflow.add_node("analyze", node_analyze)
    workflow.add_node("variance", node_variance)
    workflow.add_node("gate2_review", node_gate2_review)
    workflow.add_node("finalize", node_finalize)

    workflow.set_entry_point("route")
    workflow.add_edge("route", "extract")
    workflow.add_edge("extract", "normalize")
    workflow.add_edge("normalize", "gate1_review")
    workflow.add_edge("gate1_review", "run_engines")
    workflow.add_edge("run_engines", "analyze")
    workflow.add_edge("analyze", "variance")
    workflow.add_edge("variance", "gate2_review")
    workflow.add_edge("gate2_review", "finalize")
    workflow.add_edge("finalize", END)

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(
        checkpointer=saver,
        interrupt_before=["gate1_review", "gate2_review"],
    )


# ─────────────────────── top-level drivers ───────────────────────


async def run_deal(
    deal_id: str,
    *,
    tenant_id: str,
    source_uris: list[str] | None = None,
) -> DealState:
    """Start the graph for a deal. Runs up to the first interrupt."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    settings = get_settings()
    async with AsyncPostgresSaver.from_conn_string(settings.sync_database_url) as saver:
        graph = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": deal_id}}
        initial: DealState = {
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "source_uris": source_uris or [],
            "model_calls": [],
            "errors": [],
        }
        final_state: DealState = await graph.ainvoke(initial, config=config)  # type: ignore[assignment]
        return final_state


async def resume_deal(
    deal_id: str,
    *,
    gate1_decision: Any | None = None,
    gate2_decision: Any | None = None,
) -> DealState:
    """Resume a paused graph after a HITL gate."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    settings = get_settings()
    async with AsyncPostgresSaver.from_conn_string(settings.sync_database_url) as saver:
        graph = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": deal_id}}
        update: DealState = {}
        if gate1_decision is not None:
            update["gate1_decision"] = gate1_decision
        if gate2_decision is not None:
            update["gate2_decision"] = gate2_decision
        if update:
            await graph.aupdate_state(config, update)
        final_state: DealState = await graph.ainvoke(None, config=config)  # type: ignore[assignment]
        return final_state


__all__ = [
    "build_graph",
    "node_analyze",
    "node_extract",
    "node_finalize",
    "node_gate1_review",
    "node_gate2_review",
    "node_normalize",
    "node_route",
    "node_run_engines",
    "node_variance",
    "resume_deal",
    "run_deal",
]
