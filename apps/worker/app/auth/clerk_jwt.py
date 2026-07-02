"""Clerk session JWT verifier.

Contract with :mod:`app.auth`:

* :func:`verify_clerk_jwt` returns a dict of extracted claims on
  success or ``None`` on any failure.
* Fail-closed on every error path: bad signature, expired, missing
  key, JWKS fetch error, malformed token — all return ``None`` and log
  at ``WARNING``. The caller is responsible for deciding how to
  respond (401 vs fall-through to the header path).

Caching
-------
Clerk publishes its JWKS at ``CLERK_JWKS_URL`` and rotates keys on a
~24h cadence. Fetching the JWKS on every request would (a) add ~200 ms
of network latency to every mutation and (b) get us rate-limited.
Instead we cache the parsed JWKS in-process for
``settings.CLERK_JWKS_CACHE_TTL_S`` seconds (default 300), and on a
``kid``-not-found miss we bust the cache and re-fetch once. This is
the "kid-refresh" pattern PyJWKClient uses; we implement it inline so
we can control TTL + logging without pulling PyJWKClient's HTTP client
(which is synchronous and doesn't share our httpx pool).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

try:
    import jwt as pyjwt
    from jwt.algorithms import RSAAlgorithm
except ImportError:  # pragma: no cover — dep is declared, but fail loudly if missing
    pyjwt = None  # type: ignore[assignment]
    RSAAlgorithm = None  # type: ignore[assignment]

from ..config import get_settings

logger = logging.getLogger(__name__)


# ─────────────────────── JWKS cache ───────────────────────

# In-process cache: ``{kid: <public_key_object>}`` plus the timestamp
# at which the cache was populated. Kept module-global (not per-Settings
# instance) so a hot uvicorn worker only hits Clerk once per TTL.
_JWKS_KEYS: dict[str, Any] = {}
_JWKS_FETCHED_AT: float = 0.0


def _reset_jwks_cache() -> None:
    """Clear the module-level JWKS cache. Used by tests + on kid miss."""
    global _JWKS_KEYS, _JWKS_FETCHED_AT
    _JWKS_KEYS = {}
    _JWKS_FETCHED_AT = 0.0


def _cache_is_fresh() -> bool:
    """True when the cache was populated within the configured TTL."""
    if not _JWKS_KEYS:
        return False
    ttl = get_settings().CLERK_JWKS_CACHE_TTL_S
    return (time.monotonic() - _JWKS_FETCHED_AT) < ttl


def _load_jwks(*, force: bool = False) -> dict[str, Any]:
    """Fetch + parse Clerk's JWKS, memoized.

    Returns a ``{kid: public_key}`` dict. Empty dict on any fetch or
    parse error — every downstream caller falls back cleanly when the
    key isn't in the dict.

    Parameters
    ----------
    force
        Bypass the TTL check and re-fetch. Used by the kid-refresh
        retry path.
    """
    global _JWKS_KEYS, _JWKS_FETCHED_AT
    if not force and _cache_is_fresh():
        return _JWKS_KEYS
    if RSAAlgorithm is None:
        logger.warning("clerk_jwt: pyjwt[crypto] not installed; JWKS load skipped")
        return {}

    url = get_settings().CLERK_JWKS_URL
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — verifier must fail-closed, not raise
        logger.warning("clerk_jwt: JWKS fetch failed url=%s err=%s", url, exc)
        return {}

    keys: dict[str, Any] = {}
    for jwk in payload.get("keys", []):
        kid = jwk.get("kid")
        if not kid:
            continue
        try:
            keys[kid] = RSAAlgorithm.from_jwk(jwk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clerk_jwt: skip jwk kid=%s err=%s", kid, exc)

    _JWKS_KEYS = keys
    _JWKS_FETCHED_AT = time.monotonic()
    logger.info("clerk_jwt: loaded %d JWKS key(s) from %s", len(keys), url)
    return keys


# ─────────────────────── verify entry point ───────────────────────


def _get_key_for_token(token: str) -> Any | None:
    """Return the public key matching this token's ``kid``, refreshing on miss."""
    if pyjwt is None:
        return None
    try:
        header = pyjwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clerk_jwt: unreadable JWT header err=%s", exc)
        return None
    kid = header.get("kid")
    if not kid:
        logger.warning("clerk_jwt: JWT missing kid header")
        return None
    keys = _load_jwks()
    key = keys.get(kid)
    if key is not None:
        return key
    # Cache miss on a live kid — could be a fresh rotation. Bust and retry once.
    logger.info("clerk_jwt: kid=%s not in cache; forcing JWKS refresh", kid)
    keys = _load_jwks(force=True)
    return keys.get(kid)


def verify_clerk_jwt(token: str) -> dict[str, Any] | None:
    """Verify a Clerk session JWT.

    Parameters
    ----------
    token
        The raw JWT string (without the ``Bearer `` prefix).

    Returns
    -------
    dict | None
        Decoded claims on success — including ``sub`` (user id),
        ``org_id``, ``org_role``, ``org_slug``. ``None`` on any failure
        (bad signature, expired, missing kid, JWKS unavailable). All
        failures log at WARNING with the reason so ops can correlate.
    """
    if pyjwt is None:
        logger.warning("clerk_jwt: pyjwt not installed; verification unavailable")
        return None
    if not token or not isinstance(token, str):
        return None

    key = _get_key_for_token(token)
    if key is None:
        logger.warning("clerk_jwt: no public key for token (JWKS miss)")
        return None

    settings = get_settings()
    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "options": {"verify_signature": True, "verify_exp": True, "verify_nbf": True},
    }
    if settings.CLERK_JWT_ISSUER:
        decode_kwargs["issuer"] = settings.CLERK_JWT_ISSUER
    if settings.CLERK_JWT_AUDIENCE:
        decode_kwargs["audience"] = settings.CLERK_JWT_AUDIENCE
    else:
        # Clerk session tokens historically didn't carry aud. Skip the
        # check unless the operator has opted in via env, otherwise
        # decode() would raise MissingRequiredClaimError.
        decode_kwargs["options"]["verify_aud"] = False

    try:
        claims: dict[str, Any] = pyjwt.decode(token, key, **decode_kwargs)
    except Exception as exc:  # noqa: BLE001 — every failure = None
        # Distinguish common cases in the log so ops can spot ratchet
        # misconfig (expired clock skew) from an attack (bad signature).
        logger.warning(
            "clerk_jwt: verify failed err_type=%s err=%s",
            type(exc).__name__,
            exc,
        )
        return None

    return claims


__all__ = ["verify_clerk_jwt", "_reset_jwks_cache", "_load_jwks"]
