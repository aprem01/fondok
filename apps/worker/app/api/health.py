"""Health endpoint — DB ping + version + boot-state invariants.

``GET /health`` is unauthenticated, so it carries only what a liveness probe
needs: ``status``, ``version``, ``db``, the boot invariants (USALI catalog
size, recognizer, concept-registry version) and ``degraded_reasons``. The
deployment description — storage bucket/region, auth posture, embeddings
model, per-tenant cache counters — moved to ``GET /admin/health``
(:mod:`app.api.admin_health`) on 2026-10-17: it is infrastructure and
tenant-enumeration disclosure on a public URL. :func:`ops_snapshot` builds
that block so both routes share one implementation.

``build_sha`` / ``build_time`` (2026-10-30): on 2026-10-28 Railway served a
build 3.5 hours behind main and nothing on the probe could show it — the only
version field is the hand-maintained ``__version__``. :func:`build_info` reads
the deployed commit from ``RAILWAY_GIT_COMMIT_SHA`` (Railway runtime), else
``FONDOK_BUILD_SHA`` (Dockerfile ``ARG GIT_SHA``), else null, and the image
build time from the ``BUILD_TIME`` file the Dockerfile writes at build
(``FONDOK_BUILD_TIME`` in the environment overrides it; ``FONDOK_BUILD_TIME_FILE``
relocates the file — both for tests and non-Docker hosts), else null. A commit SHA is not
sensitive; the post-deploy smoke polls it to know the new worker is serving.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..config import get_settings
from ..database import get_session, migrations_dsn_kind

logger = logging.getLogger(__name__)
router = APIRouter()


# Written by apps/worker/Dockerfile at image build; absent on a checkout.
_DEFAULT_BUILD_TIME_FILE = Path(__file__).resolve().parents[4] / "BUILD_TIME"


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@lru_cache(maxsize=4)
def _read_build_time_file(path: str) -> str | None:
    try:
        return _clean(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None


def build_info() -> dict[str, str | None]:
    """``{build_sha, build_time}`` — see the module docstring for the sources.
    Blank values (an ``ARG`` that was never passed bakes an empty ``ENV``)
    read as null; nothing here is ever invented."""
    sha = _clean(os.environ.get("RAILWAY_GIT_COMMIT_SHA")) or _clean(
        os.environ.get("FONDOK_BUILD_SHA")
    )
    build_time = _clean(os.environ.get("FONDOK_BUILD_TIME")) or _read_build_time_file(
        os.environ.get("FONDOK_BUILD_TIME_FILE") or str(_DEFAULT_BUILD_TIME_FILE)
    )
    return {"build_sha": sha, "build_time": build_time}


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
    # FON-120 — methodology package boot invariant (the USALI precedent):
    # None = not probed yet, 0 = missing or invalid in this image.
    methodology_sections = startup.get("methodology_sections")
    if isinstance(methodology_sections, int) and methodology_sections <= 0:
        degraded_reasons.append("methodology_missing")

    # Storage backend probe — a misconfigured S3 cutover (env var didn't
    # propagate, boto3 missing, etc.) is a degraded reason here; WHICH store,
    # bucket and region is operator detail served by ``/admin/health``.
    if _raw_store_snapshot()["kind"].startswith("error:"):
        degraded_reasons.append("raw_store_init_failed")

    return {
        "status": "ok" if not degraded_reasons else "degraded",
        "version": __version__,
        # Which commit is serving — the post-deploy smoke's worker-deployed
        # job polls this until it equals the pushed SHA (2026-10-30).
        **build_info(),
        "db": db_status,
        # FON-93 — "owner" when DATABASE_URL_MIGRATIONS (a dedicated owner
        # DSN for startup DDL) is set, "same" when DDL runs on the serving
        # engine. Presence only; the URL is never reported.
        "db_migrations_dsn": migrations_dsn_kind(),
        "usali_rules": rules_loaded,
        "structural_recognizer": recognizer_ok,
        "ontology_version": ontology_version,
        "methodology_sections": methodology_sections,
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


async def reference_posture(session: AsyncSession) -> dict[str, Any]:
    """FON-96 — ``{str_markets: {loaded, version, counts, licence flag …},
    market_taxonomy_serve_external, external_tenants_served}``. Internal
    tenants always see the list; ``external_tenants_served`` is the AND of
    the dataset flag and the setting."""
    from ..services.str_markets import (
        count_str_markets,
        external_serving_enabled,
        get_reference_dataset,
    )

    rows = await count_str_markets(session)
    dataset = await get_reference_dataset(session)
    served, setting_on, licensed = await external_serving_enabled(session)
    block: dict[str, Any] = {"loaded": rows > 0, "row_count": rows}
    if dataset is not None:
        block.update(dataset.as_dict())
        block.pop("dataset", None)
    else:
        block.update({"version": None, "licensed_for_external": licensed})
    return {
        "str_markets": block,
        "market_taxonomy_serve_external": setting_on,
        "external_tenants_served": served,
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

    # Semantic search (chunk store + OpenAI embeddings). Both halves are
    # optional and BOTH fail open — a missing ``document_chunks`` table or an
    # absent OPENAI_API_KEY silently degrades /deals/{id}/search to full-text
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
            "provider": _emb.EMBEDDING_PROVIDER if embeddings_on else None,
            "model": _emb.EMBEDDING_MODEL if embeddings_on else None,
            "mode": (
                "hybrid"
                if chunks_on and embeddings_on
                else "fulltext_only"
                if chunks_on
                else "unavailable"
            ),
        }
        # The repair loop's counters: how many chunks it embedded / failed
        # since boot and the last error, so silent degradation (the Voyage
        # 429s that left 98% of chunks unembedded) is visible here.
        from ..services.embedding_repair import repair_stats

        semantic_search["repair"] = repair_stats()
    except Exception as exc:
        logger.warning("health: semantic search probe failed: %s", exc)
        semantic_search = {"error": type(exc).__name__}

    read_cache: dict[str, Any] = {}
    try:
        from ..services.read_cache import read_cache_stats

        read_cache = read_cache_stats()
    except Exception as exc:
        read_cache = {"error": type(exc).__name__}

    # Last provider refusal (dead key / no credit) — type + time, never the
    # message. Written by ``app.llm.record_provider_error`` (2026-10-30).
    llm_provider: dict[str, Any]
    try:
        from ..llm import llm_provider_status

        llm_provider = llm_provider_status()
    except Exception as exc:
        llm_provider = {"error": type(exc).__name__}

    # FON-96 — the market / submarket taxonomy list: what is loaded (version,
    # counts to check against the file's own notes) and the two licensing
    # switches that decide whether EXTERNAL workspaces are served it.
    reference: dict[str, Any]
    try:
        reference = await reference_posture(session)
    except Exception as exc:
        logger.warning("health: reference posture failed: %s", exc)
        reference = {"error": type(exc).__name__}

    return {
        "raw_store": _raw_store_snapshot(),
        "extraction_cache": extraction_cache,
        "read_cache": read_cache,
        "semantic_search": semantic_search,
        "auth": auth_posture(),
        "reference": reference,
        "llm_provider": llm_provider,
    }
