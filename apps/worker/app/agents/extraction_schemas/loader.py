"""Schema loader for the Extractor agent.

Phase 4 of the dynamic-extensibility refactor. Reads per-doc-type
Markdown schemas out of this directory and assembles them into the
Extractor's system prompt at runtime — so a new doc type becomes a
file drop instead of a Python edit + deploy.

Behavior is gated on the env var ``EXTRACTOR_USE_DYNAMIC_SCHEMAS``:

    EXTRACTOR_USE_DYNAMIC_SCHEMAS=1 → assemble from _base.md + doc-type file.
    Anything else                  → ``build_system_prompt`` returns ``None``
                                     and callers fall back to the legacy
                                     embedded SYSTEM_PROMPT.

The flag is intentionally opt-in. The legacy prompt has been hammered
by Sam's pilots; the new path becomes the default after a regression
corpus confirms byte-equivalent (or better) outputs.

Files in this directory follow the convention:

    _base.md             ← always loaded first (agent-behavior preamble).
    <doc_type>.md        ← matched case-insensitively against the
                           Router's doc_type. Lowercase, no extension.

Missing files degrade gracefully — if the doc type's file isn't
present we still return the base preamble + a short note telling the
LLM to use general extraction rules. Misclassifications get a useful
result rather than a hard failure.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_BASE_FILENAME = "_base.md"


def is_enabled() -> bool:
    """Whether the dynamic-schema path is opted in this process."""
    flag = os.environ.get("EXTRACTOR_USE_DYNAMIC_SCHEMAS", "").strip()
    return flag in ("1", "true", "yes", "on")


def _read(filename: str) -> str | None:
    """Read a file out of this directory. Returns ``None`` if missing
    (the loader treats missing schemas as fallthrough, not as error)."""
    path = _DIR / filename
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.exception("schema_loader: failed to read %s", path)
        return None


def _schema_filename(doc_type: str) -> str:
    """Map a doc_type token to its filename — lowercase, no extension."""
    return f"{(doc_type or '').strip().lower()}.md"


def available_doc_types() -> list[str]:
    """Sorted list of doc_types with a schema file present."""
    out: list[str] = []
    for p in _DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != ".md":
            continue
        if p.name in {_BASE_FILENAME, "README.md"}:
            continue
        out.append(p.stem.upper())
    return sorted(out)


def build_system_prompt(doc_type: str | None) -> str | None:
    """Assemble a system prompt for ``doc_type`` from the schema files.

    Returns the assembled prompt when the dynamic path is enabled,
    None otherwise (caller falls back to the legacy SYSTEM_PROMPT).

    Always loads ``_base.md`` first. Appends the doc-type-specific
    file when present; falls back to a short generic-extraction note
    when the file isn't there so misclassifications still get useful
    instructions.
    """
    if not is_enabled():
        return None

    base = _read(_BASE_FILENAME)
    if base is None:
        logger.error(
            "schema_loader: _base.md missing — falling back to legacy "
            "SYSTEM_PROMPT for doc_type=%s",
            doc_type,
        )
        return None

    if doc_type:
        schema = _read(_schema_filename(doc_type))
        if schema is not None:
            return f"{base}\n\n---\n\n{schema}".strip()

    # No matching schema. Append a short note telling the LLM to use
    # general extraction rules — the base preamble already covers
    # most of what's needed.
    fallback = (
        "## Generic extraction (no doc-type-specific schema available)\n\n"
        "The Router classified this document but no dedicated schema "
        "file is registered for the doc type. Use general extraction "
        "rules: emit every grounded number, identifier, and date "
        "under the closest-matching canonical prefix (broker_proforma, "
        "p_and_l_usali, asking_price, property_overview, in_place_debt, "
        "ttm_performance, cbre_horizons, pnl_benchmark, transaction_comps). "
        "Coverage beats namespace purity."
    )
    return f"{base}\n\n---\n\n{fallback}".strip()


__all__ = [
    "is_enabled",
    "build_system_prompt",
    "available_doc_types",
]
