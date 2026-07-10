"""Lazy narrative generation for engine outputs (TASK T5, 2026-07).

Instead of calling the Analyst agent immediately after every engine run
(4 engines × analysts = expensive, wasteful), narratives are generated
on first read and cached in the ``engine_outputs`` row.

Contract:
  get_or_generate_narrative(session, engine_output_id, engine_name, math_payload)
    → If row.narrative is not NULL, return it immediately (cached).
    → Else: call Analyst with math, store result, return it.
    → On LLM failure, return a fallback message (exception-safe).

All narrative generation paths are gated by the
``LAZY_ENGINE_NARRATIVES_ENABLED`` config flag.

WIRING POINTS (future):
  When the UI needs narratives, call get_or_generate_narrative for each
  engine_output that has ``narrative=NULL``. Batch the calls (parallel
  for up to 4 engines) to avoid serial delays:

    app/api/model.py::get_engine_output() — add ``include_narrative: bool = False``
    query param; if True, call get_or_generate_narrative before returning.

    app/api/model.py::list_engine_outputs() — same; batch call all engines
    with ``asyncio.gather(*[get_or_generate_narrative(...) for each])``

    app/dossier/builder.py — when assembling the dossier for a deal,
    populate narratives on engine outputs that need them (cost-justified
    when exporting PDFs or when analysts request them).

  The Analyst call signature is lightweight and exception-safe:
    await get_or_generate_narrative(session, uuid_id, "returns", math_dict, tenant_id)
  No refactoring of the existing Analyst agent (app/agents/analyst.py) needed.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings

logger = logging.getLogger(__name__)


FALLBACK_NARRATIVE = (
    "Narrative generation encountered an error. "
    "Please review the engine outputs and key metrics manually."
)

# Static persona for the narrative call. Kept as a module constant so it is
# byte-identical across calls and therefore eligible for Anthropic prompt
# caching when routed through ``cached_system_message_blocks`` (see
# ``_generate_narrative_via_analyst``).
NARRATIVE_SYSTEM_PROMPT = (
    "You are a senior hotel acquisitions analyst. Given a financial engine's "
    "numeric output, write a concise 1-2 sentence narrative explaining what "
    "the numbers mean in plain English for the deal. Focus on the headline "
    "metrics and their implications. Omit technical jargon and citations, and "
    "keep it under 200 words."
)


def _content_to_text(msg: Any) -> str:
    """Extract plain text from a LangChain ``AIMessage`` (or its ``content``).

    Anthropic / Opus responses frequently arrive as a *list* of content-block
    dicts (``[{"type": "text", "text": "..."}]``) rather than a bare string.
    A naive ``isinstance(content, str)`` check therefore misses the common
    case and callers fall back to ``str(msg)`` — persisting the full
    ``AIMessage`` repr as the narrative. This helper mirrors the extraction
    pattern used in ``app/agents/extractor.py`` (no shared helper exists to
    import): return the concatenated text of the text-type blocks, and never
    return the message repr.
    """
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    # Neither str nor a content-block list — return empty rather than the
    # AIMessage repr so a malformed response never gets stored verbatim.
    return ""


async def get_or_generate_narrative(
    session: AsyncSession,
    engine_output_id: UUID,
    engine_name: str,
    math_payload: dict[str, Any],
    tenant_id: str,
) -> str:
    """Get or generate the narrative for an engine output.

    If the row already has a cached narrative, return it immediately.
    Otherwise, call the Analyst agent with just the math (lightweight
    prompt, ~$0.02), store the result, and return it.

    Args:
        session: Database session.
        engine_output_id: UUID of the engine_outputs row.
        engine_name: Name of the engine (e.g., 'returns', 'expense').
        math_payload: The engine's ``outputs`` JSONB (numbers, metrics).
        tenant_id: Tenant ID for scope. Required — ``engine_outputs`` is a
            tenant-scoped table, so every query MUST carry a tenant predicate
            or the tenant middleware trips a CRITICAL.

    Returns:
        The narrative string (either cached or freshly generated).
        On LLM failure, returns a fallback message.
    """
    settings = get_settings()

    # If lazy narratives are disabled, return fallback immediately
    # (avoids any LLM call; helps with debugging / toggle testing).
    if not settings.LAZY_ENGINE_NARRATIVES_ENABLED:
        return FALLBACK_NARRATIVE

    # Try to fetch the cached narrative from the row.
    try:
        result = await session.execute(
            text(
                """
                SELECT narrative, narrative_generated_at
                  FROM engine_outputs
                 WHERE id = :id
                   AND tenant_id = :tenant
                """
            ),
            {"id": str(engine_output_id), "tenant": tenant_id},
        )
        row = result.first()
        if row is None:
            logger.warning(
                f"engine_output_id {engine_output_id} not found in DB"
            )
            return FALLBACK_NARRATIVE

        narrative, generated_at = row[0], row[1]
        if narrative is not None:
            # Cache hit — return immediately.
            logger.debug(
                f"engine_output {engine_output_id}: narrative cached "
                f"(generated {generated_at})"
            )
            return narrative

    except Exception as e:
        logger.exception(f"Error fetching cached narrative: {e}")
        return FALLBACK_NARRATIVE

    # Cache miss — generate the narrative via the Analyst agent.
    try:
        narrative = await _generate_narrative_via_analyst(
            engine_name, math_payload
        )
    except Exception as e:
        logger.exception(
            f"Error generating narrative for engine {engine_name}: {e}"
        )
        return FALLBACK_NARRATIVE

    # Store the generated narrative and timestamp in the row.
    try:
        now = datetime.now(UTC).isoformat()
        await session.execute(
            text(
                """
                UPDATE engine_outputs
                   SET narrative = :narrative,
                       narrative_generated_at = :ts
                 WHERE id = :id
                   AND tenant_id = :tenant
                """
            ),
            {
                "id": str(engine_output_id),
                "tenant": tenant_id,
                "narrative": narrative,
                "ts": now,
            },
        )
        await session.commit()
        logger.debug(
            f"engine_output {engine_output_id}: narrative cached "
            f"(generated at {now})"
        )
    except Exception as e:
        logger.exception(f"Error persisting narrative: {e}")
        # Continue anyway — return the generated narrative even if
        # persist fails (don't let storage bugs lose the LLM work).

    return narrative


async def _generate_narrative_via_analyst(
    engine_name: str, math_payload: dict[str, Any]
) -> str:
    """Call the Analyst agent with just the math, return narrative.

    Lightweight prompt: the Analyst doesn't need full deal context,
    OM / doc citations, or multi-section structure. Just the engine's
    outputs (headline numbers, metrics) get a 1-2 sentence narrative
    explaining what they mean.

    Args:
        engine_name: Name of the engine (e.g., 'returns', 'expense').
        math_payload: The engine's ``outputs`` dict (numbers, metrics).

    Returns:
        A 1-2 sentence narrative (str).

    Raises:
        Any LLM error propagates; callers wrap with try/except.
    """
    from langchain_core.messages import HumanMessage

    # Format the math payload as readable JSON.
    math_display = json.dumps(math_payload, indent=2, default=str)

    user_prompt = f"""A financial engine just produced the following \
{engine_name} output:

