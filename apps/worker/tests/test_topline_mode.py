"""FON-116 staged rollout — ``TOPLINE_BENCHMARK_ENABLED`` (default OFF).

The switch is read in ONE place (``services.topline.topline_mode``) and is
surfaced on ``/health`` and on ``topline_status.topline_mode``:

* OFF (``growth_only``) — FON-116 only: Year 1 from the subject's own basis,
  every year's growth from the per-year resolver.
* ON (``benchmark``) — the full FON-117 engine (benchmark × penetration × ramp).

Temporary: removed once FON-117 is on in production (DEPLOY.md).
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.config import Settings, get_settings
from app.services.topline import TOPLINE_MODES, topline_mode


def test_default_is_growth_only() -> None:
    assert Settings.model_fields["TOPLINE_BENCHMARK_ENABLED"].default is False
    assert topline_mode() == "growth_only"
    assert TOPLINE_MODES == ("growth_only", "benchmark")


def test_switch_on_is_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOPLINE_BENCHMARK_ENABLED", "1")
    get_settings.cache_clear()
    try:
        assert topline_mode() == "benchmark"
    finally:
        monkeypatch.delenv("TOPLINE_BENCHMARK_ENABLED")
        get_settings.cache_clear()
    assert topline_mode() == "growth_only"


def test_benchmark_fixture_turns_it_on(topline_benchmark: None) -> None:
    assert topline_mode() == "benchmark"


def test_the_setting_is_read_in_one_place() -> None:
    """Only ``services/topline.py`` reads the setting; everything else asks
    ``topline_mode()``."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    readers = sorted(
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if "TOPLINE_BENCHMARK_ENABLED" in p.read_text()
    )
    assert readers == ["config.py", "services/topline.py"], readers


@pytest.mark.asyncio
async def test_health_reports_the_mode(topline_benchmark: None) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/health")).json()
    assert body["topline_mode"] == "benchmark"


@pytest.mark.asyncio
async def test_health_reports_growth_only_by_default() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/health")).json()
    assert body["topline_mode"] == "growth_only"
