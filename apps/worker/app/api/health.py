"""Health endpoint — DB ping + version."""

from __future__ import annotations

import logging
from typing import Annotated

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
) -> dict[str, str]:
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("health: db check failed: %s", exc)
        db_status = "fail"
    return {"status": "ok", "version": __version__, "db": db_status}
