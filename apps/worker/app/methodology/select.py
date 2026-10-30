"""Section selection and the Atlas methodology block (FON-120, PR C-3).

``build_methodology_block(question)`` returns the text Atlas reads: the
``conceptual`` + ``detailed`` levels of the sections whose ``index.yaml``
keywords appear in the question, plus the sections ``atlas.always_include``
names (reasons, lineage), in index order, capped at ``atlas.max_chars``.
Each section is headed ``### <id> — <title>`` so the model can cite it as
``(methodology, <id>)``; the researcher projects that into
``ResearcherCitation.methodology_section`` and the web links it to
``/methodology#<id>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .loader import MethodologyPackage, Section
from .text import level_text


@dataclass(frozen=True)
class Selection:
    section_ids: list[str]
    text: str
    dropped_detailed: list[str]
    skipped: list[str]


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def keyword_hits(section: Section, question: str) -> int:
    q = _normalise(question)
    return sum(1 for k in section.keywords if _normalise(k) in q)


def select_section_ids(question: str, pkg: MethodologyPackage) -> list[str]:
    """Matched sections ranked by hit count (index order breaks ties), then
    the always-included ones that did not match."""
    scored = [(keyword_hits(s, question), i, s.id) for i, s in enumerate(pkg.sections)]
    matched = [sid for hits, _i, sid in sorted(scored, key=lambda t: (-t[0], t[1])) if hits > 0]
    for sid in pkg.atlas_always_include:
        if sid in pkg.by_id and sid not in matched:
            matched.append(sid)
    return matched


def section_text(section: Section, *, levels: tuple[str, ...]) -> str:
    parts = [f"### {section.id} — {section.title}"]
    for lvl in levels:
        if section.has(lvl):
            parts.append(f"[{lvl}]")
            parts.append(level_text(section.levels[lvl].blocks))
    return "\n".join(parts)


def build_selection(question: str, pkg: MethodologyPackage, *, max_chars: int | None = None) -> Selection:
    """Two passes over the budget so the always-included sections are never
    crowded out by a long matched one: (1) every selected section at its
    conceptual level — always-included first, then matched by rank; (2) the
    detailed level is added to sections in the same order while it fits.
    A section whose conceptual text alone does not fit is skipped."""
    budget = max_chars if max_chars is not None else pkg.atlas_max_chars
    matched = select_section_ids(question, pkg)
    always = [sid for sid in pkg.atlas_always_include if sid in pkg.by_id]
    order = always + [sid for sid in matched if sid not in always]
    header = (
        "=== METHODOLOGY (how Fondok calculates; cite as (methodology, <section id>)) ===\n"
        "Sections included for this question, in the platform's own order:\n"
    )
    used = len(header)
    placed: dict[str, tuple[str, ...]] = {}
    skipped: list[str] = []
    for sid in order:
        s = pkg.by_id[sid]
        lite = section_text(s, levels=("conceptual",))
        if used + len(lite) + 2 <= budget:
            placed[sid] = ("conceptual",)
            used += len(lite) + 2
        else:
            skipped.append(sid)
    dropped: list[str] = []
    for sid in order:
        if sid not in placed:
            continue
        s = pkg.by_id[sid]
        if not s.has("detailed"):
            continue
        extra = len(section_text(s, levels=("conceptual", "detailed"))) - len(section_text(s, levels=("conceptual",)))
        if used + extra <= budget:
            placed[sid] = ("conceptual", "detailed")
            used += extra
        else:
            dropped.append(sid)
    ordered = [s.id for s in pkg.sections if s.id in placed]
    text = header + "\n\n".join(section_text(pkg.by_id[sid], levels=placed[sid]) for sid in ordered)
    return Selection(section_ids=ordered, text=text, dropped_detailed=dropped, skipped=skipped)


def build_methodology_block(question: str, pkg: MethodologyPackage | None = None) -> str:
    if pkg is None:
        from . import get_package

        pkg = get_package()
    if pkg is None:
        return ""
    return build_selection(question, pkg).text


__all__ = [
    "Selection",
    "build_methodology_block",
    "build_selection",
    "keyword_hits",
    "section_text",
    "select_section_ids",
]
