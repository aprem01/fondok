"""``GET /health`` reports which build is serving (go-live audit D.2).

On 2026-10-28 Railway lagged 3.5 hours behind main and nobody could tell:
``/health`` carried only the hand-maintained ``__version__``. It now reports
``build_sha`` — ``RAILWAY_GIT_COMMIT_SHA`` first (Railway injects it at
runtime), else ``FONDOK_BUILD_SHA`` (baked by the Dockerfile's ``ARG
GIT_SHA``), else null — and ``build_time`` (``FONDOK_BUILD_TIME`` from the
Dockerfile ``ARG BUILD_TIME``, else the ``BUILD_TIME`` file the image writes
at build, else null). Public and non-sensitive: a commit SHA says nothing an
attacker can use and ``version`` was already there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api import health as health_mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in ("RAILWAY_GIT_COMMIT_SHA", "FONDOK_BUILD_SHA", "FONDOK_BUILD_TIME"):
        monkeypatch.delenv(name, raising=False)
    # Point the file fallback at an empty directory so a developer's checkout
    # (which has no BUILD_TIME file either) and the image behave the same.
    monkeypatch.setenv("FONDOK_BUILD_TIME_FILE", str(tmp_path / "BUILD_TIME"))
    health_mod._read_build_time_file.cache_clear()
    yield
    health_mod._read_build_time_file.cache_clear()


def test_nothing_set_is_null_not_a_placeholder() -> None:
    assert health_mod.build_info() == {"build_sha": None, "build_time": None}


def test_railway_sha_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv("FONDOK_BUILD_SHA", "b" * 40)
    assert health_mod.build_info()["build_sha"] == "a" * 40


def test_dockerfile_arg_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FONDOK_BUILD_SHA", "b" * 40)
    assert health_mod.build_info()["build_sha"] == "b" * 40


def test_blank_values_are_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ARG that was never passed bakes ``ENV FONDOK_BUILD_SHA=`` (empty)."""
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "  \n")
    monkeypatch.setenv("FONDOK_BUILD_SHA", "")
    monkeypatch.setenv("FONDOK_BUILD_TIME", "")
    assert health_mod.build_info() == {"build_sha": None, "build_time": None}


def test_build_time_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FONDOK_BUILD_TIME", "2026-10-30T14:02:11Z\n")
    assert health_mod.build_info()["build_time"] == "2026-10-30T14:02:11Z"


def test_build_time_from_the_image_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "BUILD_TIME").write_text("2026-10-30T14:02:11Z\n")
    assert health_mod.build_info()["build_time"] == "2026-10-30T14:02:11Z"


def test_env_build_time_beats_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "BUILD_TIME").write_text("2026-01-01T00:00:00Z\n")
    monkeypatch.setenv("FONDOK_BUILD_TIME", "2026-10-30T14:02:11Z")
    assert health_mod.build_info()["build_time"] == "2026-10-30T14:02:11Z"


@pytest.mark.asyncio
async def test_public_health_carries_build_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "c" * 40)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["build_sha"] == "c" * 40
    assert "build_time" in body
    assert body["build_time"] is None
