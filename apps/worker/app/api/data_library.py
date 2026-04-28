"""Data-library endpoints — shared assumption / comp / brand library.

TODO(data-library): the data library is a planned shared-knowledge
surface (operator brand defaults, comp-set templates, custom
assumption packs) but no backing schema exists yet. Both routes
return empty/stub responses so the web app can render a "coming
soon" panel without 404'ing. When the persistent ``library_entries``
table lands, the GET routes should pull rows from there filtered by
tenant.

The web app currently does not render this surface to end users; it
exists in the OpenAPI spec for forward-compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
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
    """Stub: returns an empty library.

    TODO(data-library): wire to ``library_entries`` table once the
    schema lands. Filter by tenant via ``X-Tenant-Id`` header.
    """
    return []


@router.get("/entries/{entry_id}", response_model=LibraryEntry)
async def get_entry(entry_id: str) -> LibraryEntry:
    """Stub: returns a placeholder envelope.

    Returning 404 would be more honest, but the web app is allowed to
    pre-fetch entries by id — keeping a 200 with an obviously-stub
    category lets the caller distinguish "not yet wired" from "real
    backend error". Flip to 404 once the table lands.
    """
    # Lazy-fail loud only when called with anything non-trivial so unit
    # tests can still exercise the wire shape.
    if not entry_id or entry_id == "_probe":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="data library not yet wired — see TODO(data-library)",
        )
    return LibraryEntry(id=entry_id, category="stub", label=entry_id)
