"""Export endpoints — Excel acquisition model, IC memo PDF, IC deck PPTX.

Each endpoint loads the deal's live payload (:func:`load_live_payload` —
the canonical engine snapshot, the cached memo with analyst overrides, the
real uploaded documents), invokes the matching builder in ``app.export``,
and streams the resulting file back via ``FileResponse`` with the right
MIME type.

There is exactly one payload source. A deal id that is not a UUID the
caller's tenant owns answers ``404 Deal not found.`` like every other deal
route — no fixture, no demo slug (go-live rule 3: no demo forks in
production modules).
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..database import get_session
from ..export import build_excel, build_memo_pdf, build_pptx
from ..export.live_payload import load_live_payload
from .deals import assert_deal_active, get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


#: What a caller sees for a deal id this tenant cannot export — a foreign or
#: unknown UUID, or any id that is not a UUID at all.
DEAL_NOT_FOUND_DETAIL = "Deal not found."


def _parse_deal_uuid(deal_id: str) -> UUID | None:
    """The deal id as a UUID, or ``None`` when it is not one.

    The route accepts ``deal_id`` as a free-form string so a non-UUID id
    answers the same ``404 Deal not found.`` as an unknown UUID instead of
    FastAPI's 422 — the two must be indistinguishable to a stranger.
    """
    try:
        return UUID(deal_id)
    except (TypeError, ValueError):
        return None


async def _load_payload(
    session: AsyncSession, deal_id: str, tenant_id: UUID
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(deal, model, memo)`` for a deal the caller's tenant owns.

    FON-93 — the deal must be on the caller's tenant. ``load_live_payload``
    never raises for missing data: every query it runs is tenant-scoped, so
    for a foreign (or unknown) deal id it would assemble an EMPTY payload and
    the route would serve a blank workbook / PDF / deck with a 200 — no data
    crossed, but a stranger got a file where every other deal route answers
    404 (found by scripts/isolation_check.py, 2026-10-17). So ownership is
    checked here first.
    """
    deal_uuid = _parse_deal_uuid(deal_id)
    if deal_uuid is None:
        logger.info("export: refused non-UUID deal id %r", deal_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DEAL_NOT_FOUND_DETAIL,
        )
    owned = (
        await session.execute(
            text("SELECT id FROM deals WHERE id = :id AND tenant_id = :tenant"),
            {"id": str(deal_uuid), "tenant": str(tenant_id)},
        )
    ).first()
    if owned is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DEAL_NOT_FOUND_DETAIL,
        )
    # FON-79 — a lifecycle draft has no outputs to export: 409 ``deal_is_draft``.
    await assert_deal_active(session, deal_id=deal_uuid, tenant_id=tenant_id)
    return await load_live_payload(session, str(deal_uuid), str(tenant_id))


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_part(deal_id: str) -> str:
    """Sanitize a deal identifier for use inside a filename."""
    return _FILENAME_SAFE.sub("-", deal_id)[:120] or "deal"


def _tmp_path(deal_id: str, suffix: str) -> Path:
    """Stable per-deal temp file (overwritten on each export call)."""
    base = Path(tempfile.gettempdir()) / "fondok-exports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{_safe_filename_part(deal_id)}{suffix}"


async def _audit_export(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    deal_id: str,
    action: str,
    file_path: Path,
    file_label: str,
) -> None:
    """Write one ``export.*_downloaded`` audit row.

    Best-effort — wraps log_audit's never-raises contract. Captures the
    rendered file size so the IT-review trail can spot anomalous exports
    (a tiny memo PDF after a healthy run usually means an empty memo).
    """
    try:
        size = file_path.stat().st_size if file_path.exists() else None
    except OSError:
        size = None
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action=action,
        resource_type="export",
        resource_id=deal_id,
        output_payload={
            "file_label": file_label,
            "size_bytes": size,
            "filename": file_path.name,
        },
        diff_summary=(
            f"downloaded {file_label} "
            f"({size:,} bytes)" if size is not None
            else f"downloaded {file_label} (size unknown)"
        ),
        tags=["export", "download"],
        metadata={"deal_id": deal_id},
    )
    await session.commit()


@router.get("/{deal_id}/export/excel")
async def export_excel(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> FileResponse:
    """Build and stream the multi-tab Excel acquisition model."""
    _deal, model, _memo = await _load_payload(session, deal_id, tenant_id)
    out = _tmp_path(deal_id, ".xlsx")
    build_excel(UUID(deal_id), model, out)
    logger.info("excel export built deal=%s size=%s", deal_id, out.stat().st_size)
    await _audit_export(
        session,
        tenant_id=tenant_id,
        deal_id=deal_id,
        action="export.excel_downloaded",
        file_path=out,
        file_label="Excel acquisition model",
    )
    return FileResponse(
        path=str(out),
        media_type=XLSX_MIME,
        filename=f"fondok-acquisition-model-{_safe_filename_part(deal_id)}.xlsx",
    )


@router.get("/{deal_id}/export/memo.pdf")
async def export_memo_pdf(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> FileResponse:
    """Build and stream the IC memo PDF."""
    _deal, model, memo = await _load_payload(session, deal_id, tenant_id)
    out = _tmp_path(deal_id, "-memo.pdf")
    build_memo_pdf(memo, model, out)
    logger.info("memo pdf built deal=%s size=%s", deal_id, out.stat().st_size)
    await _audit_export(
        session,
        tenant_id=tenant_id,
        deal_id=deal_id,
        action="export.memo_pdf_downloaded",
        file_path=out,
        file_label="IC memo PDF",
    )
    return FileResponse(
        path=str(out),
        media_type=PDF_MIME,
        filename=f"fondok-ic-memo-{_safe_filename_part(deal_id)}.pdf",
    )


@router.get("/{deal_id}/export/presentation.pptx")
async def export_pptx(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> FileResponse:
    """Build and stream the 8-slide IC presentation."""
    deal, model, memo = await _load_payload(session, deal_id, tenant_id)
    out = _tmp_path(deal_id, "-deck.pptx")
    build_pptx(deal, model, memo, out)
    logger.info("pptx built deal=%s size=%s", deal_id, out.stat().st_size)
    await _audit_export(
        session,
        tenant_id=tenant_id,
        deal_id=deal_id,
        action="export.pptx_downloaded",
        file_path=out,
        file_label="IC presentation PPTX",
    )
    return FileResponse(
        path=str(out),
        media_type=PPTX_MIME,
        filename=f"fondok-ic-deck-{_safe_filename_part(deal_id)}.pptx",
    )
