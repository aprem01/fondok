"""``Settings`` strips leading/trailing whitespace from every str / SecretStr.

Go-live audit D.7 (2026-10-30): only ``DATABASE_URL_MIGRATIONS`` and the
Sentry DSN were stripped. A trailing ``"\\n"`` pasted into
``CLERK_JWT_ISSUER`` on Railway would turn every JWT into a 401
(``InvalidIssuerError``) with nothing in the config pointing at the cause.
Now the model validator strips every string field, logs WHICH field it
touched — names only, never values — and leaves clean values alone.

The line is logged at WARNING on purpose: ``Settings`` is first constructed
while ``app.main`` is importing its routers, before ``logging.basicConfig``
runs, so only Python's last-resort handler (WARNING and above → stderr) is
in place. The subprocess test at the bottom proves the line reaches stderr
under that real import order.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings


def _settings(**overrides):
    # ``_env_file=None`` keeps a developer's apps/worker/.env out of the test.
    return Settings(_env_file=None, **overrides)


def test_trailing_newline_on_clerk_jwt_issuer_is_stripped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.config"):
        s = _settings(CLERK_JWT_ISSUER="https://clerk.fondok.example\n")
    assert s.CLERK_JWT_ISSUER == "https://clerk.fondok.example"
    messages = [r.getMessage() for r in caplog.records]
    assert "config: stripped whitespace from CLERK_JWT_ISSUER" in messages
    # Names only — the value never reaches the log.
    assert not any("clerk.fondok.example" in m for m in messages)


def test_trailing_newline_from_the_environment_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_JWT_ISSUER", "https://clerk.fondok.example\n")
    assert _settings().CLERK_JWT_ISSUER == "https://clerk.fondok.example"


def test_secret_str_values_are_stripped_without_logging_the_secret(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.config"):
        s = _settings(ANTHROPIC_API_KEY=" sk-ant-not-a-real-key\n")
    assert isinstance(s.ANTHROPIC_API_KEY, SecretStr)
    assert s.ANTHROPIC_API_KEY.get_secret_value() == "sk-ant-not-a-real-key"
    messages = [r.getMessage() for r in caplog.records]
    assert "config: stripped whitespace from ANTHROPIC_API_KEY" in messages
    assert not any("sk-ant-not-a-real-key" in m for m in messages)
    assert "sk-ant-not-a-real-key" not in repr(s)


def test_clean_values_are_left_alone_and_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.config"):
        s = _settings(CLERK_JWT_ISSUER="https://clerk.fondok.example", DATABASE_URL="sqlite+aiosqlite:///./x.db")
    assert s.CLERK_JWT_ISSUER == "https://clerk.fondok.example"
    assert s.DATABASE_URL == "sqlite+aiosqlite:///./x.db"
    assert not any("stripped whitespace" in r.getMessage() for r in caplog.records)


def test_every_str_field_is_covered() -> None:
    """The validator must not be a hand-maintained list: a str field added
    tomorrow is stripped too. Probe a few from different sections."""
    s = _settings(
        DATABASE_URL=" sqlite+aiosqlite:///./x.db ",
        CLERK_JWKS_URL="https://clerk.fondok.example/.well-known/jwks.json\n",
        S3_BUCKET="\tfondok-raw ",
        LLM_PROVIDER="anthropic\n",
    )
    assert s.DATABASE_URL == "sqlite+aiosqlite:///./x.db"
    assert s.CLERK_JWKS_URL == "https://clerk.fondok.example/.well-known/jwks.json"
    assert s.S3_BUCKET == "fondok-raw"
    assert s.LLM_PROVIDER == "anthropic"


def test_none_and_non_string_fields_are_untouched() -> None:
    s = _settings(CLERK_JWT_ISSUER=None, DEFAULT_DEAL_BUDGET_USD=12.5)
    assert s.CLERK_JWT_ISSUER is None
    assert s.DEFAULT_DEAL_BUDGET_USD == 12.5


def test_strip_line_reaches_stderr_under_the_real_import_order(tmp_path: Path) -> None:
    """``import app.main`` in a fresh interpreter with a trailing newline on
    CLERK_JWT_ISSUER: Settings is built during the router imports, before
    ``logging.basicConfig`` in main.py, and the WARNING must still appear on
    stderr (Python's last-resort handler). An INFO line would not."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("CLERK_", "ANTHROPIC_", "DATABASE_URL"))
    }
    env["CLERK_JWT_ISSUER"] = "https://clerk.fondok.example\n"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'import-order.db'}"
    env["ANTHROPIC_API_KEY"] = "sk-test-key-not-real"
    worker = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(worker), env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "config: stripped whitespace from CLERK_JWT_ISSUER" in proc.stderr, proc.stderr[-1500:]
    assert "clerk.fondok.example" not in proc.stderr
