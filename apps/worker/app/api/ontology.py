"""``GET /ontology/concepts`` — the validated concept registry as JSON.

Public read (no tenant data, no auth — like ``/health``). The payload is the
same shape ``apps/worker/scripts/gen_ontology.py`` snapshots into
``apps/web/src/lib/ontology/concepts.snapshot.json``. When the registry
fails validation the endpoint answers 503 with the reason rather than
serving a stale or partial vocabulary; ``/health`` reports
``ontology_invalid`` in the same situation.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/concepts")
async def get_concepts() -> dict[str, Any]:
    # Imported here (not at module load) so a broken registry degrades this
    # endpoint + /health instead of taking the whole worker down at import.
    try:
        from ..ontology.registry import get_registry

        registry = get_registry()
    except Exception as exc:
        logger.exception("ontology: registry unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=f"ontology_invalid: {exc}") from exc
    return registry.dump()
