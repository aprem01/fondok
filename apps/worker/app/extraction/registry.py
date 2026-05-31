"""Parser registry — data-driven dispatch from file extension to parser.

Each concrete parser registers itself by extension at import time:

    register_parser("xlsx", _parse_with_openpyxl)
    register_parser("xlsm", _parse_with_openpyxl)
    register_parser("pdf",  _parse_pdf)

The dispatcher in ``parser.parse_document`` looks the function up by
extension and calls it. Adding a new format becomes:

    # apps/worker/app/extraction/parsers/docx.py
    from ..registry import register_parser
    from ..models import ParsedDocument
    async def _parse_docx(*, file_bytes, filename, content_hash) -> ParsedDocument: ...
    register_parser("docx", _parse_docx)

…and importing the module once at process start. No edits to the
existing dispatch code.

The registry intentionally exposes a small surface:
    * ``register_parser(ext, fn)`` — add a handler for one extension.
    * ``get_parser(ext)``         — look up the handler or get ``None``.
    * ``registered_extensions()`` — sorted list, for error messages
                                     and the file picker accept-string.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .models import ParsedDocument

# Each parser is async and takes the standard kwargs the existing
# private fns already use. Keep this signature stable — third-party
# plugins will rely on it.
ParserFn = Callable[..., Awaitable[ParsedDocument]]

_REGISTRY: dict[str, ParserFn] = {}


def _normalize(ext: str) -> str:
    """Lowercase, strip the leading dot, normalize whitespace."""
    return ext.strip().lstrip(".").lower()


def register_parser(ext: str, fn: ParserFn) -> None:
    """Register ``fn`` to handle files with extension ``ext``.

    Re-registering an extension is allowed and overrides the previous
    handler — useful when a plugin wants to swap in a stronger parser
    (e.g. a paid OCR backend for PDFs).
    """
    _REGISTRY[_normalize(ext)] = fn


def get_parser(ext: str) -> ParserFn | None:
    """Look up the parser for an extension. Returns ``None`` for
    unknown extensions so callers can produce a typed error message
    rather than a stack trace.
    """
    return _REGISTRY.get(_normalize(ext))


def registered_extensions() -> list[str]:
    """Sorted list of every registered extension. The Data Room
    file-picker accept-string and the worker's error messages both
    read from this so adding a new parser updates both surfaces
    without a separate edit.
    """
    return sorted(_REGISTRY.keys())


__all__ = [
    "ParserFn",
    "register_parser",
    "get_parser",
    "registered_extensions",
]


# A tiny type-shim so callers can pass `Any` for the parser kwargs
# without mypy complaining at the call site. The registry doesn't
# enforce the kwarg shape; each parser is responsible for its own
# signature contract.
_ = Any
