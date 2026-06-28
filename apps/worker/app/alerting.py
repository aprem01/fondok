"""Sentry + Slack alerting (Wave 2 P2.9).

Two channels, one entrypoint:

  * Sentry (``SENTRY_DSN_WORKER``) — captures every uncaught exception
    and any explicit ``report_alert(...)`` call. FastAPI / asyncpg /
    httpx integrations attach automatically.
  * Slack (``SLACK_ALERT_WEBHOOK_URL``) — high-severity alerts only.
    Off by default; gates on ``SLACK_ALERT_MIN_SEVERITY``.

Both channels are no-ops when their respective env var is unset, so
local dev / tests / CI never paginate or hit external services.

The ``before_send`` filter drops noisy categories so the Sentry inbox
isn't dominated by user-fault errors:

  * Pydantic ``ValidationError`` from request payload parsing —
    FastAPI returns 422 to the client; that's enough signal.
  * ``BudgetExceededError`` — expected per-deal cost guard. Users see
    the error in the UI; it's not an infra failure.
  * Starlette / FastAPI ``HTTPException`` with status < 500 — 4xx
    by definition is the client's problem.

Sites that should call ``report_alert`` directly:

  * Tenant-isolation breach (``MissingTenantFilterError``) — critical
  * All-documents-failed batch — error (Sam relies on per-doc OCR)
  * Engine root NOI calculation crash — error
  * Extractor retries exhausted on a single doc — warning
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Literal
from urllib import error as urlerror
from urllib import request as urlrequest

from .config import get_settings

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "error", "critical"]

_SENTRY_INITIALIZED = False
_SENTRY_MODULE: Any = None
_SEVERITY_ORDER: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}


def init_sentry() -> bool:
    """Initialize the Sentry SDK if ``SENTRY_DSN_WORKER`` is set.

    Idempotent. Returns True when the SDK was activated (or already
    was), False when the DSN isn't configured. Never raises out — a
    crash here would defeat the purpose of error reporting.
    """
    global _SENTRY_INITIALIZED, _SENTRY_MODULE
    if _SENTRY_INITIALIZED:
        return True

    settings = get_settings()
    dsn = (settings.SENTRY_DSN_WORKER or "").strip()
    if not dsn:
        logger.info("sentry: disabled (SENTRY_DSN_WORKER not set)")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError as exc:
        logger.warning("sentry: SDK not installed — disabled (%s)", exc)
        return False

    env = os.environ.get(
        "DEPLOYMENT_ENVIRONMENT", settings.DEPLOYMENT_ENVIRONMENT
    )
    release = (settings.SENTRY_RELEASE or os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "").strip() or None

    sentry_sdk.init(
        dsn=dsn,
        environment=env,
        release=release,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            AsyncPGIntegration(),
            HttpxIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=_before_send,
    )
    sentry_sdk.set_tag("fondok.tenant_default", settings.DEFAULT_TENANT_ID)
    sentry_sdk.set_tag("fondok.service", "worker")
    _SENTRY_MODULE = sentry_sdk
    _SENTRY_INITIALIZED = True
    logger.info(
        "sentry: enabled env=%s release=%s traces=%.2f",
        env,
        release or "(unset)",
        settings.SENTRY_TRACES_SAMPLE_RATE,
    )
    return True


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop noisy / expected categories before they reach Sentry."""
    exc_info = hint.get("exc_info")
    if exc_info and len(exc_info) >= 2:
        exc = exc_info[1]
        # User-fault 4xx — FastAPI turns these into client responses.
        try:
            from fastapi.exceptions import HTTPException as FastAPIHTTPException
            from starlette.exceptions import HTTPException as StarletteHTTPException

            if isinstance(exc, (FastAPIHTTPException, StarletteHTTPException)):
                status = getattr(exc, "status_code", 500)
                if 400 <= status < 500:
                    return None
        except Exception:
            pass

        # Expected per-deal budget guard.
        try:
            from .budget import BudgetExceededError

            if isinstance(exc, BudgetExceededError):
                return None
        except Exception:
            pass

        # Pydantic body-parsing errors that FastAPI surfaces as 422.
        try:
            from pydantic import ValidationError

            if isinstance(exc, ValidationError):
                return None
        except Exception:
            pass

    return event


