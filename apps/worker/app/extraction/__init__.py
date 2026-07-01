"""Document extraction subsystem.

Multi-format parsing pipeline:
  * PDF — LlamaParse (when ``LLAMA_CLOUD_API_KEY`` is set) with a
    PyMuPDF + pdfplumber fallback.
  * Excel — ``xlrd`` for legacy ``.xls``, ``openpyxl`` for ``.xlsx``;
    each sheet becomes a ``ParsedPage``.

All paths return a normalized ``ParsedDocument`` envelope so downstream
agents (Extractor, Normalizer) operate on a single shape regardless of
which parser ran.
"""

from .compaction import compact_for_prompt
from .models import ParsedDocument, ParsedPage, ParseError
from .parser import parse_document, parse_pdf

__all__ = [
    "ParsedDocument",
    "ParsedPage",
    "ParseError",
    "compact_for_prompt",
    "parse_document",
    "parse_pdf",
]
