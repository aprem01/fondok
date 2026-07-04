"""Immutable auth context — the resolved identity for one request.

The worker accepts three auth "sources" (in priority order):

1. ``jwt``    — a verified Clerk session token on ``Authorization``.
                Canonical path for real browser traffic. Carries user
                + org + role.
2. ``header`` — a raw ``X-Tenant-Id`` value that coerces to a UUID via
                :func:`app.api.deals._coerce_tenant_id`. Backwards-compat
                for server-side callers, curl, tests, and the demo
                persona pre-Clerk. Role is ``"unknown"`` — the escape
                hatch in :func:`require_role` treats this as trusted.
3. ``default``— nothing was sent. Falls back to
                ``settings.DEFAULT_TENANT_ID`` so an unauthenticated
                dev hit renders the demo tenant. Role is ``"unknown"``.

Roles are normalized to plain strings so no caller has to remember the
Clerk ``org:admin`` / ``org:member`` prefix. See
:func:`normalize_clerk_role` for the mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

# Sources the worker recognizes. ``Literal`` is not enforced by the
# dataclass — Python dataclasses don't validate — but it keeps mypy
# honest and gives call sites a hint via IDE completion.
AuthSource = Literal["jwt", "header", "default"]

# Role vocabulary the worker consumes. ``admin`` gates the destructive
# + cost-observability endpoints; ``member`` is the everyday analyst;
# ``unknown`` is what we emit when we can't tell (header-only or
# JWT-without-org). :func:`require_role` and the escape-hatch logic in
# :mod:`app.auth.__init__` know how to reason about ``unknown``.
Role = Literal["admin", "member", "unknown"]


@dataclass(frozen=True)
class AuthContext:
    """Resolved identity for the current request. Immutable.

    Parameters
    ----------
    tenant_id
        UUID the request's data lives under. Always populated — the
        default-tenant fallback guarantees this.
    user_id
        Clerk user id (``user_XXXXX...``) when a valid JWT was
        presented; ``None`` for header-only + default paths. Threaded
        into ``audit_log.actor_id`` so we can answer "who did this?".
    role
        ``"admin"``, ``"member"``, or ``"unknown"``. Normalized from
        the Clerk-style ``org:admin`` / ``org:member`` — see
        :func:`normalize_clerk_role`.
    source
        Which auth path was used. Callers rarely need this directly,
        but the escape-hatch role gate uses it to decide whether an
        ``unknown``-role request should be treated as admin (yes for
        ``header`` / ``default``; no for ``jwt``).
    org_id
        Raw Clerk org id (``org_XXXXX...``) when the JWT carries an
        active organization. ``None`` for personal-account JWTs +
        header + default. Preserved for audit + future use.
    email
        User's email address (from custom JWT claim) when a valid JWT
        was presented; ``None`` for header-only, default paths, or if
        the JWT template doesn't include email.
    """

    tenant_id: UUID
    user_id: str | None
    role: str
    source: str
    org_id: str | None = None
    email: str | None = None


def normalize_clerk_role(raw: str | None) -> str:
    """Map Clerk's ``org:admin`` / ``org:member`` to plain strings.

    Clerk emits organization roles as ``org:<slug>``. We flatten to
    the bare slug so downstream code (the ``require_role`` factory,
    the frontend contract, audit metadata) doesn't have to carry the
    prefix around. Unknown / missing → ``"unknown"``.

    Parameters
    ----------
    raw
        The ``org_role`` claim value from the Clerk JWT (or ``None``
        when the token doesn't carry one — personal-account tokens).

    Returns
    -------
    str
        ``"admin"``, ``"member"``, or ``"unknown"``. Any other slug
        (custom Clerk role, e.g. ``org:analyst``) is returned bare
        (``"analyst"``) so callers can extend the vocabulary without
        a code change here.
    """
    if not raw:
        return "unknown"
    stripped = raw.strip()
    if not stripped:
        return "unknown"
    # ``org:admin`` → ``admin``; ``org:member`` → ``member``; also
    # tolerate an already-bare ``admin`` in case Clerk changes shape.
    if ":" in stripped:
        _, _, slug = stripped.partition(":")
        return slug.lower() or "unknown"
    return stripped.lower()


__all__ = ["AuthContext", "AuthSource", "Role", "normalize_clerk_role"]
