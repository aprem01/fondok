"""Inbound webhooks — FON-93 Clerk organization events.

``POST /webhooks/clerk`` receives Clerk's Svix-signed deliveries. The one
event we act on is ``organization.created``: the new org is written to
the ``tenants`` registry as ``kind=external`` / ``status=pending_review``
so it shows up on ``GET /admin/tenants`` for an internal admin to
approve — and, with ``REQUIRE_REGISTERED_TENANT`` on, has no data plane
until then. Every other event type is acknowledged and ignored.

Signature verification (Svix, no SDK — stdlib HMAC)
---------------------------------------------------
Headers: ``svix-id``, ``svix-timestamp``, ``svix-signature``. The signed
content is ``"{svix-id}.{svix-timestamp}.{raw body}"``; the key is the
base64 payload after ``whsec_`` in ``CLERK_WEBHOOK_SECRET``; the header
carries one or more space-separated ``v1,<base64 HMAC-SHA256>`` entries
(several during a secret rotation) and any one matching accepts. The
timestamp must be within ``CLERK_WEBHOOK_TOLERANCE_S`` of now.

Fail-closed: no secret configured → 503 for every delivery (Svix will
retry; nothing is ever accepted unsigned). Bad / missing signature → 401.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from ..config import get_settings
from ..tenants import upsert_tenant
from .deals import coerce_org_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


def _secret_bytes(raw: str) -> bytes:
    """Decode a Svix ``whsec_<base64>`` secret to its raw key bytes."""
    payload = raw.strip()
    if payload.startswith("whsec_"):
        payload = payload[len("whsec_"):]
    return base64.b64decode(payload)


def verify_svix_signature(
    *,
    secret: str,
    msg_id: str | None,
    timestamp: str | None,
    signature_header: str | None,
    body: bytes,
    tolerance_s: int,
    now: float | None = None,
) -> bool:
    """Return True when ``signature_header`` authenticates ``body``.

    Pure function so the check is unit-testable without HTTP. Constant-time
    comparison per candidate; never raises on malformed input (returns
    False).
    """
    if not msg_id or not timestamp or not signature_header:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance_s:
        return False
    try:
        key = _secret_bytes(secret)
    except Exception:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = hmac.new(key, signed, hashlib.sha256).digest()
    for candidate in signature_header.split():
        version, _, sig_b64 = candidate.partition(",")
        if version != "v1" or not sig_b64:
            continue
        try:
            provided = base64.b64decode(sig_b64)
        except Exception:
            continue
        if hmac.compare_digest(expected, provided):
            return True
    return False


@router.post("/clerk")
async def clerk_webhook(request: Request) -> dict[str, Any]:
    """Receive a Clerk (Svix) webhook delivery."""
    settings = get_settings()
    secret = settings.CLERK_WEBHOOK_SECRET
    if secret is None or not secret.get_secret_value().strip():
        logger.error(
            "webhooks/clerk: CLERK_WEBHOOK_SECRET is unset — refusing delivery"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook_secret_not_configured",
        )

    body = await request.body()
    ok = verify_svix_signature(
        secret=secret.get_secret_value(),
        msg_id=request.headers.get("svix-id"),
        timestamp=request.headers.get("svix-timestamp"),
        signature_header=request.headers.get("svix-signature"),
        body=body,
        tolerance_s=settings.CLERK_WEBHOOK_TOLERANCE_S,
    )
    if not ok:
        logger.warning("webhooks/clerk: signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_webhook_signature",
        )

    try:
        event = json.loads(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json"
        ) from exc
    event_type = str(event.get("type") or "")
    data = event.get("data") or {}

    if event_type != "organization.created":
        return {"ok": True, "ignored": event_type or None}

    org_id = str(data.get("id") or "")
    tenant_id = coerce_org_tenant_id(org_id)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="organization.created without a Clerk org id",
        )
    name = str(data.get("name") or data.get("slug") or org_id)
    stored = await upsert_tenant(
        tenant_id=tenant_id,
        clerk_org_id=org_id,
        name=name,
        kind="external",
        status="pending_review",
        # A replayed delivery for an org an admin already approved must
        # not knock it back to pending_review.
        preserve_existing=True,
    )
    logger.info(
        "webhooks/clerk: organization.created org=%s tenant=%s → %s/%s",
        org_id,
        tenant_id,
        stored.kind,
        stored.status,
    )
    return {"ok": True, "tenant": stored.as_dict()}


__all__ = ["router", "verify_svix_signature"]
