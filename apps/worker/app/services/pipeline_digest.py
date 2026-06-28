"""Pipeline digest engine (Wave 4 W4.5).

Builds, formats, and dispatches recurring pipeline summaries to Slack
and/or email. Reuses the W3.5 pipeline snapshot + filter primitives
so a digest sees identical numbers to the analyst's live pipeline
page.

Pipeline of operations
----------------------
1. ``build_digest_payload`` runs the saved view's filter (or
   "everything in scope" if no saved view is referenced) against the
   pipeline snapshot, then composes a :class:`DigestPayload` with the
   sections the schedule asked for.
2. ``format_slack_message`` renders the payload as a Slack Block Kit
   ``{blocks: [...]}`` envelope.
3. ``format_email_html`` renders a minimal transactional-email HTML
   document (single column, no inline images, no external CSS).
4. ``dispatch_digest`` fans out to Slack + the configured email
   backend.  A failure in either channel is logged and swallowed —
   the digest is best-effort and a Slack hiccup must not stop email.

Cadence
-------
The cadence math (``compute_next_run_at``) is shared with the
``digest_scheduler`` loop. Daily fires at ``hour_utc``; weekly fires
on ``weekday`` (0=Monday … 6=Sunday) at ``hour_utc``; monthly fires
on the 1st at ``hour_utc``. All math is done in UTC — the UI is
responsible for converting to the operator's local time when it
displays the schedule.

No live integrations
--------------------
Slack posts use ``urllib`` and Sentry/alerting follow the same
fire-and-forget pattern as :mod:`fondok_worker.alerting`. SendGrid is
stubbed against the v3 ``/mail/send`` endpoint; we never hit the
network from tests (the post boundary is patched).
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from .pipeline import (
    apply_sort,
    build_pipeline_snapshot,
    build_summary,
)

logger = logging.getLogger(__name__)


# ─────────────────────────── data shapes ───────────────────────────


@dataclass
class DigestPayload:
    """Assembled digest body — fed to Slack + email formatters."""

    title: str
    subtitle: str
    generated_at: datetime
    cadence: str
    kpi_block: dict[str, Any] | None = None
    recently_mutated: list[dict[str, Any]] = field(default_factory=list)
    deals_meeting_target: list[dict[str, Any]] = field(default_factory=list)
    full_table: list[dict[str, Any]] = field(default_factory=list)
    deal_count: int = 0


@dataclass
class DispatchResult:
    """Per-channel dispatch outcome for telemetry + the run-now API."""

    slack_attempted: bool = False
    slack_succeeded: bool = False
    slack_error: str | None = None
    email_attempted: bool = False
    email_succeeded: bool = False
    email_error: str | None = None
    no_op_reason: str | None = None


# ─────────────────────────── cadence math ───────────────────────────


def _next_at_hour(now: datetime, hour_utc: int) -> datetime:
    """Next instant at ``hour_utc`` strictly in the future (>= now+1s).

    Same-day if we haven't hit the hour yet, else tomorrow.
    """
    base = now.astimezone(UTC).replace(
        minute=0, second=0, microsecond=0
    )
    candidate = base.replace(hour=hour_utc)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def compute_next_run_at(
    *,
    cadence: str,
    hour_utc: int,
    weekday: int | None,
    now: datetime | None = None,
) -> datetime:
    """Return the next UTC firing instant for a schedule.

    * daily — next ``hour_utc`` (today if in the future, else
      tomorrow).
    * weekly — next occurrence of ``weekday`` at ``hour_utc``.
      ``weekday`` defaults to Monday when omitted.
    * monthly — 1st of next month at ``hour_utc``.

    The result is always strictly in the future from ``now`` so the
    scheduler doesn't fire twice on the same minute.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    hour_utc = max(0, min(23, int(hour_utc)))

    if cadence == "weekly":
        wd = weekday if weekday is not None else 0
        wd = max(0, min(6, int(wd)))
        candidate = _next_at_hour(now, hour_utc)
        delta_days = (wd - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=delta_days)
        if candidate <= now:
            candidate = candidate + timedelta(days=7)
        return candidate

    if cadence == "monthly":
        candidate = now.replace(
            day=1, hour=hour_utc, minute=0, second=0, microsecond=0
        )
        if candidate <= now:
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            candidate = candidate.replace(year=year, month=month)
        return candidate

    # daily / unknown — fall back to daily semantics
    return _next_at_hour(now, hour_utc)


def lookback_window(cadence: str) -> timedelta:
    """How far back to scan for "recently mutated" deals."""
    if cadence == "weekly":
        return timedelta(days=7)
    if cadence == "monthly":
        return timedelta(days=30)
    return timedelta(hours=24)


