"""Export endpoints — Excel acquisition model, IC memo PDF, IC deck PPTX.

Each endpoint loads the deal payload (currently a hard-coded Kimpton
Angler fixture; switches to a DB read once the agent runtime persists
EngineOutputs), invokes the matching builder in ``app.export``, and
streams the resulting file back via ``FileResponse`` with the right
MIME type.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..export import build_excel, build_memo_pdf, build_pptx
from ..export.fixtures import load_demo_payload

logger = logging.getLogger(__name__)
router = APIRouter()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"


def _tmp_path(deal_id: UUID, suffix: str) -> Path:
    """Stable per-deal temp file (overwritten on each export call)."""
    base = Path(tempfile.gettempdir()) / "fondok-exports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{deal_id}{suffix}"


@router.get("/{deal_id}/export/excel")
async def export_excel(deal_id: UUID) -> FileResponse:
    """Build and stream the multi-tab Excel acquisition model."""
    _deal, model, _memo = load_demo_payload(str(deal_id))
    out = _tmp_path(deal_id, ".xlsx")
    build_excel(deal_id, model, out)
    logger.info("excel export built deal=%s size=%s", deal_id, out.stat().st_size)
    return FileResponse(
        path=str(out),
        media_type=XLSX_MIME,
        filename=f"fondok-acquisition-model-{deal_id}.xlsx",
    )


@router.get("/{deal_id}/export/memo.pdf")
async def export_memo_pdf(deal_id: UUID) -> FileResponse:
    """Build and stream the IC memo PDF."""
    _deal, model, memo = load_demo_payload(str(deal_id))
    out = _tmp_path(deal_id, "-memo.pdf")
    build_memo_pdf(memo, model, out)
    logger.info("memo pdf built deal=%s size=%s", deal_id, out.stat().st_size)
    return FileResponse(
        path=str(out),
        media_type=PDF_MIME,
        filename=f"fondok-ic-memo-{deal_id}.pdf",
    )


@router.get("/{deal_id}/export/presentation.pptx")
async def export_pptx(deal_id: UUID) -> FileResponse:
    """Build and stream the 8-slide IC presentation."""
    deal, model, memo = load_demo_payload(str(deal_id))
    out = _tmp_path(deal_id, "-deck.pptx")
    build_pptx(deal, model, memo, out)
    logger.info("pptx built deal=%s size=%s", deal_id, out.stat().st_size)
    return FileResponse(
        path=str(out),
        media_type=PPTX_MIME,
        filename=f"fondok-ic-deck-{deal_id}.pptx",
    )
