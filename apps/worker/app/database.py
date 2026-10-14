"""Async SQLAlchemy session management."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazily build the async engine using the configured DSN."""
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.async_database_url
        # SQLite (used in dev) doesn't support pool_size / max_overflow.
        if url.startswith("sqlite"):
            _engine = create_async_engine(url, future=True)
        else:
            _engine = create_async_engine(
                url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=10,
                future=True,
            )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazily build the session factory bound to the engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


# ─────────────────── FON-93: RLS tenant GUC ───────────────────

# ``set_config(name, value, is_local=true)`` is the parameterisable form of
# ``SET LOCAL`` — ``SET LOCAL`` itself takes no bind parameters. Local
# means "until this transaction ends", which is exactly the RLS contract:
# the policy in ``migrations/rls/001_enable_rls.sql`` reads
# ``current_setting('app.tenant_id')`` and sees nothing once the
# transaction is over.
TENANT_GUC_SQL = text("SELECT set_config('app.tenant_id', :tenant, true)")


def _tenant_guc_is_active() -> bool:
    """True when the GUC should be published: flag on AND Postgres."""
    settings = get_settings()
    if not settings.DB_SET_TENANT_GUC:
        return False
    return not settings.async_database_url.startswith("sqlite")


def _publish_tenant_guc(session: Any, transaction: Any, connection: Any) -> None:
    """``after_begin`` hook — runs at the start of EVERY transaction.

    Attaching to ``after_begin`` (not a one-shot statement in
    ``get_session``) is what makes this robust to ``session.commit()``
    mid-request: the next statement autobegins a new transaction, the
    hook fires again, and the GUC is re-published. Without it, every
    statement after the first commit would run with no tenant set and —
    under FORCE ROW LEVEL SECURITY — see zero rows.

    Reads the tenant from :data:`app.auth.context.current_tenant_id`. A
    request that resolved no tenant (schedulers, scripts) publishes
    nothing; RLS then denies every tenant-scoped row, which is the
    intended fail-closed posture for an unidentified caller.
    """
    from .auth.context import current_tenant_id

    tenant = current_tenant_id.get()
    if not tenant:
        return
    connection.execute(TENANT_GUC_SQL, {"tenant": tenant})


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession`` with rollback-on-error.

    With ``settings.DB_SET_TENANT_GUC`` on (and a Postgres DSN) the
    session publishes the caller's tenant to ``app.tenant_id`` at the
    start of each transaction — see :func:`_publish_tenant_guc`.
    """
    factory = get_session_factory()
    async with factory() as session:
        if _tenant_guc_is_active():
            event.listen(session.sync_session, "after_begin", _publish_tenant_guc)
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Cleanly dispose of the engine on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
