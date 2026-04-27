"""PDF parsing — LlamaParse first, PyMuPDF + pdfplumber fallback.

Routing logic
-------------
1. If ``LLAMA_CLOUD_API_KEY`` is set in the environment, attempt
   LlamaParse (page-aware, table-aware markdown extraction). On any
   import / network / API failure we drop to the local fallback rather
   than failing the upload.
2. Otherwise (or after a LlamaParse failure) parse with PyMuPDF for
   per-page text and pdfplumber for table cells.

Both paths emit a ``ParsedDocument`` so callers don't branch on
backend. ``parser`` and ``metadata`` carry provenance so we can
diagnose extraction quality after the fact.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from .models import ParsedDocument, ParsedPage, ParseError

logger = logging.getLogger(__name__)


# ──────────────────────────── public ────────────────────────────


async def parse_pdf(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Parse ``file_bytes`` into a ``ParsedDocument``.

    Runs the actual parser in a thread to keep the FastAPI event loop
    responsive — both PyMuPDF and pdfplumber are blocking C-extension
    workloads.
    """
    if not file_bytes:
        raise ParseError(f"empty file bytes for {filename}")

    content_hash = hashlib.sha256(file_bytes).hexdigest()
    api_key = os.environ.get("LLAMA_CLOUD_API_KEY")

    if api_key:
        try:
            return await asyncio.to_thread(
                _parse_with_llamaparse,
                file_bytes=file_bytes,
                filename=filename,
                content_hash=content_hash,
                api_key=api_key,
            )
        except Exception as exc:  # noqa: BLE001 — fallback is the safety net
            logger.warning(
                "parse_pdf: LlamaParse failed for %s (%s); falling back to PyMuPDF",
                filename,
                exc,
            )

    return await asyncio.to_thread(
        _parse_with_pymupdf,
        file_bytes=file_bytes,
        filename=filename,
        content_hash=content_hash,
    )


# ──────────────────────────── llamaparse ────────────────────────────


def _parse_with_llamaparse(
    *,
    file_bytes: bytes,
    filename: str,
    content_hash: str,
    api_key: str,
) -> ParsedDocument:
    """Synchronous LlamaParse path. Imported lazily.

    LlamaParse returns a list of Document objects (one per page in
    ``markdown`` mode). We harvest plain text from ``.text`` and pull
    pipe-tables out of the markdown for the ``tables`` channel.
    """
    try:
        from llama_parse import LlamaParse  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover — exercised when pkg missing
        raise ParseError(
            "llama-parse package not installed; cannot use LlamaParse"
        ) from exc

    import tempfile
    from pathlib import Path

    parser = LlamaParse(
        api_key=api_key,
        result_type="markdown",
        verbose=False,
    )

    # LlamaParse's load_data wants a path. Stash bytes in a tempfile
    # since we accept arbitrary uploads (including in-memory testing).
    with tempfile.NamedTemporaryFile(
        suffix=Path(filename).suffix or ".pdf", delete=False
    ) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        documents = parser.load_data(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    pages: list[ParsedPage] = []
    for i, doc in enumerate(documents, start=1):
        text = getattr(doc, "text", "") or ""
        tables = _extract_markdown_tables(text)
        pages.append(
            ParsedPage(
                page_num=i,
                text=text,
                tables=tables,
                metadata={"source": "llamaparse"},
            )
        )

    return ParsedDocument(
        filename=filename,
        total_pages=len(pages),
        pages=pages,
        content_hash=content_hash,
        parsed_at=datetime.now(UTC),
        parser="llamaparse",
        metadata={"backend": "llamaparse"},
    )


def _extract_markdown_tables(markdown: str) -> list[list[list[str]]]:
    """Pull pipe-formatted tables out of a markdown blob.

    A markdown table looks like:
        | col1 | col2 |
        |------|------|
        | a    | b    |

    We collect contiguous lines starting with ``|`` and ending with
    ``|``. Header separators (``---``) are dropped. Cells are stripped.
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []

    def _flush() -> None:
        if current:
            tables.append([row for row in current if row])
            current.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # Skip pure separator rows.
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                continue
            current.append(cells)
        else:
            _flush()
    _flush()

    # Drop tables with fewer than 2 rows — they're almost always noise.
    return [t for t in tables if len(t) >= 2]


# ──────────────────────────── pymupdf fallback ────────────────────────────


def _parse_with_pymupdf(
    *,
    file_bytes: bytes,
    filename: str,
    content_hash: str,
) -> ParsedDocument:
    """Synchronous PyMuPDF + pdfplumber path.

    PyMuPDF (``fitz``) gives us per-page text fast. pdfplumber gives
    us table cells. Either may fail per-page; we log and continue —
    a partial parse is more useful than none for the underwriter.
    """
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise ParseError(
            "pymupdf is not installed; cannot parse PDFs"
        ) from exc

    pdfplumber_pdf: Any | None = None
    try:
        import pdfplumber  # type: ignore[import-untyped]

        import io

        pdfplumber_pdf = pdfplumber.open(io.BytesIO(file_bytes))
    except ImportError:
        logger.info(
            "pdfplumber not installed; skipping table extraction for %s",
            filename,
        )
        pdfplumber_pdf = None
    except Exception as exc:  # noqa: BLE001 — pdfplumber chokes on weird PDFs
        logger.warning(
            "pdfplumber failed to open %s: %s — proceeding without tables",
            filename,
            exc,
        )
        pdfplumber_pdf = None

    pages: list[ParsedPage] = []
    parser_label = "pymupdf+pdfplumber" if pdfplumber_pdf is not None else "pymupdf"

    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
            for i, page in enumerate(pdf, start=1):
                try:
                    text = page.get_text("text") or ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "pymupdf: failed to extract text on page %d of %s: %s",
                        i,
                        filename,
                        exc,
                    )
                    text = ""

                tables: list[list[list[str]]] = []
                if pdfplumber_pdf is not None and i <= len(pdfplumber_pdf.pages):
                    try:
                        plumber_page = pdfplumber_pdf.pages[i - 1]
                        raw_tables = plumber_page.extract_tables() or []
                        for tbl in raw_tables:
                            cleaned = [
                                [(cell or "").strip() for cell in row]
                                for row in tbl
                                if any((cell or "").strip() for cell in row)
                            ]
                            if cleaned:
                                tables.append(cleaned)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "pdfplumber: table extraction failed on page %d of %s: %s",
                            i,
                            filename,
                            exc,
                        )

                pages.append(
                    ParsedPage(
                        page_num=i,
                        text=text,
                        tables=tables,
                        metadata={"source": parser_label},
                    )
                )
    finally:
        if pdfplumber_pdf is not None:
            try:
                pdfplumber_pdf.close()
            except Exception:  # noqa: BLE001
                pass

    return ParsedDocument(
        filename=filename,
        total_pages=len(pages),
        pages=pages,
        content_hash=content_hash,
        parsed_at=datetime.now(UTC),
        parser=parser_label,
        metadata={"backend": parser_label},
    )


__all__ = ["parse_pdf"]
