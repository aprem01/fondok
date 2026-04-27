"""FastAPI entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import analysis as analysis_router
from .api import data_library as data_library_router
from .api import deals as deals_router
from .api import documents as documents_router
from .api import export as export_router
from .api import health as health_router
from .api import market as market_router
from .api import model as model_router
from .api import settings as settings_router
from .config import get_settings
from .database import dispose_engine
from .migrations import run_startup_migrations
from .telemetry import setup_telemetry

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
    # Idempotent schema additions — creates deals/documents/audit_log
    # on Postgres, no-ops on SQLite (dev). Never raises out of lifespan.
    try:
        await run_startup_migrations()
    except Exception as exc:
        logger.exception("startup migrations failed: %s", exc)
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
    app.include_router(market_router.router, prefix="/market", tags=["market"])
    app.include_router(analysis_router.router, prefix="/analysis", tags=["analysis"])
    app.include_router(export_router.router, prefix="/deals", tags=["export"])
    app.include_router(
        data_library_router.router, prefix="/data-library", tags=["data-library"]
    )
    app.include_router(settings_router.router, prefix="/settings", tags=["settings"])
    return app


app = create_app()
