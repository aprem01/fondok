"""Export endpoints — Excel acquisition model, IC memo PDF, IC deck PPTX.

Each endpoint loads the deal payload (currently a hard-coded Kimpton
Angler fixture; switches to a DB read once the agent runtime persists
EngineOutputs), invokes the matching builder in ``app.export``, and
streams the resulting file back via ``FileResponse`` with the right
MIME type.

The route accepts ``deal_id`` as a free-form string and coerces it to
a UUID internally. Fondok deals are sometimes addressed by slug
(``kimpton-angler-2026``) and sometimes by UUID; FastAPI rejecting
non-UUID strings here was returning 422s for every export attempt.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from uuid import UUID, uuid5

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..export import build_excel, build_memo_pdf, build_pptx
from ..export.fixtures import load_demo_payload

logger = logging.getLogger(__name__)
router = APIRouter()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"


_DEAL_NAMESPACE = UUID("00000000-0000-0000-0000-000000000000")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _coerce_deal_uuid(deal_id: str) -> UUID:
    """Accept either a UUID string or an opaque slug.

    Slugs (e.g. ``kimpton-angler-2026``) are mapped through ``uuid5`` so
    the same slug always resolves to the same UUID — matches the same
    coercion the engine runner uses elsewhere in the worker.
    """
    try:
        return UUID(deal_id)
    except (TypeError, ValueError):
        return uuid5(_DEAL_NAMESPACE, deal_id)


def _safe_filename_part(deal_id: str) -> str:
    """Sanitize a deal identifier for use inside a filename."""
    return _FILENAME_SAFE.sub("-", deal_id)[:120] or "deal"


def _tmp_path(deal_id: str, suffix: str) -> Path:
    """Stable per-deal temp file (overwritten on each export call)."""
    base = Path(tempfile.gettempdir()) / "fondok-exports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{_safe_filename_part(deal_id)}{suffix}"


@router.get("/{deal_id}/export/excel")
async def export_excel(deal_id: str) -> FileResponse:
    """Build and stream the multi-tab Excel acquisition model."""
    deal_uuid = _coerce_deal_uuid(deal_id)
    _deal, model, _memo = load_demo_payload(deal_id)
    out = _tmp_path(deal_id, ".xlsx")
    build_excel(deal_uuid, model, out)
    logger.info("excel export built deal=%s size=%s", deal_id, out.stat().st_size)
    return FileResponse(
        path=str(out),
        media_type=XLSX_MIME,
        filename=f"fondok-acquisition-model-{_safe_filename_part(deal_id)}.xlsx",
    )


@router.get("/{deal_id}/export/memo.pdf")
async def export_memo_pdf(deal_id: str) -> FileResponse:
    """Build and stream the IC memo PDF."""
    _coerce_deal_uuid(deal_id)
    _deal, model, memo = load_demo_payload(deal_id)
    out = _tmp_path(deal_id, "-memo.pdf")
    build_memo_pdf(memo, model, out)
    logger.info("memo pdf built deal=%s size=%s", deal_id, out.stat().st_size)
    return FileResponse(
        path=str(out),
        media_type=PDF_MIME,
        filename=f"fondok-ic-memo-{_safe_filename_part(deal_id)}.pdf",
    )


@router.get("/{deal_id}/export/presentation.pptx")
async def export_pptx(deal_id: str) -> FileResponse:
    """Build and stream the 8-slide IC presentation."""
    _coerce_deal_uuid(deal_id)
    deal, model, memo = load_demo_payload(deal_id)
    out = _tmp_path(deal_id, "-deck.pptx")
    build_pptx(deal, model, memo, out)
    logger.info("pptx built deal=%s size=%s", deal_id, out.stat().st_size)
    return FileResponse(
        path=str(out),
        media_type=PPTX_MIME,
        filename=f"fondok-ic-deck-{_safe_filename_part(deal_id)}.pptx",
    )
