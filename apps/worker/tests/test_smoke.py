"""Smoke tests — verify the worker scaffold is wired end-to-end.

These intentionally test only structural properties (imports, settings
defaults, graph compile, callable interfaces, /health endpoint). Heavy
integration / agent-behavior tests live under tests/test_*.py once the
agents stop being stubs.
"""

from __future__ import annotations

import os

import pytest

# Force the SQLite dev DSN before app modules import — otherwise the
# Settings() singleton may pick up an unrelated env var from CI / dev.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


def test_app_imports() -> None:
    """app.main:app must be importable without errors."""
    from app.main import app

    assert app is not None


def test_settings_load() -> None:
    """Settings must load with defaults."""
    from app.config import Settings

    s = Settings()
    assert s.DATABASE_URL
    assert s.DEFAULT_DEAL_BUDGET_USD == 20.0


def test_graph_compiles() -> None:
    """LangGraph StateGraph must compile."""
    from app.graph import build_graph

    g = build_graph()
    assert g is not None


def test_engines_importable() -> None:
    """All 8 engines must be importable and have .run() callable."""
    from app.engines import (
        CapitalEngine,
        DebtEngine,
        ExpenseEngine,
        FBRevenueEngine,
        PartnershipEngine,
        ReturnsEngine,
        RevenueEngine,
        SensitivityEngine,
    )

    for cls in [
        RevenueEngine,
        FBRevenueEngine,
        ExpenseEngine,
        CapitalEngine,
        DebtEngine,
        ReturnsEngine,
        SensitivityEngine,
        PartnershipEngine,
    ]:
        assert hasattr(cls, "run"), f"{cls.__name__} missing .run()"
        assert callable(cls.run)


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    """GET /health returns 200."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "db" in body


@pytest.mark.asyncio
async def test_deals_list_endpoint() -> None:
    """GET /deals returns a JSON list (empty for the stub)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/deals")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_deals_create_endpoint() -> None:
    """POST /deals accepts a CreateDealBody and echoes a DealSummary."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Test Hotel", "city": "Austin", "keys": 120},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Test Hotel"
        assert body["status"] == "Draft"
