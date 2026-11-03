"""Rendering a ``USALIFinancials`` spread into agent prompts (FON-22 round 3).

A line the source does not state is listed in ``USALIFinancials.not_stated``;
its numeric slot holds 0.0 only because the schema needs a number. Every
prompt renders such a line as "not stated" — never "$0" or "100.00%" — so no
model is told a hotel earns $0 NOI (product rule 1). One helper, shared by the
critic, the analyst and the researcher.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

NOT_STATED = "not stated"


def _not_stated(fin: Any) -> frozenset[str]:
    raw = fin.get("not_stated") if isinstance(fin, Mapping) else getattr(fin, "not_stated", None)
    return frozenset(raw or ())


def _value(fin: Any, path: str) -> Any:
    node: Any = fin
    for seg in path.split("."):
        node = node.get(seg) if isinstance(node, Mapping) else getattr(node, seg, None)
        if node is None:
            return None
    return node


def money(fin: Any, path: str) -> str:
    """``$1,234`` for a stated line; "not stated" for an absent one."""
    if path in _not_stated(fin):
        return NOT_STATED
    v = _value(fin, path)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return NOT_STATED
    return f"${v:,.0f}"


def ratio(fin: Any, path: str, digits: int = 2) -> str:
    """``73.10%`` for a stated ratio; "not stated" for an absent one."""
    if path in _not_stated(fin):
        return NOT_STATED
    v = _value(fin, path)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return NOT_STATED
    return f"{v:.{digits}%}"


def source_line(fin: Any) -> str | None:
    """"Source statement: <file>" when the spread names its statement."""
    src = fin.get("source_statement") if isinstance(fin, Mapping) else getattr(fin, "source_statement", None)
    return f"Source statement: {src} (the statement the model uses)" if src else None


__all__ = ["NOT_STATED", "money", "ratio", "source_line"]
