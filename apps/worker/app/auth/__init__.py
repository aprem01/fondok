"""RBAC + auth entry points for the worker.

Wave RBAC 2026-07 — two-role model:

* ``admin``   — full access, gated destructive + observability endpoints.
* ``member``  — everyday analyst; can create/read/edit deals, run models,
                view memos, upload docs. Cannot hard-delete or read the
                cost dashboard.

The frontend sends ``Authorization: Bearer <clerk_session_jwt>`` on
every worker request. When the JWT verifies we take its ``org_role``
as the truth. When there's no JWT (curl / tests / server-side
scripts / demo persona), we fall back to the pre-Clerk
``X-Tenant-Id`` header path and treat the caller as **admin-equivalent**
via :func:`require_role`'s escape hatch — the JWT path is the strict
path; the header/default paths are the trusted-caller paths.

Public surface
--------------
* :class:`AuthContext`         — the resolved identity dataclass
                                 (re-exported from :mod:`context`).
* :func:`get_current_auth`     — FastAPI dependency; canonical.
* :func:`require_role`         — role-gate factory. Wrap around a
                                 dependency to lock an endpoint down.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from ..config import get_settings
from .clerk_jwt import verify_clerk_jwt
from .context import AuthContext, current_tenant_id, normalize_clerk_role

logger = logging.getLogger(__name__)


def _extract_bearer(header: str | None) -> str | None:
    """Pull the token out of ``Authorization: Bearer <token>``.

    Case-insensitive on the scheme; strict on the shape. Returns
    ``None`` on anything malformed so the caller can fall through to
    the header path.
    """
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def get_current_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> AuthContext:
    """Resolve the caller's :class:`AuthContext`.

    Resolution order (must match the frontend contract):

    1. ``Authorization: Bearer <jwt>``  — verify + extract → ``source="jwt"``.
       On verification failure we raise **401** so the browser knows to
       refresh the token, rather than silently degrading to the header
       path (which would mask a bad token as a permission problem). The
       tenant is the token's ``org_id`` and nothing else (FON-93): no
       ``org_id`` → **409** ``no_active_organization`` even when an
       ``X-Tenant-Id`` header is present; with
       ``REQUIRE_REGISTERED_TENANT`` on, an org missing from the
       ``tenants`` registry (or not ``active``) → **403**
       ``tenant_not_registered``.
    2. ``X-Tenant-Id: <uuid|org_...>`` — coerce → ``source="header"``,
       ``role="unknown"``. Only reachable when
       ``ALLOW_TENANT_HEADER_WITHOUT_JWT`` is on (local dev / tests);
       otherwise **401**.
    3. Neither  — ``settings.DEFAULT_TENANT_ID`` → ``source="default"``,
       ``role="unknown"``. Same flag gate as path 2.

    Every path publishes the resolved tenant to
    :data:`app.auth.context.current_tenant_id` so the DB session can set
    the RLS GUC when ``DB_SET_TENANT_GUC`` is on.
    """
    # Local import to keep the ``deals`` module the single source of
    # truth for _coerce_tenant_id / _CLERK_ORG_UUID_NAMESPACE. Prevents
    # a circular import at module-load time (deals imports auth for
    # the dependency).
    from ..api.deals import _coerce_tenant_id, coerce_org_tenant_id

    settings = get_settings()

    token = _extract_bearer(authorization)
    if token is not None:
        claims = verify_clerk_jwt(token)
        if claims is None:
            # A malformed / expired / bad-sig JWT is an active auth
            # failure — surface it as 401 so the browser refreshes
            # rather than silently down-grading to the header path.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired session token",
            )
        user_id = claims.get("sub")
        org_id = claims.get("org_id")
        org_role_raw = claims.get("org_role")
        email = claims.get("email")
        role = normalize_clerk_role(org_role_raw)

        # Tenant resolution — FON-93: the JWT's ``org_id`` is the ONLY
        # tenant source on this path. Before 2026-10-14 a personal-account
        # token (no org_id) fell back to ``X-Tenant-Id``, so any signed-in
        # Clerk user could name an arbitrary tenant in a header and be
        # served it. The header is ignored here entirely; a token with no
        # active organization gets the FON-20 409 regardless of headers.
        if not org_id:
            logger.warning(
                "get_current_auth: verified JWT (sub=%s) carried no org_id "
                "(x_tenant_id=%s) — refusing; the header is not a tenant "
                "source on the JWT path (FON-93). Client must set an "
                "active organization.",
                user_id,
                "present" if x_tenant_id else "absent",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="no_active_organization",
            )
        tenant_id = coerce_org_tenant_id(str(org_id))
        if tenant_id is None:
            # An ``org_id`` claim that is not a Clerk organization id
            # (``org_…``). Nothing legitimate produces this; refuse rather
            # than guess a tenant from it.
            logger.warning(
                "get_current_auth: verified JWT (sub=%s) carried a malformed "
                "org_id=%r — refusing",
                user_id,
                org_id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="no_active_organization",
            )

        # FON-93 — tenant registry. Off by default (prod unchanged); when
        # on, an org nobody registered — or one that is suspended / still
        # ``pending_review`` from the Clerk webhook — has no data plane.
        if settings.REQUIRE_REGISTERED_TENANT:
            from ..tenants import is_registered

            if not await is_registered(tenant_id):
                logger.warning(
                    "get_current_auth: org %s (tenant %s) is not a registered, "
                    "active tenant — refusing (REQUIRE_REGISTERED_TENANT)",
                    org_id,
                    tenant_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="tenant_not_registered",
                )

        current_tenant_id.set(str(tenant_id))
        return AuthContext(
            tenant_id=tenant_id,
            user_id=str(user_id) if user_id else None,
            role=role,
            source="jwt",
            org_id=str(org_id),
            email=str(email) if email else None,
        )

    # No JWT. Before 2026-10-06 the request fell through to the header
    # path and then to DEFAULT_TENANT_ID, and both were SERVED. The header
    # path was designed for callers inside the worker's own perimeter —
    # ``require_role`` even treats it as a trusted admin — but the worker
    # is on the public internet, so a forged ``X-Tenant-Id`` read (and
    # could write) any tenant's deals with no credentials at all. Verified
    # live, read-only, during the Eshan/Rani feedback review.
    #
    # Now: refuse unless the operator has explicitly opted in. The web app
    # always sends a Bearer when Clerk is configured, so real users never
    # reach this branch; the test suite and local dev set the flag.
    if not settings.ALLOW_TENANT_HEADER_WITHOUT_JWT:
        logger.warning(
            "get_current_auth: no verified JWT (x_tenant_id=%s) — refusing "
            "the header/default tenant path (ALLOW_TENANT_HEADER_WITHOUT_JWT "
            "is off)",
            "present" if x_tenant_id else "absent",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Header path — explicitly enabled (dev / tests / trusted scripts).
    if x_tenant_id:
        coerced = _coerce_tenant_id(x_tenant_id)
        if coerced is not None:
            current_tenant_id.set(str(coerced))
            return AuthContext(
                tenant_id=coerced,
                user_id=None,
                role="unknown",
                source="header",
                org_id=x_tenant_id if x_tenant_id.startswith("org_") else None,
            )
        logger.warning(
            "get_current_auth: unrecognized X-Tenant-Id header %r — using default",
            x_tenant_id,
        )

    # Default tenant fallback.
    current_tenant_id.set(str(settings.DEFAULT_TENANT_ID))
    return AuthContext(
        tenant_id=UUID(settings.DEFAULT_TENANT_ID),
        user_id=None,
        role="unknown",
        source="default",
        org_id=None,
    )


def require_role(*required: str):
    """Return a FastAPI dependency that gates on ``auth.role``.

    Two rules govern the check — a policy decision, not an oversight:

    1. If the request came from a verified JWT (``source="jwt"``), the
       role must exactly match one of ``required``. Anything else 403s.
       This is the *strict* path — real browser traffic gets the real
       Clerk role and no free upgrade.
    2. If the request came via ``X-Tenant-Id`` or the default fallback
       (``source in {"header", "default"}``), the caller passes.
       Rationale: server-side scripts, curl-based ops runbooks, and
       the pre-Clerk demo persona need admin-tier access to keep the
       system operable. The header path is treated as a trusted
       caller — mint tokens on that surface only from the worker's
       own network perimeter.

    Parameters
    ----------
    *required
        One or more role strings the caller must match. Typical use:
        ``Depends(require_role("admin"))``.
    """

    async def dep(
        auth: Annotated[AuthContext, Depends(get_current_auth)],
    ) -> AuthContext:
        if auth.source == "jwt":
            if auth.role not in required:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"role '{auth.role}' insufficient; "
                        f"requires one of {sorted(required)}"
                    ),
                )
        # Header + default paths bypass the role gate (trusted caller).
        return auth

    return dep


async def get_current_actor_id(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> str | None:
    """Return ``auth.user_id`` for audit logging.

    Small convenience dep so an endpoint that only needs the actor id
    for ``log_audit(actor_id=...)`` doesn't have to accept the whole
    :class:`AuthContext`. Yields ``None`` on header + default paths;
    :func:`app.audit.log_audit` translates ``None`` into ``"system"``.

    Existing mutating endpoints can migrate incrementally: add
    ``actor_id: Annotated[str | None, Depends(get_current_actor_id)]``
    beside the existing ``Depends(get_tenant_id)`` and forward it into
    the ``log_audit`` call. Keeps this PR small; wider audit-trail
    adoption follows in a targeted sweep.
    """
    return auth.user_id


__all__ = [
    "AuthContext",
    "current_tenant_id",
    "get_current_actor_id",
    "get_current_auth",
    "require_role",
]
