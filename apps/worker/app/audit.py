"""Centralized append-only audit logging.

Every state-changing operation in the worker writes an ``audit_log``
row via :func:`log_audit`. The contract that lets us hand this to a
Blackstone / Brookfield IT review:

* **Append-only** — Postgres has a trigger blocking ``UPDATE``/``DELETE``
  on ``audit_log`` (see :mod:`app.migrations`). SQLite (dev-only) keeps
  the same shape but can't enforce the trigger.
* **Tamper-evident** — every row stores a SHA-256 of the canonical-JSON
  input and output payloads so we can prove later what the caller saw
  and what the system returned.
* **Best-effort** — :func:`log_audit` never raises out. Audit logging is
  observability, not business logic; a logging failure must never roll
  back the caller's mutation.

The helper coexists with whatever transaction the caller is running:
we ``flush`` (so the row is staged), but we leave commit semantics to
the caller — that way a failed audit write inside a wider transaction
rolls back together with the mutation it was tracking.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _sha256_canonical(payload: Any) -> str:
    """SHA-256 of a deterministically encoded JSON payload.

    ``sort_keys=True`` + ``default=str`` give us a byte-for-byte stable
    hash even across Python versions / dict insertion orders, and
    transparently coerce ``UUID``/``datetime``/``Decimal`` values that
    ``json.dumps`` would otherwise reject.
    """
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


async def log_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    actor_id: UUID | str | None = None,
    action: str,
    resource_type: str,
    resource_id: UUID | str | None = None,
    input_payload: Any = None,
    output_payload: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert one append-only ``audit_log`` row. Never raises out.

    Parameters
    ----------
    session
        The active ``AsyncSession``. The caller owns commit semantics;
        we only ``flush`` so the row is staged.
    tenant_id
        Tenant the action took place under. Required for multi-tenant
        isolation when the audit trail is later sliced.
    actor_id
        Who initiated the action. ``None`` is recorded as ``"system"``
        (e.g. a scheduled job, a back-fill).
    action
        Dotted verb describing the event — e.g. ``deal.created``,
        ``memo.edited``, ``document.uploaded``. Conventions live in the
        callers; the audit table doesn't enforce a vocabulary.
    resource_type
        High-level entity the action targeted (``deal``, ``document``,
        ``memo``). Used by audit dashboards to filter.
    resource_id
        Concrete entity id. When ``resource_type == 'deal'`` this is
        also written into the dedicated ``deal_id`` column so the
        deal-scoped audit query stays a simple equality lookup.
    input_payload, output_payload
        Caller-supplied dicts (or any JSON-serialisable shape). Both
        are SHA-256 hashed and the originals stored under the
        ``payload`` JSONB column so we can verify integrity later
        without trusting the surrounding row.
    metadata
        Free-form context (request id, trace id, …) bundled into
        ``payload['metadata']``.
    """
    try:
        input_hash = (
            _sha256_canonical(input_payload) if input_payload is not None else None
        )
        output_hash = (
            _sha256_canonical(output_payload) if output_payload is not None else None
        )

        # Deal-scoped actions populate the dedicated ``deal_id`` column
        # so the existing ``idx_audit_log_deal`` index is hit directly.
        deal_col = (
            str(resource_id)
            if resource_type == "deal" and resource_id is not None
            else None
        )

        payload_blob = {
            "input": input_payload,
            "output": output_payload,
            "metadata": metadata,
        }

        await session.execute(
            text(
                """
                INSERT INTO audit_log (
                    id, tenant_id, deal_id, actor_id, action,
                    resource_type, resource_id, input_hash, output_hash,
                    payload, created_at
                ) VALUES (
                    :id, :tenant, :deal, :actor, :action,
                    :rtype, :rid, :ihash, :ohash, :payload, :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant": str(tenant_id),
                "deal": deal_col,
                "actor": str(actor_id) if actor_id is not None else "system",
                "action": action,
                "rtype": resource_type,
                "rid": str(resource_id) if resource_id is not None else None,
                "ihash": input_hash,
                "ohash": output_hash,
                "payload": json.dumps(payload_blob, default=str),
                "ts": datetime.now(UTC),
            },
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001 — audit must never raise out
        logger.warning(
            "log_audit write failed (action=%s rtype=%s rid=%s): %s",
            action,
            resource_type,
            resource_id,
            exc,
        )


__all__ = ["log_audit"]
