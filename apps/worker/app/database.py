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
from sqlalchemy.orm import DeclarativeBase, Session

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


class TenantSession(Session):
    """The sync ``Session`` behind every ``AsyncSession`` the factory builds.

    A subclass only so the FON-93 ``after_begin`` listener below is scoped
    to Fondok's sessions rather than every SQLAlchemy ``Session`` in the
    process. Every session that comes out of :func:`get_session_factory` —
    the request dependency AND the background pipelines that call
    ``get_session_factory()()`` directly — is one of these, so the tenant
    GUC is published for all of them.
    """


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazily build the session factory bound to the engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
            sync_session_class=TenantSession,
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
    """Publish the request's tenant to ``app.tenant_id`` on ``connection``.

    Reads the tenant from :data:`app.auth.context.current_tenant_id`. A
    caller that resolved no tenant (a script, a scheduler tick before it
    picked a tenant) publishes nothing; RLS then denies every
    tenant-scoped row, which is the intended fail-closed posture for an
    unidentified caller. Pure apart from the one statement — the flag /
    dialect gate lives in :func:`_tenant_guc_after_begin`.
    """
    from .auth.context import current_tenant_id

    tenant = current_tenant_id.get()
    if not tenant:
        return
    connection.execute(TENANT_GUC_SQL, {"tenant": tenant})


def _tenant_guc_after_begin(session: Any, transaction: Any, connection: Any) -> None:
    """``after_begin`` listener on :class:`TenantSession` — EVERY transaction
    of EVERY factory session.

    Registered once at import, at the Session-class level. Until 2026-10-14
    it was attached per session inside :func:`get_session` only, so the
    extraction pipeline, the background Run Model task, cost persistence,
    the batch poller and the digest scheduler — all of which open sessions
    with ``get_session_factory()()`` — never published the GUC and would
    have read zero rows under RLS.

    ``after_begin`` (not a one-shot statement when the session opens) is
    what makes this robust to ``session.commit()`` mid-request: the next
    statement autobegins a new transaction, the listener fires again, and
    the GUC is re-published. Without it every statement after the first
    commit would run with no tenant set and — under FORCE ROW LEVEL
    SECURITY — see zero rows.

    The flag is checked on every call (a cached settings read) so the
    default-off posture costs nothing to flip: no re-registration, no
    restart ordering. On SQLite it is always a no-op.
    """
    if not _tenant_guc_is_active():
        return
    _publish_tenant_guc(session, transaction, connection)


event.listen(TenantSession, "after_begin", _tenant_guc_after_begin)


def tenant_guc_hook_registered() -> bool:
    """True when the class-level listener is in place (health / tests)."""
    return event.contains(TenantSession, "after_begin", _tenant_guc_after_begin)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession`` with rollback-on-error.

    With ``settings.DB_SET_TENANT_GUC`` on (and a Postgres DSN) the
    session publishes the caller's tenant to ``app.tenant_id`` at the
    start of each transaction — see :func:`_tenant_guc_after_begin`. The
    listener is on the Session class, so nothing is attached here.
    """
    factory = get_session_factory()
    async with factory() as session:
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
