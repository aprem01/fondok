"""``GET /methodology?level=…`` — the methodology package, by detail level (FON-120).

Three levels, three gates (go-live plan C.3):

* ``conceptual`` — any signed-in caller (``get_current_auth``); the same text
  the public page renders from its bundle. Served here so a client that
  wants it from the API (and Atlas, internally) reads one source.
* ``detailed`` — formulas, defaults and precedence WITH values. Deliberately
  not in the web bundle: /methodology/detail fetches it from here, and this
  route requires an ``admin`` of a registered, active **internal** tenant —
  the stricter of the two gates the plan discussed, the one ``/admin/health``
  uses. Sam may widen it to any tenant's admin later; the check is one line.
* ``engineering`` — field paths, routes, env vars. Same internal-admin gate.

A member on the JWT path gets 403 on ``detailed``/``engineering``; an admin of
an external tenant gets the same 403 and is not told what they are missing.
The response is the parsed AST the web renders (``MethodologyLevelResponse``
in ``apps/web/src/lib/methodology/types.ts``), numbered the way the page
numbers that level's sections.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthContext, get_current_auth, require_role
from ..methodology import get_package, load_error
from ..tenants import get_tenant

router = APIRouter()

Level = Literal["conceptual", "detailed", "engineering"]

METHODOLOGY_REQUIRES_INTERNAL_ADMIN = "methodology_level_requires_internal_admin"
METHODOLOGY_UNAVAILABLE = "methodology_unavailable"

_require_admin = require_role("admin")


class MethodologyPageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eyebrow: str
    title: str
    subtitle: str


class MethodologySectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    number: int
    title: str
    decisions: list[str] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class MethodologyLevelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Level
    page: MethodologyPageMeta
    sections: list[MethodologySectionOut]


async def _gate_internal_admin(auth: AuthContext) -> None:
    """``require_role("admin")`` (JWT members → 403; header/default paths are
    trusted callers, as everywhere) and then the internal-tenant check the
    operator health view uses."""
    await _require_admin(auth)
    me = await get_tenant(auth.tenant_id)
    if me is None or not me.is_serving or not me.is_internal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=METHODOLOGY_REQUIRES_INTERNAL_ADMIN,
        )


@router.get("/methodology", response_model=MethodologyLevelResponse)
async def get_methodology(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    level: Annotated[Level, Query()] = "conceptual",
) -> MethodologyLevelResponse:
    if level != "conceptual":
        await _gate_internal_admin(auth)
    pkg = get_package()
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": METHODOLOGY_UNAVAILABLE, "reason": load_error() or "package not loaded"},
        )
    page = pkg.pages.get(level) or {}
    sections: list[MethodologySectionOut] = []
    for s in pkg.sections_with(level):
        number = pkg.number_of(s.id, level)
        assert number is not None
        sections.append(
            MethodologySectionOut(
                id=s.id,
                number=number,
                title=s.level_title(level),
                decisions=list(s.decisions),
                anchors=list(s.anchors),
                blocks=s.levels[level].blocks,
            )
        )
    return MethodologyLevelResponse(
        level=level,
        page=MethodologyPageMeta(
            eyebrow=str(page.get("eyebrow", "Methodology")),
            title=str(page.get("title", "Methodology")),
            subtitle=str(page.get("subtitle", "")),
        ),
        sections=sections,
    )


__all__ = [
    "METHODOLOGY_REQUIRES_INTERNAL_ADMIN",
    "METHODOLOGY_UNAVAILABLE",
    "MethodologyLevelResponse",
    "router",
]
