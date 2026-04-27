"""Tests for the /observability endpoints + the eval runner exit codes.

Covers:
* ``test_cache_stats_endpoint`` — POST a synthetic ModelCall and assert
  /observability/cache-stats returns it with the right hit rate.
* ``test_agent_costs_aggregation`` — multiple ModelCalls across two
  agents; verify per-agent breakdown sums correctly.
* ``test_evals_run_exits_nonzero_on_drift`` — run ``evals/run.py`` with
  a deliberately broken expected/model.json and assert exit code 1.
* ``test_evals_run_passes_when_clean`` — happy path produces exit 0
  and writes the JUnit XML artifact.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

# Force the SQLite dev DSN before app modules import — otherwise the
# Settings() singleton may pick up an unrelated env var from CI / dev.
# Use a per-test DB so other test modules don't see our synthetic rows.
_TEST_DB = Path(__file__).resolve().parent / "fondok-obs.db"
os.environ.setdefault(
    "DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
)
os.environ.setdefault("ALLOW_TEST_INGEST", "true")


REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS_DIR = REPO_ROOT / "evals"
CASE_DIR = EVALS_DIR / "golden-set" / "kimpton-angler"
EXPECTED_MODEL = CASE_DIR / "expected" / "model.json"


# ─────────────────────── fixtures ───────────────────────


async def _ensure_schema() -> None:
    """Run startup migrations directly — AsyncClient/ASGITransport
    doesn't drive the FastAPI lifespan, so the ``model_calls`` table
    won't exist on a fresh sqlite file otherwise."""
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


@pytest.fixture
def deal_id() -> str:
    """Per-test deal id so /cache-stats counts only this run's rows.

    Ingest endpoint stamps created_at = now, so requesting the most
    recent N rows after we POST gives us our own data back.
    """
    return str(uuid.uuid4())


# ─────────────────────── /observability tests ───────────────────────


@pytest.mark.asyncio
async def test_cache_stats_endpoint(deal_id: str) -> None:
    """Insert one synthetic call and verify /cache-stats reflects it."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Insert one synthetic call: 80% cache hit rate (8000 read,
        # 1000 created, 1000 plain in).
        ingest = await client.post(
            "/observability/_test/model-call",
            json={
                "deal_id": deal_id,
                "agent_name": "extractor",
                "model": "claude-sonnet-4-6",
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_tokens": 8000,
                "cache_creation_tokens": 1000,
                "cost_usd": 0.0,
                "trace_id": "obs-test",
            },
        )
        assert ingest.status_code == 201, ingest.text

        # Pull back the most recent 5 calls — our row should dominate.
        r = await client.get("/observability/cache-stats?n=5")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["samples"] >= 1
        # 8000 / (8000 + 1000 + 1000) = 0.8
        assert body["cache_hit_rate"] >= 0.5, body
        agents = {a["agent"]: a for a in body["by_agent"]}
        assert "extractor" in agents
        ext = agents["extractor"]
        assert ext["cache_read_tokens"] >= 8000
        assert ext["cache_creation_tokens"] >= 1000


@pytest.mark.asyncio
async def test_agent_costs_aggregation(deal_id: str) -> None:
    """Multiple calls across two agents — per-agent breakdown sums correctly."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Three extractor calls (10k input each)
        for _ in range(3):
            r = await client.post(
                "/observability/_test/model-call",
                json={
                    "deal_id": deal_id,
                    "agent_name": "extractor",
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 10_000,
                    "output_tokens": 2_000,
                    "cache_read_tokens": 4_000,
                    "cache_creation_tokens": 0,
                },
            )
            assert r.status_code == 201, r.text

        # Two analyst calls (5k input each, mostly cached)
        for _ in range(2):
            r = await client.post(
                "/observability/_test/model-call",
                json={
                    "deal_id": deal_id,
                    "agent_name": "analyst",
                    "model": "claude-opus-4-7",
                    "input_tokens": 1_000,
                    "output_tokens": 500,
                    "cache_read_tokens": 4_000,
                    "cache_creation_tokens": 0,
                },
            )
            assert r.status_code == 201, r.text

        r = await client.get("/observability/agent-costs?days=1")
        assert r.status_code == 200, r.text
        body = r.json()
        agents = {a["agent"]: a for a in body["by_agent"]}
        # Both agents should be present and the calls field should sum
        # to at least what we POST'd.
        assert agents.get("extractor", {}).get("calls", 0) >= 3
        assert agents.get("analyst", {}).get("calls", 0) >= 2
        # Total cost is the sum of per-agent costs
        total = sum(a["cost_usd"] for a in body["by_agent"])
        assert abs(total - body["total_cost_usd"]) < 1e-6


# ─────────────────────── evals/run.py exit-code tests ───────────────────────


def _run_eval_runner(case: str = "kimpton-angler") -> subprocess.CompletedProcess:
    """Invoke the eval runner as a child process so the exit code is
    really returned via sys.exit (which the in-process import path
    can't reproduce)."""
    return subprocess.run(
        [
            sys.executable,
            str(EVALS_DIR / "run.py"),
            "--case",
            case,
            "--junit-output",
            str(EVALS_DIR / "test-results.xml"),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": ""},
    )


def test_evals_run_passes_when_clean() -> None:
    """Happy path: golden set unmodified → exit 0 + JUnit XML written."""
    proc = _run_eval_runner()
    assert proc.returncode == 0, (
        f"unexpected non-zero exit:\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    junit = EVALS_DIR / "test-results.xml"
    assert junit.exists(), "JUnit XML not written"
    text = junit.read_text(encoding="utf-8")
    assert text.startswith("<?xml")
    assert "kimpton-angler" in text


def test_evals_run_exits_nonzero_on_drift(tmp_path: Path) -> None:
    """Tamper with expected/model.json so a number drifts way out of
    tolerance — runner must exit 1 and call out the offending row."""
    if not EXPECTED_MODEL.exists():
        pytest.skip("expected/model.json missing — golden set incomplete")

    original = EXPECTED_MODEL.read_text(encoding="utf-8")
    try:
        broken = json.loads(original)
        # 30M loan vs ~23.66M actual = ~26% drift, way over 0.5% tol.
        broken["debt_engine"]["loan_amount_usd"] = 30_000_000
        EXPECTED_MODEL.write_text(json.dumps(broken, indent=2), encoding="utf-8")

        proc = _run_eval_runner()
    finally:
        EXPECTED_MODEL.write_text(original, encoding="utf-8")

    assert proc.returncode == 1, (
        f"expected exit 1 on drift, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # The offending metric should appear in the drift table with FAIL.
    assert "loan_amount_usd" in proc.stdout
    assert "FAIL" in proc.stdout
