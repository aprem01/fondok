"""Health endpoint — DB ping + version + boot-state invariants.

``GET /health`` is unauthenticated, so it carries only what a liveness probe
needs: ``status``, ``version``, ``db``, the boot invariants (USALI catalog
size, recognizer, concept-registry version) and ``degraded_reasons``. The
deployment description — storage bucket/region, auth posture, embeddings
model, per-tenant cache counters — moved to ``GET /admin/health``
(:mod:`app.api.admin_health`) on 2026-10-17: it is infrastructure and
tenant-enumeration disclosure on a public URL. :func:`ops_snapshot` builds
that block so both routes share one implementation.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..config import get_settings
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
    return await public_snapshot(session)


async def public_snapshot(session: AsyncSession) -> dict[str, Any]:
    """The unauthenticated probe body — no infrastructure or tenant detail."""
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
    # Phase 1.1 — concept registry boot invariant (same precedent as the
    # USALI catalog): -1 means the registry failed validation at startup.
    ontology_version = startup.get("ontology_version")
    if isinstance(ontology_version, int) and ontology_version < 0:
        degraded_reasons.append("ontology_invalid")

    # Storage backend probe — a misconfigured S3 cutover (env var didn't
    # propagate, boto3 missing, etc.) is a degraded reason here; WHICH store,
    # bucket and region is operator detail served by ``/admin/health``.
    if _raw_store_snapshot()["kind"].startswith("error:"):
        degraded_reasons.append("raw_store_init_failed")

    return {
        "status": "ok" if not degraded_reasons else "degraded",
        "version": __version__,
        "db": db_status,
        "usali_rules": rules_loaded,
        "structural_recognizer": recognizer_ok,
        "ontology_version": ontology_version,
        "degraded_reasons": degraded_reasons,
    }


def _raw_store_snapshot() -> dict[str, Any]:
    """Which RawStore class the worker instantiated (+ bucket / region)."""
    try:
        from ..storage import get_raw_store

        store = get_raw_store()
        return {
            "kind": type(store).__name__,
            "bucket": getattr(store, "bucket", None),
            "region": getattr(store, "region", None),
        }
    except Exception as exc:
        return {"kind": f"error:{type(exc).__name__}", "bucket": None, "region": None}


def auth_posture() -> dict[str, bool]:
    """The four auth flags. A public worker that honours a bare X-Tenant-Id is
    a cross-tenant hole (2026-10-06); ``jwt_required`` should read true on
    every deployed environment and false only on a developer's machine."""
    settings = get_settings()
    header_path = bool(getattr(settings, "ALLOW_TENANT_HEADER_WITHOUT_JWT", False))
    return {
        "header_tenant_path_enabled": header_path,
        "jwt_required": not header_path,
        # FON-93 — should read true on prod once the registry is seeded.
        "registered_tenant_required": bool(
            getattr(settings, "REQUIRE_REGISTERED_TENANT", False)
        ),
        # FON-93 — true once RLS is applied and the app role is in use.
        "rls_tenant_guc": bool(getattr(settings, "DB_SET_TENANT_GUC", False)),
    }


async def ops_snapshot(session: AsyncSession) -> dict[str, Any]:
    """The operator blocks ``GET /admin/health`` adds to the public body."""
    # Content-hash extraction cache metrics (Sam cost-opt 2026-07). A
    # per-tenant hit/miss/hit_rate breakdown so ops can eyeball how much
    # LLM spend the cache is deflecting. Process-local — restart zeros
    # the gauge; the long-term aggregate lives in extraction_results.
    extraction_cache: dict[str, Any] = {}
    try:
        from .documents import get_extraction_cache_metrics

        extraction_cache = get_extraction_cache_metrics()
    except Exception as exc:
        logger.warning("health: extraction cache metrics failed: %s", exc)
        extraction_cache = {"error": type(exc).__name__}

    # Semantic search (chunk store + Voyage embeddings). Both halves are
    # optional and BOTH fail open — a missing ``document_chunks`` table or an
    # absent VOYAGE_API_KEY silently degrades /deals/{id}/search to full-text
    # only. That is a defensible fallback but an indefensible mystery, so the
    # state is reported rather than inferred from behaviour. Not a degraded
    # reason: full-text-only is a supported mode, not a fault.
    semantic_search: dict[str, Any] = {}
    try:
        from ..extraction import embeddings as _emb
        from ..extraction.context_store import _table_exists

        embeddings_on = _emb.is_enabled()
        chunks_on = await _table_exists(session)
        semantic_search = {
            "chunk_store": chunks_on,
            "embeddings": embeddings_on,
            "model": _emb.VOYAGE_MODEL if embeddings_on else None,
            "mode": (
                "hybrid"
                if chunks_on and embeddings_on
                else "fulltext_only"
                if chunks_on
                else "unavailable"
            ),
        }
    except Exception as exc:
        logger.warning("health: semantic search probe failed: %s", exc)
        semantic_search = {"error": type(exc).__name__}

    read_cache: dict[str, Any] = {}
    try:
        from ..services.read_cache import read_cache_stats

        read_cache = read_cache_stats()
    except Exception as exc:
        read_cache = {"error": type(exc).__name__}

    return {
        "raw_store": _raw_store_snapshot(),
        "extraction_cache": extraction_cache,
        "read_cache": read_cache,
        "semantic_search": semantic_search,
        "auth": auth_posture(),
    }
