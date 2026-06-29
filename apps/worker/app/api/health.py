"""Health endpoint — DB ping + version + boot-state invariants."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..database import get_session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Liveness + readiness probe.

    ``status``: ``ok`` when DB pings AND every boot invariant is
    populated; ``degraded`` when the worker is up but a startup
    invariant is missing (Sam QA 2026-06-29 — missing USALI catalog
    used to silently produce score=null on every doc; now it shows
    here as ``usali_rules: 0`` and ``status: degraded``).
    """
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("health: db check failed: %s", exc)
        db_status = "fail"

    # Pull startup-state snapshot from app.main. Imported here (not at
    # module load) so test code that instantiates this router without
    # the full lifespan doesn't trip on the import.
    try:
        from ..main import get_startup_state

        startup = get_startup_state()
    except Exception:
        startup = {}

    rules_loaded = startup.get("usali_rules_loaded")
    recognizer_ok = startup.get("structural_recognizer_available")
    degraded_reasons: list[str] = []
    if db_status != "ok":
        degraded_reasons.append("db")
    if rules_loaded is None:
        degraded_reasons.append("usali_rules_not_probed")
    elif isinstance(rules_loaded, int) and rules_loaded <= 0:
        degraded_reasons.append("usali_catalog_missing")
    if recognizer_ok is False:
        degraded_reasons.append("structural_recognizer_unavailable")

    return {
        "status": "ok" if not degraded_reasons else "degraded",
        "version": __version__,
        "db": db_status,
        "usali_rules": rules_loaded,
        "structural_recognizer": recognizer_ok,
        "degraded_reasons": degraded_reasons,
    }
