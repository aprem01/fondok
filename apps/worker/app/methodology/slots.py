"""Slot vocabulary and the worker-side (plain text) slot renderers.

A slot is a block the markdown cannot carry as prose because the facts live
in code: the reason-code table, the concept registry table, the assumption
badge labels, the management-fee guidance. The web renders each slot with a
TSX component (``components/methodology/slots.tsx``, keyed by the same
names); the worker renders it to text here from the SAME generated sources
— ``fondok_schemas.reasons`` / ``app/ontology/concepts.yaml`` through the
registry, and ``packages/methodology/data/management_fee_guidance.yaml`` —
so Atlas reads the facts the page shows.

Both renderers are keyed by :data:`BLOCK_SLOTS` / :data:`INLINE_SLOTS`; the
loader refuses a section that names a slot neither side knows, and the web
test ``methodologyLevels.test.ts`` asserts every slot token is rendered.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BLOCK_SLOTS: frozenset[str] = frozenset(
    {
        "reason-code-table",
        "reason-code-table-engineering",
        "concept-registry-table",
        "mgmt-fee-guidance",
    }
)
INLINE_SLOTS: frozenset[str] = frozenset(
    {
        "registry-version",
        "registry-count",
        "mgmt-fee-disclaimer",
    }
)
KNOWN_SLOTS: frozenset[str] = BLOCK_SLOTS | INLINE_SLOTS

#: lucide icon names the ``{{icon:…}}`` token / ``icon=`` attribute may name.
KNOWN_ICONS: frozenset[str] = frozenset(
    {"FileText", "Sparkles", "BarChart3", "Database", "Link2", "ArrowRight", "Pencil", "Map", "BookOpen"}
)

_PAREN_KEYS_RE = re.compile(r"\s*\((?:[a-z][a-z0-9]*_[a-z0-9_]*(?:\s*/\s*)?)+\)")


def client_reason_explanation(explanation: str) -> str:
    """The registry explanation with any parenthesised engine-key list
    removed — the same transform as ``apps/web/src/lib/reasonCopy.ts``."""
    return re.sub(r"\s{2,}", " ", _PAREN_KEYS_RE.sub("", explanation)).strip()


def _registry():
    from ..ontology.registry import get_registry

    return get_registry()


def badge_label(source: str) -> str | None:
    """The badge text ``AssumptionBadge`` prints for ``source`` (registry
    ``sources.<id>.badge``); ``None`` for an unknown source."""
    src = _registry().sources.get(source)
    return src.badge if src is not None else None


def reason_label(code: str) -> str | None:
    meta = _registry().reasons.get(code)
    return meta.label if meta is not None else None


def known_sources() -> frozenset[str]:
    return frozenset(_registry().sources.keys())


def known_reasons() -> frozenset[str]:
    return frozenset(_registry().reasons.keys())


@lru_cache(maxsize=1)
def management_fee_guidance(path: str | None = None) -> dict[str, Any]:
    """``{disclaimer, models: [{id, label, guidance}]}`` from the package data file."""
    from .loader import package_root

    p = Path(path) if path else package_root() / "data" / "management_fee_guidance.yaml"
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict) or "disclaimer" not in data or not isinstance(data.get("models"), list):
        raise ValueError(f"{p}: expected 'disclaimer' and a 'models' list")
    for m in data["models"]:
        for k in ("id", "label", "guidance"):
            if not isinstance(m.get(k), str) or not m[k].strip():
                raise ValueError(f"{p}: every model needs a non-empty '{k}'")
    return data


# ───────────────────────────── text renderers ──────────────────────────────


def render_block_slot_text(name: str) -> str:
    if name == "reason-code-table":
        reg = _registry()
        rows = ["| Shown | Meaning | What it tells you |", "| --- | --- | --- |"]
        for code, meta in reg.reasons.items():
            rows.append(f"| {meta.ui} | {meta.label} | {client_reason_explanation(meta.explanation)} |")
        return "\n".join(rows)
    if name == "reason-code-table-engineering":
        reg = _registry()
        rows = ["| Shown | Code | Meaning | What it tells you |", "| --- | --- | --- | --- |"]
        for code, meta in reg.reasons.items():
            rows.append(f"| {meta.ui} | {code} | {meta.label} | {meta.explanation} |")
        return "\n".join(rows)
    if name == "concept-registry-table":
        reg = _registry()
        rows = ["| Concept | USALI line | Unit | Period | Engines |", "| --- | --- | --- | --- | --- |"]
        for cid, c in reg.concepts.items():
            if c.group == "synthetic":
                continue
            usali = f"{c.usali.line} ({c.usali.section})" if c.usali is not None else "—"
            rows.append(f"| {c.label} ({cid}) | {usali} | {c.unit} | {c.period} | {', '.join(c.engines) or '—'} |")
        return "\n".join(rows)
    if name == "mgmt-fee-guidance":
        data = management_fee_guidance()
        return "\n".join(f"- {m['label']}. {m['guidance']}" for m in data["models"])
    raise KeyError(name)


def render_inline_slot_text(name: str) -> str:
    if name == "registry-version":
        return str(_registry().version)
    if name == "registry-count":
        return str(sum(1 for c in _registry().concepts.values() if c.group != "synthetic"))
    if name == "mgmt-fee-disclaimer":
        return str(management_fee_guidance()["disclaimer"])
    raise KeyError(name)


__all__ = [
    "BLOCK_SLOTS",
    "INLINE_SLOTS",
    "KNOWN_ICONS",
    "KNOWN_SLOTS",
    "badge_label",
    "client_reason_explanation",
    "known_reasons",
    "known_sources",
    "management_fee_guidance",
    "reason_label",
    "render_block_slot_text",
    "render_inline_slot_text",
]
