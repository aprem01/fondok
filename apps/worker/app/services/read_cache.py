"""Short-lived, self-invalidating cache for the heaviest deal reads.

Why
---
The IC Memo tab (the page a committee member opens) fired
``GET /analysis/{id}/variance`` at 9.7 s and ``GET /deals/{id}/assumption_sources``
at 3.6 to 6 s on the testers' deals while every other tab was sub-2 s; the same
``assumption_sources`` call is also repeated on every tab (0.7 to 1.4 s each).
Both routes re-derive their answer from the deal's extraction rows on every
request, and the answer only changes when the deal's inputs change.

What
----
:func:`cached_deal_read` keys a computed response by ``(name, tenant, deal)``
and serves it again while all three hold:

* the deal's **state fingerprint** is unchanged — one cheap, tenant-scoped
  read per table the loaders depend on (the ``deals`` row including
  ``field_overrides``, the ``documents`` rows — classification, fiscal
  year, status —, the ``extraction_results`` row ids, the ``engine_outputs``
  run aggregate and the tenant's ``portfolio_library`` aggregate). A new
  extraction, an override, a reclassification, an engine run or a new
  portfolio entry therefore invalidates on its own, with no hook needed in
  the write path;
* the deal's **generation** is unchanged — :func:`bump_generation` is called
  by the two writes that mutate an ``extraction_results`` row IN PLACE
  (field review, verifier confidence), which no timestamp records;
* the entry is younger than ``READ_CACHE_TTL_S`` (default two minutes) — the
  safety net for a write this module does not see (another replica, a
  manual SQL repair). ``0`` disables the cache.

Concurrent identical reads are coalesced: the first computes, the rest
await its result, so a tab that asks for ``assumption_sources`` from three
components costs one computation.

Scope
-----
Tenant isolation is NOT this module's job: callers run their tenant
assertion before asking the cache, and the key carries the tenant id so a
second tenant can never be served the first's entry. Values are returned as
deep copies so a caller mutating its response cannot poison the cache.
Process-local — each worker replica warms its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings

logger = logging.getLogger(__name__)

@dataclass
class _Entry:
    fingerprint: str
    generation: int
    expires_at: float
    value: Any


_ENTRIES: dict[tuple[str, str, str], _Entry] = {}
_INFLIGHT: dict[tuple[str, str, str, str, int], asyncio.Future[Any]] = {}
_GENERATION: dict[tuple[str, str], int] = {}

# Counters for tests / the operator health view.
_STATS = {"hits": 0, "misses": 0, "coalesced": 0, "bypassed": 0}


def _ttl_seconds() -> float:
    try:
        return float(getattr(get_settings(), "READ_CACHE_TTL_S", 120))
    except Exception:  # settings must never break a read
        return 120.0


def bump_generation(*, tenant_id: str | Any, deal_id: str | Any) -> None:
    """Invalidate every cached read of ``deal_id`` after an in-place write
    the state fingerprint cannot see (an ``extraction_results.fields``
    rewrite). Cheap and idempotent; safe to call on any write."""
    key = (str(tenant_id), str(deal_id))
    _GENERATION[key] = _GENERATION.get(key, 0) + 1


def reset_read_cache() -> None:
    """Drop every entry, generation and counter — for test isolation."""
    _ENTRIES.clear()
    _INFLIGHT.clear()
    _GENERATION.clear()
    for k in _STATS:
        _STATS[k] = 0


def read_cache_stats() -> dict[str, int]:
    return dict(_STATS, entries=len(_ENTRIES))


def _digest(parts: list[Any]) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(repr(part).encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()


async def deal_state_fingerprint(
    session: AsyncSession, *, deal_id: str | Any, tenant_id: str | Any
) -> str | None:
    """Hash of everything the input loaders read for ``deal_id``.

    Every statement carries the tenant predicate (``tenant_middleware``).
    Returns ``None`` when any read fails — the caller then bypasses the
    cache rather than risk serving a stale answer.
    """
    deal, tenant = str(deal_id), str(tenant_id)
    params = {"id": deal, "tenant": tenant}
    try:
        deal_row = (
            await session.execute(
                text("SELECT * FROM deals WHERE id = :id AND tenant_id = :tenant"),
                params,
            )
        ).first()
        docs = (
            await session.execute(
                text(
                    "SELECT * FROM documents "
                    "WHERE deal_id = :id AND tenant_id = :tenant ORDER BY id"
                ),
                params,
            )
        ).fetchall()
        extractions = (
            await session.execute(
                text(
                    "SELECT id, document_id, created_at FROM extraction_results "
                    "WHERE deal_id = :id AND tenant_id = :tenant ORDER BY id"
                ),
                params,
            )
        ).fetchall()
        runs = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n, MAX(started_at) AS s, MAX(completed_at) AS c "
                    "FROM engine_outputs WHERE deal_id = :id AND tenant_id = :tenant"
                ),
                params,
            )
        ).first()
        portfolio = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n, MAX(updated_at) AS u "
                    "FROM portfolio_library WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant},
            )
        ).first()
        # FON-116 — the tenant's data policies (the CoStar licensing gate)
        # are an input to the loaders, so a policy flip must invalidate every
        # cached read that could have depended on it. Read straight from the
        # row (not the registry cache) so a flip on another replica lands
        # here at once. No row (unregistered tenant) is a stable part.
        policy = (
            await session.execute(
                text("SELECT data_policies FROM tenants WHERE id = :tenant"),
                {"tenant": tenant},
            )
        ).first()
    except Exception as exc:  # a fingerprint failure only disables caching
        logger.info("read_cache: fingerprint unavailable for deal=%s: %s", deal, exc)
        return None

    parts: list[Any] = [
        sorted(dict(deal_row._mapping).items()) if deal_row is not None else None,
        [sorted(dict(r._mapping).items()) for r in docs],
        [tuple(r._mapping.values()) for r in extractions],
        tuple(runs._mapping.values()) if runs is not None else None,
        tuple(portfolio._mapping.values()) if portfolio is not None else None,
        str(policy[0]) if policy is not None else None,
    ]
    return _digest(parts)


def _copy[T](value: T) -> T:
    copier = getattr(value, "model_copy", None)
    if callable(copier):
        try:
            return copier(deep=True)
        except Exception:
            return value
    return value


async def cached_deal_read[T](
    session: AsyncSession,
    *,
    name: str,
    deal_id: str | Any,
    tenant_id: str | Any,
    compute: Callable[[], Awaitable[T]],
) -> T:
    """Serve ``compute()``'s result from the cache while the deal is unchanged.

    ``name`` distinguishes the reads cached for one deal (``"variance"``,
    ``"assumption_sources"``). ``compute`` runs at most once per distinct
    ``(fingerprint, generation)`` even under concurrent requests.
    """
    ttl = _ttl_seconds()
    if ttl <= 0:
        _STATS["bypassed"] += 1
        return await compute()

    tenant, deal = str(tenant_id), str(deal_id)
    fingerprint = await deal_state_fingerprint(session, deal_id=deal, tenant_id=tenant)
    if fingerprint is None:
        _STATS["bypassed"] += 1
        return await compute()

    generation = _GENERATION.get((tenant, deal), 0)
    key = (name, tenant, deal)
    now = time.monotonic()
    entry = _ENTRIES.get(key)
    if (
        entry is not None
        and entry.fingerprint == fingerprint
        and entry.generation == generation
        and entry.expires_at > now
    ):
        _STATS["hits"] += 1
        return _copy(entry.value)

    inflight_key = (name, tenant, deal, fingerprint, generation)
    pending = _INFLIGHT.get(inflight_key)
    if pending is not None:
        _STATS["coalesced"] += 1
        return _copy(await pending)

    _STATS["misses"] += 1
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    _INFLIGHT[inflight_key] = future
    try:
        value = await compute()
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
            # Mark retrieved so an un-awaited future never logs
            # "exception was never retrieved" on top of the real error.
            future.exception()
        raise
    else:
        if not future.done():
            future.set_result(value)
        _ENTRIES[key] = _Entry(
            fingerprint=fingerprint,
            generation=generation,
            expires_at=time.monotonic() + ttl,
            value=value,
        )
        return _copy(value)
    finally:
        _INFLIGHT.pop(inflight_key, None)


__all__ = [
    "bump_generation",
    "cached_deal_read",
    "deal_state_fingerprint",
    "read_cache_stats",
    "reset_read_cache",
]