```json
{math_display}
```

Write the narrative now."""

    # Build a lightweight LLM client (no structured output, just plain text)
    # on the Analyst model (Opus) so the narrative is high-quality.
    from ..llm import build_llm, cached_system_message_blocks

    # Route the static persona through the shared cached_system_message_blocks
    # so the system prefix hits Anthropic's prompt cache on repeat calls — the
    # cost win this lazy-narrative design claims. We keep the persona minimal
    # rather than pulling the full analyst context: build_agent_system_blocks
    # adds the USALI rules / brand / schema blocks, which a 1-2 sentence numeric
    # gloss does not need.
    #
    # TODO(wave5): if narratives ever need deal/OM context, route the whole
    # call through the shared analyst plumbing (build_agent_system_blocks +
    # invoke_with_escalation) so it inherits the 4-block cache layout and
    # parse-escalation lane instead of this bare ainvoke + divergent persona.
    # invoke_with_escalation is structured-output only today, so it is not a
    # drop-in for a plain-text narrative.
    system_message = cached_system_message_blocks(
        [NARRATIVE_SYSTEM_PROMPT], role="analyst"
    )

    llm = build_llm(
        role="analyst",
        max_tokens=512,  # Lightweight — just 1-2 sentences
        timeout=30,
    )

    # Call the LLM and extract the text response. Opus content is frequently a
    # list of content-block dicts, so use the robust extractor rather than an
    # isinstance(str) check that would fall through to the AIMessage repr.
    result = await llm.ainvoke(
        [system_message, HumanMessage(content=user_prompt)]
    )
    text = _content_to_text(result)
    if not text:
        logger.warning(f"Empty/unexpected LLM response: {type(result)}")
    return text


__all__ = ["get_or_generate_narrative", "FALLBACK_NARRATIVE"]
