"""LLM provider alert — a dead Anthropic key pages instead of failing silently.

Go-live audit D.4 (2026-10-30): a revoked key, a permission-denied
workspace or an exhausted credit balance used to surface only as every
extraction failing with a generic error. ``app.llm`` now hangs a
callback on every ChatAnthropic it builds; on ``AuthenticationError``,
``PermissionDeniedError``, HTTP 402 or Anthropic's "credit balance is too
low" 400 it calls ``report_alert(severity="critical",
kind="llm_provider_unavailable")`` — at most once per 10 minutes per process
— and records the last error type + timestamp for ``/admin/health``.
Rate limits, overloads and timeouts are NOT provider-unavailable: the SDK
retries them and they must not page.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anthropic
import httpx
import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-llm-provider-alert.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app import llm as llm_mod  # noqa: E402


def _status_error(cls: type[anthropic.APIStatusError], status: int, message: str = "nope", body: Any = None):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req)
    return cls(message, response=resp, body=body)


@pytest.fixture(autouse=True)
def _reset_state():
    llm_mod.reset_provider_state()
    yield
    llm_mod.reset_provider_state()


# ─── classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (_status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "authentication"),
        (_status_error(anthropic.PermissionDeniedError, 403), "permission_denied"),
        (_status_error(anthropic.APIStatusError, 402, "payment required"), "insufficient_credit"),
        (
            _status_error(
                anthropic.BadRequestError,
                400,
                "Your credit balance is too low to access the Anthropic API.",
                body={"type": "error", "error": {"type": "invalid_request_error",
                      "message": "Your credit balance is too low to access the Anthropic API."}},
            ),
            "insufficient_credit",
        ),
    ],
)
def test_provider_unavailable_errors_are_classified(exc, kind) -> None:
    assert llm_mod.classify_provider_error(exc) == kind


@pytest.mark.parametrize(
    "exc",
    [
        _status_error(anthropic.RateLimitError, 429),
        _status_error(anthropic.InternalServerError, 529, "overloaded"),
        _status_error(anthropic.BadRequestError, 400, "max_tokens too large"),
        _status_error(anthropic.NotFoundError, 404, "model not found"),
        anthropic.APITimeoutError(httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
        ValueError("parse miss"),
    ],
)
def test_transient_and_request_errors_are_not_provider_unavailable(exc) -> None:
    assert llm_mod.classify_provider_error(exc) is None


# ─── alert + dedupe + state ───────────────────────────────────────────────


def test_first_error_alerts_critical_with_the_kind_and_records_state() -> None:
    exc = _status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    with patch("app.alerting.report_alert") as alert:
        assert llm_mod.record_provider_error(exc, now=now, monotonic=1000.0) is True
    alert.assert_called_once()
    kwargs = alert.call_args.kwargs
    assert kwargs["severity"] == "critical"
    assert kwargs["kind"] == "llm_provider_unavailable"
    assert kwargs["exc"] is exc
    assert kwargs["extra"]["error_kind"] == "authentication"
    assert kwargs["extra"]["status_code"] == 401

    status = llm_mod.llm_provider_status()
    assert status == {
        "last_error": "authentication (AuthenticationError 401)",
        "last_error_at": "2026-10-30T12:00:00+00:00",
    }


def test_repeat_within_ten_minutes_is_deduped_but_state_still_updates() -> None:
    exc = _status_error(anthropic.AuthenticationError, 401)
    with patch("app.alerting.report_alert") as alert:
        assert llm_mod.record_provider_error(exc, now=datetime(2026, 10, 30, 12, 0, tzinfo=UTC), monotonic=0.0)
        assert llm_mod.record_provider_error(exc, now=datetime(2026, 10, 30, 12, 5, tzinfo=UTC), monotonic=300.0) is False
        assert llm_mod.record_provider_error(exc, now=datetime(2026, 10, 30, 12, 9, tzinfo=UTC), monotonic=599.9) is False
    assert alert.call_count == 1
    # The health view still shows the LATEST occurrence.
    assert llm_mod.llm_provider_status()["last_error_at"] == "2026-10-30T12:09:00+00:00"


def test_alert_fires_again_after_the_window() -> None:
    exc = _status_error(anthropic.PermissionDeniedError, 403)
    with patch("app.alerting.report_alert") as alert:
        llm_mod.record_provider_error(exc, monotonic=0.0)
        llm_mod.record_provider_error(exc, monotonic=600.0)
    assert alert.call_count == 2


def test_non_provider_errors_touch_nothing() -> None:
    with patch("app.alerting.report_alert") as alert:
        assert llm_mod.record_provider_error(_status_error(anthropic.RateLimitError, 429)) is False
        assert llm_mod.record_provider_error(ValueError("x")) is False
    alert.assert_not_called()
    assert llm_mod.llm_provider_status() == {"last_error": None, "last_error_at": None}


def test_alerting_never_raises_out_of_the_llm_call_path() -> None:
    exc = _status_error(anthropic.AuthenticationError, 401)
    with patch("app.alerting.report_alert", side_effect=RuntimeError("slack down")):
        assert llm_mod.record_provider_error(exc) is True  # recorded, swallowed
    assert llm_mod.llm_provider_status()["last_error"].startswith("authentication")


# ─── wiring ───────────────────────────────────────────────────────────────


def test_callback_handler_routes_llm_errors_to_the_monitor() -> None:
    from uuid import uuid4

    handler = llm_mod.ProviderErrorMonitor()
    exc = _status_error(anthropic.AuthenticationError, 401)
    with patch("app.alerting.report_alert") as alert:
        handler.on_llm_error(exc, run_id=uuid4())
        handler.on_llm_error(_status_error(anthropic.RateLimitError, 429), run_id=uuid4())
    alert.assert_called_once()
    # ``run_inline`` so the state write happens on the calling thread, before
    # the exception propagates to the agent.
    assert handler.run_inline is True


def test_build_llm_attaches_the_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeChatAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeChatAnthropic)
    llm_mod.build_llm(role="extractor", max_tokens=100, timeout=10)
    monitors = [cb for cb in captured["callbacks"] if isinstance(cb, llm_mod.ProviderErrorMonitor)]
    assert len(monitors) == 1


@pytest.mark.asyncio
async def test_escalation_path_attaches_the_monitor_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel, ValidationError

    class _Env(BaseModel):
        n: int

    captured: dict[str, Any] = {}

    class _Stub:
        async def ainvoke(self, messages, config=None):
            return _Env(n=1)

    class _FakeChatAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def with_structured_output(self, _schema, method="function_calling"):
            return _Stub()

    class _Missing:
        async def ainvoke(self, messages, config=None):
            raise ValidationError.from_exception_data("miss", [])

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeChatAnthropic)
    monkeypatch.setenv("ANTHROPIC_NORMALIZER_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_ESCALATION_MODEL", "claude-sonnet-4-6")
    await llm_mod.invoke_with_escalation(
        role="normalizer", schema=_Env, messages=["hi"], max_tokens=10, timeout=5,
        llm=_Missing(), escalation_threshold=1,
    )
    assert any(isinstance(cb, llm_mod.ProviderErrorMonitor) for cb in captured["callbacks"])
