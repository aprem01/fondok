"""Async SQLAlchemy session management."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


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


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession`` with rollback-on-error."""
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
