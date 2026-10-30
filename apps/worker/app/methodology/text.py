"""AST → plain text (the worker's renderer; what Atlas reads).

Mirrors ``apps/web/src/components/methodology/render.tsx`` in structure so
a reader of the page and a reader of the text see the same facts in the
same order: containers flatten to their children (a badge row prints its
badge and name first, a stage its number and title), slots render from the
same generated sources the web uses (``slots.py``), inline marks drop to
their text.
"""

from __future__ import annotations

from typing import Any

from .parser import Block, Inline
from .slots import badge_label, reason_label, render_block_slot_text, render_inline_slot_text


def inline_text(nodes: list[Inline]) -> str:
    out: list[str] = []
    for n in nodes:
        t = n["type"]
        if t == "text":
            out.append(n["value"])
        elif t in ("strong", "em", "link"):
            out.append(inline_text(n["children"]))
        elif t == "code":
            out.append(n["value"])
        elif t == "br":
            out.append("\n")
        elif t == "badge":
            label = badge_label(n["source"])
            out.append(f"[{label}]" if label else f"[{n['source']}]")
        elif t == "reason":
            label = reason_label(n["code"])
            out.append(f"{n['code']} ({label})" if label else n["code"])
        elif t == "icon":
            continue
        elif t == "slot":
            out.append(render_inline_slot_text(n["name"]))
        else:  # pragma: no cover — the parser emits only the kinds above
            raise ValueError(f"unknown inline node {t!r}")
    return "".join(out)


def _collapse(s: str) -> str:
    return " ".join(s.split())


def blocks_text(blocks: list[Block], *, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            lines.append(f"{pad}{_collapse(inline_text(b['inline']))}")
        elif t == "paragraph":
            lines.append(f"{pad}{_collapse(inline_text(b['inline']))}")
        elif t == "list":
            for i, item in enumerate(b["items"], 1):
                marker = f"{i}." if b["ordered"] else "-"
                lines.append(f"{pad}{marker} {_collapse(inline_text(item['inline']))}")
        elif t == "table":
            head = [_collapse(inline_text(c)) for c in b["head"]]
            lines.append(f"{pad}| " + " | ".join(head) + " |")
            lines.append(f"{pad}|" + "|".join(" --- " for _ in head) + "|")
            for row in b["rows"]:
                lines.append(f"{pad}| " + " | ".join(_collapse(inline_text(c)) for c in row) + " |")
        elif t == "slot":
            lines.extend(f"{pad}{ln}" for ln in render_block_slot_text(b["name"]).split("\n"))
        elif t == "container":
            kind = b["kind"]
            attrs: dict[str, Any] = b.get("attrs", {})
            if kind == "stage":
                lines.append(f"{pad}Stage {attrs.get('n', '')}".rstrip())
            elif kind == "badge":
                label = badge_label(attrs.get("source", "")) or attrs.get("source", "")
                lines.append(f"{pad}[{label}] {attrs.get('name', '')}".rstrip())
            lines.extend(blocks_text(b["children"], indent=indent + (2 if kind in ("badge", "stage", "column") else 0)))
        else:  # pragma: no cover
            raise ValueError(f"unknown block {t!r}")
        lines.append("")
    # trim trailing blank
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def level_text(blocks: list[Block]) -> str:
    return "\n".join(blocks_text(blocks))


__all__ = ["blocks_text", "inline_text", "level_text"]
