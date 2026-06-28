"""FastAPI entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .alerting import init_sentry
from .api import analysis as analysis_router
from .api import audit as audit_router
from .api import data_library as data_library_router
from .api import deals as deals_router
from .api import documents as documents_router
from .api import dossier as dossier_router
from .api import due_diligence as due_diligence_router
from .api import export as export_router
from .api import health as health_router
from .api import market as market_router
from .api import model as model_router
from .api import observability as observability_router
from .api import portfolio_library as portfolio_library_router
from .api import scenarios as scenarios_router
from .api import settings as settings_router
from .config import get_settings
from .database import dispose_engine, get_engine
from .migrations import run_startup_migrations
from .telemetry import setup_langsmith, setup_telemetry
from .tenant_middleware import register_tenant_safety_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "fondok-worker starting tenant=%s provider=%s budget=$%.2f",
        settings.DEFAULT_TENANT_ID,
        settings.LLM_PROVIDER,
        settings.DEFAULT_DEAL_BUDGET_USD,
    )
    # Sentry — no-op when SENTRY_DSN_WORKER is unset. Init FIRST so
    # any subsequent setup crash gets reported.
    try:
        init_sentry()
    except Exception as exc:
        logger.warning("sentry init failed: %s", exc)
    # LangSmith tracing — no-op when LANGSMITH_API_KEY is unset.
    # Must run before the first agent call so the LangChain client
    # picks up the tracing env vars on instantiation.
    try:
        setup_langsmith()
    except Exception as exc:
        logger.warning("langsmith setup failed: %s", exc)
    # Idempotent schema additions — creates deals/documents/audit_log
    # on Postgres, no-ops on SQLite (dev). Never raises out of lifespan.
    try:
        await run_startup_migrations()
    except Exception as exc:
        logger.exception("startup migrations failed: %s", exc)
    # Defense-in-depth tenant-isolation listener — installed AFTER
    # migrations so the schema bootstrap (which legitimately runs
    # CREATE TABLE + a handful of unscoped SELECTs against system
    # catalogs) doesn't trip the listener.
    try:
        register_tenant_safety_listener(get_engine())
    except Exception as exc:
        logger.exception("tenant safety listener registration failed: %s", exc)
    try:
        yield
    finally:
        logger.info("fondok-worker shutting down")
        await dispose_engine()


def create_app() -> FastAPI:
    """Application factory — used by uvicorn and tests."""
    app = FastAPI(
        title="Fondok Worker",
        version=__version__,
        description="Python tier: FastAPI + LangGraph hotel-underwriting agent runtime.",
        lifespan=lifespan,
    )

    settings = get_settings()
    cors_origins = settings.cors_origin_list
    if settings.ALLOWED_CORS_ORIGIN_REGEX:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_origin_regex=settings.ALLOWED_CORS_ORIGIN_REGEX,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # OpenTelemetry: must run AFTER FastAPI app exists but BEFORE the
    # routers handle traffic. No-op when OTEL_EXPORTER_OTLP_ENDPOINT
    # is unset, so dev runs aren't affected.
    setup_telemetry(app)

    app.include_router(health_router.router, tags=["health"])
    app.include_router(deals_router.router, prefix="/deals", tags=["deals"])
    # Documents are deal-scoped: /deals/{deal_id}/documents/...
    app.include_router(documents_router.router, prefix="/deals", tags=["documents"])
    app.include_router(model_router.router, prefix="/model", tags=["model"])
    # Deal-scoped engine surface — /deals/{id}/engines/... — backs the
    # web app's Run Model button. Mounted under /deals so the routes
    # share path-parameter semantics with the rest of the deal API.
    app.include_router(
        model_router.engines_router, prefix="/deals", tags=["engines"]
    )
    app.include_router(market_router.router, prefix="/market", tags=["market"])
    app.include_router(analysis_router.router, prefix="/analysis", tags=["analysis"])
    # /deals/{id}/dossier + /deals/{id}/ask — Context Data Product surface.
    app.include_router(dossier_router.router, prefix="/deals", tags=["dossier"])
    app.include_router(export_router.router, prefix="/deals", tags=["export"])
    # /deals/{id}/due-diligence — Lovable parity broker-question packet.
    app.include_router(
        due_diligence_router.router, prefix="/deals", tags=["due-diligence"]
    )
    # Wave 3 W3.2 — named what-if scenarios per deal. Mounted under
    # /deals so every route inherits the deal-scoped path parameter and
    # tenant resolution model the rest of the deal API uses.
    app.include_router(
        scenarios_router.router, prefix="/deals", tags=["scenarios"]
    )
    app.include_router(
        data_library_router.router, prefix="/data-library", tags=["data-library"]
    )
    # Wave 4 W4.1 — firm-level Portfolio P&L Library. Tenant-scoped via
    # ``Depends(get_tenant_id)``; the engine_runner pulls active entries
    # whose chain_scales_covered overlap the subject deal's chain scale
    # and whose vintage_year falls inside the 3-year look-back to feed
    # the portfolio_pnl tier of op_ratio_precedence.
    app.include_router(
        portfolio_library_router.router,
        prefix="/portfolio-library",
        tags=["portfolio-library"],
    )
    app.include_router(settings_router.router, prefix="/settings", tags=["settings"])
    app.include_router(
        observability_router.router,
        prefix="/observability",
        tags=["observability"],
    )
    # Wave 4 W4.3 — Activity Feed (per-deal stream) + Compliance Explorer
    # (tenant-wide). Per-deal mounts under /deals so it shares the same
    # path-parameter contract as the rest of the deal API; the explorer
    # mounts at /audit so the UI route + the API surface line up.
    app.include_router(
        audit_router.router, prefix="/deals", tags=["audit"]
    )
    app.include_router(
        audit_router.explorer_router, prefix="/audit", tags=["audit"]
    )
    return app


app = create_app()