# ─────────────────────────── filter application ───────────────────────────


def apply_filter_dict(
    rows: list[dict[str, Any]],
    *,
    filter_dict: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Apply a :class:`fondok_schemas.PipelineFilter`-shaped dict.

    Mirrors ``services.pipeline.apply_filters`` but accepts the saved
    view's multi-select ``state`` list. NULL metric values fail every
    predicate (same semantics as the live pipeline — "is this deal
    hitting 15% IRR?" is unknown, not no).
    """
    if not filter_dict:
        return list(rows)

    states = filter_dict.get("state")
    if isinstance(states, str):
        states = [states]
    min_irr = filter_dict.get("min_irr")
    max_irr = filter_dict.get("max_irr")
    min_per_key = filter_dict.get("min_per_key")
    max_per_key = filter_dict.get("max_per_key")
    max_cap_rate = filter_dict.get("max_cap_rate")
    chain_scales = filter_dict.get("chain_scales")
    if isinstance(chain_scales, str):
        chain_scales = [chain_scales]

    def keep(row: dict[str, Any]) -> bool:
        if states:
            if row.get("state") not in states:
                return False
        if min_irr is not None:
            v = row.get("levered_irr")
            if v is None or v < min_irr:
                return False
        if max_irr is not None:
            v = row.get("levered_irr")
            if v is None or v > max_irr:
                return False
        if min_per_key is not None:
            v = row.get("price_per_key")
            if v is None or v < min_per_key:
                return False
        if max_per_key is not None:
            v = row.get("price_per_key")
            if v is None or v > max_per_key:
                return False
        if max_cap_rate is not None:
            v = row.get("exit_cap_rate")
            if v is None or v > max_cap_rate:
                return False
        if chain_scales:
            # ``brand`` is the only chain-scale-ish field we surface
            # in the snapshot today (positioning is a per-deal text
            # field). Match case-insensitively so "Marriott" hits
            # "marriott" rows seeded from extractor.
            brand = (row.get("brand") or "").lower()
            if brand and not any(
                cs.lower() in brand or brand in cs.lower()
                for cs in chain_scales
            ):
                return False
            if not brand:
                return False
        return True

    return [r for r in rows if keep(r)]


# ─────────────────────────── payload assembly ───────────────────────────


async def _load_saved_filter(
    session: AsyncSession,
    *,
    tenant_id: str,
    saved_view_id: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Return ``(filter_dict, view_name)`` for the schedule's view.

    Falls back to ``({}, None)`` when ``saved_view_id`` is unset or
    the view has been deleted out from under the schedule.
    """
    if not saved_view_id:
        return {}, None
    row = (
        await session.execute(
            text(
                """
                SELECT name, filter FROM saved_pipeline_views
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(saved_view_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        return {}, None
    raw = row._mapping.get("filter")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = {}
    return parsed, row._mapping.get("name")


def _row_lite(row: dict[str, Any]) -> dict[str, Any]:
    """Trim a pipeline row down to digest-friendly fields."""
    return {
        "deal_id": row.get("deal_id"),
        "name": row.get("name"),
        "state": row.get("state"),
        "brand": row.get("brand"),
        "city": row.get("city"),
        "keys": row.get("keys"),
        "levered_irr": row.get("levered_irr"),
        "equity_multiple": row.get("equity_multiple"),
        "price_per_key": row.get("price_per_key"),
        "exit_cap_rate": row.get("exit_cap_rate"),
        "target_irr": row.get("target_irr"),
        "target_irr_met": row.get("target_irr_met"),
        "last_activity_at": row.get("last_activity_at"),
    }


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


async def build_digest_payload(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    schedule: dict[str, Any],
    now: datetime | None = None,
) -> DigestPayload:
    """Compose the digest body for a single schedule.

    Pulls the cached pipeline snapshot, applies the schedule's saved
    filter (when set), and assembles the sections the schedule's
    include-* flags asked for.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    tenant_str = str(tenant_id)
    snapshot = await build_pipeline_snapshot(session, tenant_id=tenant_id)
    filter_dict, view_name = await _load_saved_filter(
        session,
        tenant_id=tenant_str,
        saved_view_id=schedule.get("saved_view_id"),
    )
    filtered = apply_filter_dict(snapshot, filter_dict=filter_dict)
    sort_token = filter_dict.get("sort", "last_activity_desc")
    filtered = apply_sort(filtered, sort_token)

    cadence = schedule.get("cadence", "daily")

    title = f"Pipeline digest — {now.date().isoformat()}"
    if view_name:
        subtitle = f"Filter: {view_name} · {len(filtered)} deal(s)"
    else:
        subtitle = f"All active deals · {len(filtered)} deal(s)"

    payload = DigestPayload(
        title=title,
        subtitle=subtitle,
        generated_at=now,
        cadence=cadence,
        deal_count=len(filtered),
    )

    if schedule.get("include_kpi_summary", True):
        summary = build_summary(filtered)
        payload.kpi_block = summary

    if schedule.get("include_recently_mutated", True):
        cutoff = now - lookback_window(cadence)
        recent = []
        for r in filtered:
            ts = _coerce_dt(r.get("last_activity_at"))
            if ts is not None and ts >= cutoff:
                recent.append(_row_lite(r))
        # newest first
        recent.sort(
            key=lambda x: _coerce_dt(x.get("last_activity_at")) or now,
            reverse=True,
        )
        payload.recently_mutated = recent[:10]

    if schedule.get("include_deals_meeting_target", True):
        winners = [
            _row_lite(r)
            for r in filtered
            if r.get("target_irr_met") is True
            and r.get("levered_irr") is not None
        ]
        winners.sort(
            key=lambda x: x.get("levered_irr") or 0.0, reverse=True
        )
        payload.deals_meeting_target = winners[:5]

    if schedule.get("include_full_table", False):
        payload.full_table = [_row_lite(r) for r in filtered]

    return payload


# ─────────────────────────── formatters ───────────────────────────


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_em(value: Any) -> str:
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return "—"


def format_slack_message(payload: DigestPayload) -> dict[str, Any]:
    """Render the digest as a Slack Block Kit ``{blocks: [...]}`` body.

    Block shape sticks to ``section`` + ``divider`` + ``context``
    blocks — broadly compatible across Slack clients (mobile, desktop,
    threaded preview). The top-level ``text`` is the notification
    fallback shown in the system tray.
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": payload.title},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*{payload.subtitle}*"},
                {
                    "type": "mrkdwn",
                    "text": f"Cadence: `{payload.cadence}`",
                },
            ],
        },
    ]

    if payload.kpi_block:
        kpi = payload.kpi_block
        lines = [
            f"*Deals:* {kpi.get('deal_count', 0)}",
            f"*Median IRR:* {_fmt_pct(kpi.get('median_irr'))}",
            f"*Median $/key:* {_fmt_money(kpi.get('median_per_key'))}",
            f"*Median cap rate:* {_fmt_pct(kpi.get('median_cap_rate'))}",
            (
                f"*Meeting target:* "
                f"{kpi.get('deals_meeting_target_irr', 0)} / "
                f"{kpi.get('deals_with_target_irr', 0)}"
            ),
        ]
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )

    if payload.recently_mutated:
        blocks.append({"type": "divider"})
        lines = [":zap: *Recently mutated*"]
        for r in payload.recently_mutated[:10]:
            lines.append(
                f"• *{r.get('name', '?')}* — "
                f"IRR {_fmt_pct(r.get('levered_irr'))}, "
                f"EM {_fmt_em(r.get('equity_multiple'))}"
            )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )

    if payload.deals_meeting_target:
        blocks.append({"type": "divider"})
        lines = [":dart: *Deals meeting target IRR (top 5)*"]
        for r in payload.deals_meeting_target:
            lines.append(
                f"• *{r.get('name', '?')}* — "
                f"{_fmt_pct(r.get('levered_irr'))} "
                f"(target {_fmt_pct(r.get('target_irr'))})"
            )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )

    if payload.full_table:
        blocks.append({"type": "divider"})
        # Slack code blocks render fixed-width and survive on mobile;
        # 3000-char cap per text element so we truncate aggressively.
        header = (
            f"{'Name':<22} {'State':<11} {'IRR':>6} {'$/key':>10} {'EM':>5}"
        )
        rows = [header, "-" * len(header)]
        for r in payload.full_table[:50]:
            rows.append(
                f"{(r.get('name') or '?')[:22]:<22} "
                f"{(r.get('state') or '')[:11]:<11} "
                f"{_fmt_pct(r.get('levered_irr')):>6} "
                f"{_fmt_money(r.get('price_per_key')):>10} "
                f"{_fmt_em(r.get('equity_multiple')):>5}"
            )
        text_block = "```" + "\n".join(rows)[:2900] + "```"
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text_block},
            }
        )

    return {
        "text": payload.title,  # fallback for notifications
        "blocks": blocks,
    }


def format_email_html(payload: DigestPayload) -> str:
    """Render the payload as a minimal transactional-email HTML doc.

    No external CSS, no inline images — keeps the body well under the
    Gmail clipping threshold and resilient across mail clients.
    """
    parts: list[str] = [
        "<html><body style=\"font-family:Helvetica,Arial,sans-serif;"
        "color:#111;max-width:640px;margin:0 auto;padding:24px;\">",
        f"<h2 style=\"margin:0 0 4px 0;\">{payload.title}</h2>",
        (
            f"<p style=\"color:#555;margin:0 0 16px 0;\">"
            f"{payload.subtitle} · cadence "
            f"<code>{payload.cadence}</code></p>"
        ),
    ]
    if payload.kpi_block:
        kpi = payload.kpi_block
        parts.append("<h3>KPIs</h3><ul>")
        parts.append(
            f"<li><b>Deals:</b> {kpi.get('deal_count', 0)}</li>"
        )
        parts.append(
            f"<li><b>Median IRR:</b> "
            f"{_fmt_pct(kpi.get('median_irr'))}</li>"
        )
        parts.append(
            f"<li><b>Median $/key:</b> "
            f"{_fmt_money(kpi.get('median_per_key'))}</li>"
        )
        parts.append(
            f"<li><b>Median cap rate:</b> "
            f"{_fmt_pct(kpi.get('median_cap_rate'))}</li>"
        )
        parts.append(
            f"<li><b>Meeting target IRR:</b> "
            f"{kpi.get('deals_meeting_target_irr', 0)} / "
            f"{kpi.get('deals_with_target_irr', 0)}</li>"
        )
        parts.append("</ul>")

    def _ul(rows: Iterable[dict[str, Any]]) -> str:
        items = "".join(
            f"<li><b>{r.get('name', '?')}</b> — "
            f"IRR {_fmt_pct(r.get('levered_irr'))}, "
            f"EM {_fmt_em(r.get('equity_multiple'))}, "
            f"$/key {_fmt_money(r.get('price_per_key'))}</li>"
            for r in rows
        )
        return f"<ul>{items}</ul>"

    if payload.recently_mutated:
        parts.append("<h3>Recently mutated</h3>")
        parts.append(_ul(payload.recently_mutated))

    if payload.deals_meeting_target:
        parts.append("<h3>Deals meeting target IRR</h3>")
        parts.append(_ul(payload.deals_meeting_target))

    if payload.full_table:
        parts.append("<h3>Pipeline</h3><table border=\"1\" "
                     "cellpadding=\"4\" cellspacing=\"0\" style=\""
                     "border-collapse:collapse;width:100%;\">")
        parts.append(
            "<tr><th>Name</th><th>State</th><th>IRR</th>"
            "<th>$/key</th><th>EM</th></tr>"
        )
        for r in payload.full_table:
            parts.append(
                "<tr>"
                f"<td>{r.get('name', '?')}</td>"
                f"<td>{r.get('state', '')}</td>"
                f"<td>{_fmt_pct(r.get('levered_irr'))}</td>"
                f"<td>{_fmt_money(r.get('price_per_key'))}</td>"
                f"<td>{_fmt_em(r.get('equity_multiple'))}</td>"
                "</tr>"
            )
        parts.append("</table>")

    parts.append("</body></html>")
    return "".join(parts)


# ─────────────────────────── dispatch ───────────────────────────


def _post_slack(url: str, body: dict[str, Any]) -> tuple[bool, str | None]:
    """Synchronous Slack POST — patched at the urlopen boundary in tests."""
    try:
        data = json.dumps(body).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=5) as resp:
            if 200 <= resp.status < 300:
                return True, None
            return False, f"slack webhook returned {resp.status}"
    except urlerror.HTTPError as ex:
        return False, f"slack HTTPError {ex.code}"
    except urlerror.URLError as ex:
        return False, f"slack URLError {ex.reason}"
    except Exception as ex:  # noqa: BLE001 — best-effort
        return False, f"slack unexpected {type(ex).__name__}"


def _send_email(
    *,
    subject: str,
    html: str,
    recipients: list[str],
) -> tuple[bool, str | None, str]:
    """Send via the configured ``EMAIL_BACKEND``.

    Returns ``(ok, error, backend_used)``. The ``log_only`` backend is
    the dev / CI default; it always returns success. ``sendgrid`` POSTs
    to the v3 API. ``ses`` is a TODO and falls back to log_only with a
    warning.
    """
    settings = get_settings()
    backend = (settings.EMAIL_BACKEND or "log_only").lower()
    if not recipients:
        return False, "no recipients configured", backend

    if backend == "log_only":
        logger.info(
            "email[log_only]: subject=%r recipients=%s html_bytes=%d",
            subject,
            recipients,
            len(html),
        )
        return True, None, backend

    if backend == "sendgrid":
        if settings.SENDGRID_API_KEY is None:
            return False, "SENDGRID_API_KEY not set", backend
        try:
            api_key = settings.SENDGRID_API_KEY.get_secret_value()
            body = {
                "personalizations": [
                    {"to": [{"email": addr} for addr in recipients]}
                ],
                "from": {
                    "email": settings.EMAIL_FROM_ADDRESS,
                    "name": settings.EMAIL_FROM_NAME,
                },
                "subject": subject,
                "content": [{"type": "text/html", "value": html}],
            }
            req = urlrequest.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=8) as resp:
                if 200 <= resp.status < 300:
                    return True, None, backend
                return False, f"sendgrid returned {resp.status}", backend
        except Exception as ex:  # noqa: BLE001
            return False, f"sendgrid error {type(ex).__name__}", backend

    # ses — TODO. Log and fall through.
    logger.warning(
        "email[%s]: backend not implemented — falling back to log_only",
        backend,
    )
    logger.info(
        "email[fallback-log]: subject=%r recipients=%s", subject, recipients
    )
    return True, None, "log_only"


def dispatch_digest(
    schedule: dict[str, Any],
    payload: DigestPayload,
) -> DispatchResult:
    """Fan out the digest to Slack + email.

    Never raises — both channels are best-effort. A 500 from Slack is
    logged and reported via the ``DispatchResult`` but does not stop
    the email path. When no usable delivery channel is configured the
    result carries ``no_op_reason`` so the run-now API can surface it.
    """
    result = DispatchResult()
    delivery = (schedule.get("delivery") or "slack").lower()
    settings = get_settings()

    wants_slack = delivery in ("slack", "both")
    wants_email = delivery in ("email", "both")

    slack_url = (schedule.get("slack_webhook_url") or "").strip()
    recipients = schedule.get("email_recipients") or []
    if isinstance(recipients, str):
        try:
            recipients = json.loads(recipients)
        except json.JSONDecodeError:
            recipients = []
    if not isinstance(recipients, list):
        recipients = []

    # Settings already loaded above — kept for explicit log line context.
    _ = settings

    # No-op detection: nothing is wired up at all on the channels we
    # wanted. We special-case the common "delivery=slack but webhook
    # blank, and no fallback" case so the run-now API can surface a
    # clear reason instead of pretending we dispatched.
    if not wants_slack and not wants_email:
        result.no_op_reason = "delivery=none"
        return result
    has_slack_channel = wants_slack and bool(slack_url)
    has_email_channel = wants_email and bool(recipients)
    if not has_slack_channel and not has_email_channel:
        # No channel can actually deliver — pure no-op.
        if wants_slack and not slack_url:
            result.no_op_reason = "slack webhook missing"
        elif wants_email and not recipients:
            result.no_op_reason = "no email recipients"
        else:
            result.no_op_reason = "no channel configured"
        return result

    if wants_slack:
        result.slack_attempted = True
        if not slack_url:
            result.slack_error = "webhook URL not configured"
            logger.warning(
                "digest[%s] slack skipped: webhook URL missing",
                schedule.get("id"),
            )
        else:
            body = format_slack_message(payload)
            ok, err = _post_slack(slack_url, body)
            result.slack_succeeded = ok
            result.slack_error = err
            if not ok:
                logger.warning(
                    "digest[%s] slack post failed: %s",
                    schedule.get("id"),
                    err,
                )

    if wants_email:
        result.email_attempted = True
        if not recipients:
            result.email_error = "no recipients configured"
            logger.warning(
                "digest[%s] email skipped: no recipients",
                schedule.get("id"),
            )
        else:
            html = format_email_html(payload)
            ok, err, _ = _send_email(
                subject=payload.title,
                html=html,
                recipients=[str(r) for r in recipients],
            )
            result.email_succeeded = ok
            result.email_error = err
            if not ok:
                logger.warning(
                    "digest[%s] email send failed: %s",
                    schedule.get("id"),
                    err,
                )

    if not (result.slack_attempted or result.email_attempted):
        result.no_op_reason = "no channel attempted"

    return result


__all__ = [
    "DigestPayload",
    "DispatchResult",
    "apply_filter_dict",
    "build_digest_payload",
    "compute_next_run_at",
    "dispatch_digest",
    "format_email_html",
    "format_slack_message",
    "lookback_window",
]
