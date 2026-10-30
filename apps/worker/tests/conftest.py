"""Pytest configuration and fixtures for Fondok worker tests.

Process-level hygiene lives here (docs/TESTING.md has the incident log):

* a PRIVATE temp directory per pytest process — ~115 test modules pin
  ``Path(tempfile.gettempdir()) / "fondok-tests-<module>.db"`` at import and
  ``unlink`` it, so two suites sharing one $TMPDIR corrupt each other's
  live sqlite files ("database disk image is malformed", "disk I/O error");
* a per-module snapshot/restore of ``os.environ`` plus a settings-cache
  clear, so a module sees the same ``get_settings()`` whatever ran before it.
"""

import asyncio
import os
import shutil
import tempfile

# ── 1. Private temp directory for THIS process ─────────────────────────────
# Created under whatever $TMPDIR the caller chose (``.tmp_fx`` in the gate,
# the system default otherwise), then pinned as both the env var (so child
# processes — evals/run.py, scripts/compare_golden_baseline.py — inherit it)
# and ``tempfile.tempdir`` (so this process's ``gettempdir()`` returns it).
# This runs before any test module is imported — conftest is collected
# first — which is what makes the fixed-name ``fondok-tests-*.db`` files
# per-process. Removed at session end unless FONDOK_TEST_KEEP_TMP=1.
_PRIVATE_TMP = tempfile.mkdtemp(prefix="fondok-pytest-")
os.environ["TMPDIR"] = _PRIVATE_TMP
tempfile.tempdir = _PRIVATE_TMP

# 35 test modules exercise tenant scoping through a bare ``X-Tenant-Id``
# header with no Clerk JWT — that is the header path the worker now refuses
# by default (2026-10-06: it was reachable from the public internet with a
# forged header). The suite opts in explicitly. This MUST precede any
# ``app`` import below, because ``get_settings()`` is lru-cached on first
# use and ``app.database`` reads it at import.
os.environ.setdefault("ALLOW_TENANT_HEADER_WITHOUT_JWT", "1")
# Tests never call a real embeddings provider (a developer shell may export
# a key) and never start the background repair loop; embedding tests
# monkeypatch the client and drive ``embedding_repair.tick_once`` directly.
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("VOYAGE_API_KEY", None)
os.environ["EMBEDDING_REPAIR_ENABLED"] = "false"

import pytest  # noqa: E402 — env pinning above must precede every app import
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import get_engine  # noqa: E402
from app.migrations import SQLITE_MIGRATIONS  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Remove this process's private temp directory (see module docstring)."""
    if os.environ.get("FONDOK_TEST_KEEP_TMP", "").strip().lower() not in ("1", "true", "yes"):
        shutil.rmtree(_PRIVATE_TMP, ignore_errors=True)


@pytest.fixture(scope="module", autouse=True)
def _isolate_env_and_settings_per_module():
    """Snapshot/restore ``os.environ`` and clear the settings cache per module.

    Why (2026-10-17 audit): ``get_settings()`` is ``lru_cache(maxsize=1)``
    and is populated DURING COLLECTION — ``app/services/extractor_throttle.py``
    reads it at import, which the first module to import ``app.api.documents``
    triggers. At that moment only the alphabetically-earlier modules have run
    their module-level ``os.environ[...] = ...`` lines, so the cached
    ``Settings`` reflects a partial environment (probe on this suite: cached
    ``EXTRACTION_WATCHDOG_ENABLED=True``, ``DOCUMENT_STORAGE_ROOT=/tmp/fondok``;
    the final environment had the watchdog disabled and a per-module storage
    root).
    Any test that then calls ``get_settings.cache_clear()`` flips EVERY later
    module to the final environment — e.g. ``test_extraction_watchdog.py::
    test_watchdog_start_is_a_noop_when_disabled`` passed or failed depending
    on whether some earlier module had cleared the cache. Likewise ~30 tests
    write ``os.environ`` directly (not via ``monkeypatch``) and a few never
    restore it, leaking into whichever module runs next.

    Clearing at module start gives every module the same deterministic view
    — the fully-collected environment — and restoring the snapshot at module
    end contains in-body leaks to the module that made them. The database
    engine is NOT touched: it is a process singleton bound on first use (by
    the session fixture below), and ``run_startup_migrations`` goes through
    the same ``get_engine()``, so a re-read ``DATABASE_URL`` cannot split the
    migrated file from the one the engine uses. Cost: one ``Settings()``
    construction per module (~200 modules, well under a second in total).
    """
    snapshot = dict(os.environ)
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
        get_settings.cache_clear()


# ── C7 — the canned extraction backend (an injected seam, no env switch) ────
# The pipeline's LLM steps go through ``app.api.documents``'s injectable
# backend. Every test runs on the canned backend that lives in
# ``evals/mock_extraction.py`` (no tokens, deterministic); a test that drives
# the real backend — usually with ``_run_graph_extraction`` monkeypatched —
# opts out with ``@pytest.mark.real_extraction_backend``.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def load_mock_extraction_module():
    """``evals/mock_extraction.py`` loaded by path (``evals`` is not a
    package on the worker's import path)."""
    import importlib.util
    import sys

    name = "fondok_evals_mock_extraction"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_REPO_ROOT, "evals", "mock_extraction.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _canned_extraction_backend(request):
    from app.api.documents import set_extraction_backend

    if request.node.get_closest_marker("real_extraction_backend"):
        yield
        return
    previous = load_mock_extraction_module().install_mock_extraction_backend()
    try:
        yield
    finally:
        set_extraction_backend(previous)


@pytest.fixture(autouse=True)
def _reset_deal_read_cache():
    """``services.read_cache`` is process-local; a test that seeds a deal with
    the same ids and timestamps as an earlier one must never be served the
    earlier test's response, so every test starts with an empty cache."""
    from app.services.read_cache import reset_read_cache

    reset_read_cache()
    yield
    reset_read_cache()


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Set up the shared test schema by running the sqlite migrations.

    HOTFIX 2026-07-05: the original version (d632c37) passed raw
    strings to ``conn.execute`` — SQLAlchemy 2.x requires ``text()``,
    so every statement raised AttributeError. Setup swallowed those in
    a bare ``except`` (i.e. the whole fixture silently no-op'd), while
    teardown had NO try/except and crashed the alphabetically-last
    test module's teardown. It also iterated the Postgres-flavored
    ``MIGRATIONS`` list against sqlite; the sqlite set exists
    precisely for this.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        for _name, stmt in SQLITE_MIGRATIONS:
            try:
                await conn.execute(text(stmt))
            except Exception:
                # Idempotency: table/column may already exist because a
                # test module created its own schema first. Best-effort.
                pass
    yield
    # Teardown: best-effort drops. Never raise out of a session
    # finalizer — a failed DROP must not fail the last test module.
    drop_tables = [
        "template_mappings",
        "engine_outputs",
        "extraction_results",
        "documents",
        "deals",
        "audit_log",
        "model_calls",
        "broker_questions",
        "broker_qa_pairs",
        "scenarios",
        "saved_pipeline_views",
        "pipeline_digest_schedules",
        "portfolio_library",
        "field_catalog",
        "pending_batches",
        "due_diligence_questions",
    ]
    try:
        async with engine.begin() as conn:
            for table in drop_tables:
                try:
                    await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
                except Exception:
                    pass
    except Exception:
        pass
