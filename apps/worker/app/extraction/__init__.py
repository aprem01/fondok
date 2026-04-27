"""Document extraction subsystem.

Real PDF parsing pipeline: LlamaParse (when ``LLAMA_CLOUD_API_KEY`` is
set) with a PyMuPDF + pdfplumber fallback. Returns a normalized
``ParsedDocument`` envelope so downstream agents (Extractor,
Normalizer) operate on a single shape regardless of which parser ran.
"""

from .models import ParsedDocument, ParsedPage, ParseError
from .parser import parse_pdf

__all__ = [
    "ParsedDocument",
    "ParsedPage",
    "ParseError",
    "parse_pdf",
]
