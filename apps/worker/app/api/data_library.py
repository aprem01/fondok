"""Data-library endpoints — shared assumption / comp / brand library."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class LibraryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/entries", response_model=list[LibraryEntry])
async def list_entries() -> list[LibraryEntry]:
    """Stub: returns an empty library."""
    return []


@router.get("/entries/{entry_id}", response_model=LibraryEntry)
async def get_entry(entry_id: str) -> LibraryEntry:
    """Stub."""
    return LibraryEntry(id=entry_id, category="stub", label=entry_id)
