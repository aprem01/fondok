"""Methodology package reader (FON-120).

``packages/methodology`` is the one source of the Methodology page at three
detail levels. The worker reads it at boot (``main.py`` lifespan →
:func:`load_at_startup`; ``/health`` reports ``methodology_sections``), serves
it on ``GET /methodology?level=…`` (``api/methodology.py``) and hands the
relevant sections to Atlas (``select.py`` → ``agents/researcher.py``).

The same parser drives ``scripts/gen_methodology.py``, which emits the
conceptual and engineering levels for the web bundle — ``detailed`` is
never in the bundle; the web fetches it from the route at runtime.
"""

from __future__ import annotations

import logging

from .loader import MethodologyError, MethodologyPackage, Section, load_package, package_root
from .parser import MethodologyParseError
from .select import build_methodology_block, build_selection, select_section_ids
from .text import level_text

logger = logging.getLogger(__name__)

_PACKAGE: MethodologyPackage | None = None
_LOAD_ERROR: str | None = None


def load_at_startup() -> int:
    """Load the package once; return the section count (0 when it could not
    be read — the caller records the degraded state, never raises)."""
    global _PACKAGE, _LOAD_ERROR
    try:
        _PACKAGE = load_package()
        _LOAD_ERROR = None
        return len(_PACKAGE.sections)
    except (MethodologyError, MethodologyParseError, OSError, ValueError) as exc:
        _PACKAGE = None
        _LOAD_ERROR = str(exc)
        logger.error("methodology: package failed to load from %s: %s", package_root(), exc)
        return 0


def get_package() -> MethodologyPackage | None:
    """The loaded package, loading lazily when the lifespan has not run
    (tests, scripts). ``None`` when it cannot be read."""
    if _PACKAGE is None and _LOAD_ERROR is None:
        load_at_startup()
    return _PACKAGE


def load_error() -> str | None:
    return _LOAD_ERROR


def reset_for_tests() -> None:
    global _PACKAGE, _LOAD_ERROR
    _PACKAGE = None
    _LOAD_ERROR = None


__all__ = [
    "MethodologyError",
    "MethodologyPackage",
    "MethodologyParseError",
    "Section",
    "build_methodology_block",
    "build_selection",
    "get_package",
    "level_text",
    "load_at_startup",
    "load_error",
    "load_package",
    "package_root",
    "reset_for_tests",
    "select_section_ids",
]