def report_alert(
    *,
    severity: Severity,
    title: str,
    deal_id: str | None = None,
    tenant_id: str | None = None,
    stage: str | None = None,
    exc: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Single entrypoint for high-severity events.

    Sends to Sentry (capture_exception when ``exc`` present, else
    capture_message) AND posts a one-line Slack alert when severity
    >= ``SLACK_ALERT_MIN_SEVERITY``.

    Never raises out — alerting must be best-effort.
    """
    payload = {
        "title": title,
        "deal_id": deal_id,
        "tenant_id": tenant_id,
        "stage": stage,
        **(extra or {}),
    }

    if _SENTRY_INITIALIZED and _SENTRY_MODULE is not None:
        try:
            with _SENTRY_MODULE.push_scope() as scope:
                scope.set_level(severity)
                if deal_id:
                    scope.set_tag("fondok.deal_id", deal_id)
                if tenant_id:
                    scope.set_tag("fondok.tenant_id", tenant_id)
                if stage:
                    scope.set_tag("fondok.stage", stage)
                for k, v in (extra or {}).items():
                    scope.set_extra(k, v)
                if exc is not None:
                    _SENTRY_MODULE.capture_exception(exc)
                else:
                    _SENTRY_MODULE.capture_message(title, level=severity)
        except Exception as ex:
            logger.warning("sentry: report_alert failed (%s)", ex)

    _maybe_slack(severity, title, payload, exc)


def _maybe_slack(
    severity: Severity,
    title: str,
    payload: dict[str, Any],
    exc: BaseException | None,
) -> None:
    settings = get_settings()
    webhook = settings.SLACK_ALERT_WEBHOOK_URL
    if webhook is None:
        return
    url = webhook.get_secret_value().strip()
    if not url:
        return
    min_sev = _SEVERITY_ORDER.get(settings.SLACK_ALERT_MIN_SEVERITY, 2)
    if _SEVERITY_ORDER.get(severity, 0) < min_sev:
        return

    env = os.environ.get("DEPLOYMENT_ENVIRONMENT", settings.DEPLOYMENT_ENVIRONMENT)
    emoji = {
        "critical": ":fire:",
        "error": ":rotating_light:",
        "warning": ":warning:",
        "info": ":information_source:",
    }.get(severity, ":information_source:")

    fields = []
    if payload.get("deal_id"):
        fields.append({"title": "Deal", "value": payload["deal_id"], "short": True})
    if payload.get("tenant_id"):
        fields.append({"title": "Tenant", "value": payload["tenant_id"], "short": True})
    if payload.get("stage"):
        fields.append({"title": "Stage", "value": payload["stage"], "short": True})
    if exc is not None:
        fields.append(
            {
                "title": "Exception",
                "value": f"`{type(exc).__name__}: {exc}`"[:300],
                "short": False,
            }
        )

    body: dict[str, Any] = {
        "text": f"{emoji} *{severity.upper()}* — {title} [`{env}`]",
        "attachments": [
            {
                "color": _color_for(severity),
                "fields": fields or [{"title": "Service", "value": "fondok-worker", "short": True}],
            }
        ],
    }
    if settings.SLACK_ALERT_CHANNEL:
        body["channel"] = settings.SLACK_ALERT_CHANNEL

    # Fire-and-forget on a background thread so the request path
    # never blocks on Slack's response time.
    threading.Thread(
        target=_post_slack_webhook,
        args=(url, body),
        name="slack-alert",
        daemon=True,
    ).start()


def _color_for(severity: Severity) -> str:
    return {
        "critical": "#7f1d1d",
        "error": "#dc2626",
        "warning": "#f59e0b",
        "info": "#6b7280",
    }.get(severity, "#6b7280")


def _post_slack_webhook(url: str, body: dict[str, Any]) -> None:
    try:
        data = json.dumps(body).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=5) as resp:
            if resp.status >= 400:
                logger.warning("slack: webhook %s returned %s", url[:40], resp.status)
    except urlerror.URLError as ex:
        logger.warning("slack: webhook post failed (%s)", ex)
    except Exception as ex:
        logger.warning("slack: webhook unexpected error (%s)", ex)


__all__ = [
    "Severity",
    "init_sentry",
    "report_alert",
]
