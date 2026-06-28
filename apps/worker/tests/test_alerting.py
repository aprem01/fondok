"""Sentry + Slack alerting unit tests (Wave 2 P2.9).

Most of ``alerting.py`` no-ops when ``SENTRY_DSN_WORKER`` /
``SLACK_ALERT_WEBHOOK_URL`` are unset, which is the dev / test default.
The branches that DO have logic worth testing are:

  * the ``_before_send`` noise filter that drops 4xx HTTPException,
    Pydantic ``ValidationError``, and ``BudgetExceededError`` — these
    are user-fault errors and shouldn't burn Sentry quota.
  * the ``report_alert`` entrypoint should be a pure no-op when neither
    DSN nor webhook is configured (no exceptions, no network calls).
  * the Slack severity gate (``SLACK_ALERT_MIN_SEVERITY``) — an
    ``info`` alert under the default ``error`` floor must NOT call
    Slack.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import BaseModel, ValidationError

from app.alerting import _before_send, report_alert
from app.budget import BudgetExceededError


class _Toy(BaseModel):
    n: int


def _validation_error() -> ValidationError:
    """Manufacture a real Pydantic ValidationError for the filter test."""
    try:
        _Toy(n="not-an-int")  # type: ignore[arg-type]
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        return exc


def test_before_send_drops_4xx_http_exception() -> None:
    from fastapi.exceptions import HTTPException

    exc = HTTPException(status_code=404, detail="not found")
    event = {"event_id": "test"}
    hint = {"exc_info": (type(exc), exc, None)}

    assert _before_send(event, hint) is None


def test_before_send_drops_pydantic_validation_error() -> None:
    exc = _validation_error()
    event = {"event_id": "test"}
    hint = {"exc_info": (type(exc), exc, None)}

    assert _before_send(event, hint) is None


def test_before_send_drops_budget_exceeded() -> None:
    exc = BudgetExceededError(
        deal_id="deal-1", spent_usd=20.10, budget_usd=20.00
    )
    event = {"event_id": "test"}
    hint = {"exc_info": (type(exc), exc, None)}

    assert _before_send(event, hint) is None


def test_before_send_passes_500_through() -> None:
    from fastapi.exceptions import HTTPException

    exc = HTTPException(status_code=500, detail="kaboom")
    event = {"event_id": "test"}
    hint = {"exc_info": (type(exc), exc, None)}

    out = _before_send(event, hint)
    assert out is event


def test_before_send_passes_generic_exception_through() -> None:
    exc = RuntimeError("ungated infra error")
    event = {"event_id": "test"}
    hint = {"exc_info": (type(exc), exc, None)}

    out = _before_send(event, hint)
    assert out is event


def test_report_alert_no_op_when_unconfigured() -> None:
    # Neither SENTRY_DSN_WORKER nor SLACK_ALERT_WEBHOOK_URL set —
    # report_alert should be a pure no-op with no exceptions.
    report_alert(
        severity="critical",
        title="should-be-silent",
        deal_id="deal-1",
        tenant_id="tenant-1",
        stage="test",
    )
    # If we got here without raising, the no-op path is correct.


def test_report_alert_below_slack_threshold_does_not_post() -> None:
    # The Slack severity gate must reject 'info' under the default
    # 'error' floor — proving the threshold logic short-circuits before
    # any network attempt would be made.
    with patch("app.alerting._post_slack_webhook") as poster:
        report_alert(severity="info", title="quiet noise")
    poster.assert_not_called()
