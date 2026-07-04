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


async def get_or_generate_narrative(
    session: AsyncSession,
    engine_output_id: UUID,
    engine_name: str,
    math_payload: dict[str, Any],
    tenant_id: str | None = None,
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
        tenant_id: Tenant ID for scope (optional; inferred from DB if needed).

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
                """
            ),
            {"id": str(engine_output_id)},
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
                """
            ),
            {
                "id": str(engine_output_id),
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

    settings = get_settings()

    # Format the math payload as readable JSON.
    math_display = json.dumps(math_payload, indent=2, default=str)

    prompt = f"""You are a senior hotel acquisitions analyst.
A financial engine just produced the following {engine_name} output:

```json
{math_display}
```

Write a concise 1-2 sentence narrative explaining what these numbers mean
in plain English. Focus on the headline metrics and their implications for
the deal. Omit technical jargon and citations — keep it under 200 words.
"""

    # Build a lightweight LLM client (no structured output, just plain text).
    # Use the Analyst model (Opus) so the narrative is high-quality.
    from ..llm import build_llm

    llm = build_llm(
        role="analyst",
        max_tokens=512,  # Lightweight — just 1-2 sentences
        timeout=30,
    )

    # Call the LLM and extract the text response.
    result = await llm.ainvoke([HumanMessage(content=prompt)])

    # result.content is the text response from the LLM.
    if hasattr(result, "content"):
        text = result.content
        if isinstance(text, str):
            return text

    # Fallback if the response is malformed.
    logger.warning(f"Unexpected LLM response type: {type(result)}")
    return str(result)


__all__ = ["get_or_generate_narrative", "FALLBACK_NARRATIVE"]
