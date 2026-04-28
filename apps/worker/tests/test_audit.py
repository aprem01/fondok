"""Tests for the centralized append-only audit helper.

Covers:
* ``test_log_audit_writes_row_with_hashes`` — round-trips input/output
  payloads and verifies the SHA-256 columns hold the deterministic
  hash so the audit trail is tamper-evident.
* ``test_log_audit_never_raises_on_bad_payload`` — caller's mutation
  must always win even when the audit insert blows up.
* ``test_log_audit_resource_type_deal_populates_deal_col`` — the
  dedicated ``deal_id`` column is populated for deal-scoped events so
  the existing index is hit directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-audit.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema before every test so each starts on an empty table."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(text("DELETE FROM audit_log"))
            await session.commit()
        except Exception:  # noqa: BLE001
            pass
    yield


def _canonical_hash(payload: object) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@pytest.mark.asyncio
async def test_log_audit_writes_row_with_hashes() -> None:
    """Round-trip an input/output payload and verify the SHA-256 columns."""
    from app.audit import log_audit
    from app.database import get_session_factory

    tenant = str(uuid4())
    deal = str(uuid4())
    actor = "user-123"

    inp = {"name": "Ritz Carlton", "keys": 200}
    out = {"id": deal, "status": "Draft"}

    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            action="deal.created",
            resource_type="deal",
            resource_id=deal,
            input_payload=inp,
            output_payload=out,
            metadata={"trace_id": "abc"},
        )
        await session.commit()

        row = (
            await session.execute(
                text(
                    """
                    SELECT tenant_id, deal_id, actor_id, action,
                           resource_type, resource_id, input_hash,
                           output_hash, payload
                      FROM audit_log
                     WHERE resource_id = :rid
                    """
                ),
                {"rid": deal},
            )
        ).first()

    assert row is not None
    m = row._mapping
    assert m["tenant_id"] == tenant
    assert m["actor_id"] == actor
    assert m["action"] == "deal.created"
    assert m["resource_type"] == "deal"
    assert m["resource_id"] == deal
    # deal-scoped action populates the dedicated deal_id col
    assert m["deal_id"] == deal
    # SHA-256 hashes are deterministic and match the canonical hash.
    assert m["input_hash"] == _canonical_hash(inp)
    assert m["output_hash"] == _canonical_hash(out)
    # payload roundtrips the original blob + metadata.
    payload = json.loads(m["payload"])
    assert payload["input"] == inp
    assert payload["output"] == out
    assert payload["metadata"] == {"trace_id": "abc"}


@pytest.mark.asyncio
async def test_log_audit_never_raises_on_bad_payload(caplog) -> None:
    """A non-JSON-serialisable object must not crash the caller.

    We pass an object whose ``__repr__`` blows up — ``default=str``
    fallback also fails, so the audit insert should error out
    internally and be swallowed by the helper. The caller's mutation
    (here: nothing) must continue.
    """
    from app.audit import log_audit
    from app.database import get_session_factory

    class Exploding:
        def __str__(self) -> str:  # pragma: no cover - branch hit by json.dumps
            raise RuntimeError("boom")

        __repr__ = __str__

    factory = get_session_factory()
    async with factory() as session:
        # Should NOT raise even though the payload can't be serialised.
        await log_audit(
            session,
            tenant_id=str(uuid4()),
            actor_id="system",
            action="deal.broken",
            resource_type="deal",
            resource_id=str(uuid4()),
            input_payload={"bad": Exploding()},
        )
        # Session is still usable — best-effort semantics held.
        await session.execute(text("SELECT 1"))


@pytest.mark.asyncio
async def test_log_audit_actor_defaults_to_system() -> None:
    """When ``actor_id`` is None we record ``'system'`` per the contract."""
    from app.audit import log_audit
    from app.database import get_session_factory

    deal = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session,
            tenant_id=str(uuid4()),
            actor_id=None,
            action="cron.backfill",
            resource_type="deal",
            resource_id=deal,
        )
        await session.commit()

        row = (
            await session.execute(
                text("SELECT actor_id FROM audit_log WHERE resource_id = :rid"),
                {"rid": deal},
            )
        ).first()
    assert row is not None
    assert row._mapping["actor_id"] == "system"


@pytest.mark.asyncio
async def test_log_audit_non_deal_resource_leaves_deal_col_null() -> None:
    """``resource_type='document'`` must NOT populate the deal_id column —
    that index is reserved for deal-scoped lookups."""
    from app.audit import log_audit
    from app.database import get_session_factory

    doc = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await log_audit(
            session,
            tenant_id=str(uuid4()),
            actor_id="user",
            action="document.uploaded",
            resource_type="document",
            resource_id=doc,
        )
        await session.commit()

        row = (
            await session.execute(
                text(
                    "SELECT deal_id, resource_id, resource_type "
                    "FROM audit_log WHERE resource_id = :rid"
                ),
                {"rid": doc},
            )
        ).first()
    assert row is not None
    m = row._mapping
    assert m["deal_id"] is None
    assert m["resource_id"] == doc
    assert m["resource_type"] == "document"
