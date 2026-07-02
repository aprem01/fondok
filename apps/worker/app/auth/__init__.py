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
from .context import AuthContext, normalize_clerk_role

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
       path (which would mask a bad token as a permission problem).
    2. ``X-Tenant-Id: <uuid|org_...>`` — coerce → ``source="header"``,
       ``role="unknown"``. This is the backwards-compat path server-side
       callers rely on.
    3. Neither  — ``settings.DEFAULT_TENANT_ID`` → ``source="default"``,
       ``role="unknown"``. Keeps the unauthenticated demo persona working.

    401 is only raised for path 1's *failed* verification. Path 1 with
    no bearer token at all falls straight through to path 2 → path 3.
    """
    # Local import to keep the ``deals`` module the single source of
    # truth for _coerce_tenant_id / _CLERK_ORG_UUID_NAMESPACE. Prevents
    # a circular import at module-load time (deals imports auth for
    # the dependency).
    from ..api.deals import _coerce_tenant_id

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
        role = normalize_clerk_role(org_role_raw)

        # Tenant resolution: prefer the JWT's org_id (canonical). If
        # the token is a personal-account token (no org_id), fall
        # back to X-Tenant-Id, then to the default tenant. This lets
        # a solo Clerk user still hit the app.
        tenant_id: UUID | None = None
        if org_id:
            tenant_id = _coerce_tenant_id(org_id)
        if tenant_id is None and x_tenant_id:
            tenant_id = _coerce_tenant_id(x_tenant_id)
        if tenant_id is None:
            tenant_id = UUID(settings.DEFAULT_TENANT_ID)

        return AuthContext(
            tenant_id=tenant_id,
            user_id=str(user_id) if user_id else None,
            role=role,
            source="jwt",
            org_id=str(org_id) if org_id else None,
        )

    # No JWT — try the header path.
    if x_tenant_id:
        coerced = _coerce_tenant_id(x_tenant_id)
        if coerced is not None:
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
    "get_current_auth",
    "get_current_actor_id",
    "require_role",
]
