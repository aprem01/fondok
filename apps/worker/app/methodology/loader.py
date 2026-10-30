"""Load and validate ``packages/methodology`` (FON-120).

The package is copied into the worker image the way ``evals`` is
(``apps/worker/Dockerfile``: ``COPY packages/methodology
/workspace/packages/methodology``) and read once at boot; ``/health``
reports ``methodology_sections`` and appends ``methodology_missing`` to
``degraded_reasons`` when the count is zero — the USALI-catalog precedent
(``main.py``), because a worker that boots without the package would serve
an Atlas with no methodology and a ``/methodology`` route with nothing in it.

Validation is structural and strict (any problem is a
:class:`MethodologyError` naming the file): a typo in a slot name, a badge
source the registry does not know, a testid the frontmatter does not list,
a ``detailed`` figure without a ``<!-- source: … -->`` citation — each fails
``scripts/gen_methodology.py`` and the worker tests before it reaches a page.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .parser import (
    LEVELS,
    MethodologyParseError,
    ParsedLevel,
    ParsedSection,
    iter_blocks,
    iter_inline,
    parse_section,
)
from .slots import KNOWN_ICONS, KNOWN_SLOTS, known_reasons, known_sources

_ENV_ROOT = "FONDOK_METHODOLOGY_DIR"


def package_root() -> Path:
    """``packages/methodology`` — next to ``apps/`` in the repo and in the
    image (``/workspace/packages/methodology``). ``FONDOK_METHODOLOGY_DIR``
    overrides it (tests, ad-hoc tooling)."""
    env = os.environ.get(_ENV_ROOT)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "packages" / "methodology"


class MethodologyError(ValueError):
    """The package failed validation; ``problems`` lists every finding."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("methodology package invalid:\n  - " + "\n  - ".join(problems))


@dataclass
class Section:
    id: str
    file: str
    title: str
    decisions: list[str]
    anchors: list[str]
    keywords: list[str]
    levels: dict[str, ParsedLevel]
    #: explicit ``<level>: none`` frontmatter markers
    none_markers: frozenset[str]
    path: Path

    def has(self, level: str) -> bool:
        return level in self.levels

    def level_title(self, level: str) -> str:
        lvl = self.levels.get(level)
        return (lvl.title if lvl is not None and lvl.title else None) or self.title


@dataclass
class MethodologyPackage:
    root: Path
    version: int
    pages: dict[str, dict[str, str]]
    sections: list[Section]
    atlas_always_include: list[str]
    atlas_max_chars: int
    by_id: dict[str, Section] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_id = {s.id: s for s in self.sections}

    def sections_with(self, level: str) -> list[Section]:
        return [s for s in self.sections if s.has(level)]

    def number_of(self, section_id: str, level: str) -> int | None:
        """1-based "Section N" over the sections that have ``level``, in
        index order — the numbering each page prints (the client page has no
        concept-registry section, so its lineage is 8; the engineering page
        has it, so its lineage is 9)."""
        n = 0
        for s in self.sections:
            if s.has(level):
                n += 1
                if s.id == section_id:
                    return n
        return None


# ──────────────────────────────── loading ──────────────────────────────────

_FILE_RE = re.compile(r"^(\d{2})-([a-z][a-z0-9-]*)\.md$")


def _read_index(root: Path) -> dict[str, Any]:
    p = root / "index.yaml"
    if not p.exists():
        raise MethodologyError([f"{p}: index.yaml not found"])
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise MethodologyError([f"{p}: top level must be a mapping"])
    return data


def load_package(root: str | Path | None = None, *, strict: bool = True, repo_root: str | Path | None = None) -> MethodologyPackage:
    """Parse the package. ``strict`` runs :func:`validate` and raises on any
    problem; ``repo_root`` additionally checks that every path a ``detailed``
    citation names exists (the codegen passes it; the worker image, which
    carries only part of the tree, does not)."""
    root = Path(root) if root is not None else package_root()
    index = _read_index(root)
    problems: list[str] = []
    sections: list[Section] = []
    entries = index.get("sections")
    if not isinstance(entries, list) or not entries:
        raise MethodologyError([f"{root / 'index.yaml'}: 'sections' must be a non-empty list"])
    for pos, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("file"), str):
            problems.append(f"index.yaml: section #{pos} needs 'id' and 'file'")
            continue
        path = root / "sections" / entry["file"]
        m = _FILE_RE.match(entry["file"])
        if not m or int(m.group(1)) != pos or m.group(2) != entry["id"]:
            problems.append(f"index.yaml: section #{pos} '{entry['id']}' must be filed as {pos:02d}-{entry['id']}.md, got {entry['file']!r}")
        if not path.exists():
            problems.append(f"{path}: file not found")
            continue
        try:
            parsed = parse_section(path.read_text(encoding="utf-8"), path=str(path))
        except MethodologyParseError as exc:
            problems.append(str(exc))
            continue
        fm = parsed.frontmatter
        if fm.get("id") != entry["id"]:
            problems.append(f"{path}: frontmatter id {fm.get('id')!r} != index id {entry['id']!r}")
        if not isinstance(fm.get("title"), str) or not fm["title"].strip():
            problems.append(f"{path}: frontmatter needs a non-empty 'title'")
        none_markers = frozenset(k for k in LEVELS if fm.get(k) == "none")
        keywords = entry.get("keywords") or []
        if not isinstance(keywords, list) or not all(isinstance(k, str) and k.strip() for k in keywords):
            problems.append(f"index.yaml: '{entry['id']}' keywords must be a list of non-empty strings")
            keywords = []
        sections.append(
            Section(
                id=entry["id"],
                file=entry["file"],
                title=str(fm.get("title") or entry["id"]),
                decisions=[str(d) for d in (fm.get("decisions") or [])],
                anchors=[str(a) for a in (fm.get("anchors") or [])],
                keywords=[str(k) for k in keywords],
                levels=parsed.levels,
                none_markers=none_markers,
                path=path,
            )
        )
    atlas = index.get("atlas") or {}
    pkg = MethodologyPackage(
        root=root,
        version=int(index.get("version", 1)),
        pages={k: dict(v) for k, v in (index.get("pages") or {}).items()},
        sections=sections,
        atlas_always_include=[str(x) for x in (atlas.get("always_include") or [])],
        atlas_max_chars=int(atlas.get("max_chars") or 28000),
    )
    problems.extend(validate(pkg, repo_root=repo_root))
    if strict and problems:
        raise MethodologyError(problems)
    return pkg


# ─────────────────────────────── validation ────────────────────────────────

_TESTID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SOURCE_COMMENT_RE = re.compile(r"^source:\s*(.+)$", re.DOTALL)
_PATH_RE = re.compile(r"(?:apps|packages|evals|scripts|docs)/[\w./\-\[\]]+")
_DIGIT_RE = re.compile(r"\d")
_FENCE_LINE_RE = re.compile(r"^:::")


def validate(pkg: MethodologyPackage, *, repo_root: str | Path | None = None) -> list[str]:
    problems: list[str] = []
    ids = [s.id for s in pkg.sections]
    if len(set(ids)) != len(ids):
        problems.append("index.yaml: duplicate section ids")
    for key in ("conceptual", "detailed", "engineering"):
        page = pkg.pages.get(key)
        if not page or not isinstance(page.get("title"), str):
            problems.append(f"index.yaml: pages.{key}.title is required")
    for sid in pkg.atlas_always_include:
        if sid not in pkg.by_id:
            problems.append(f"index.yaml: atlas.always_include names unknown section {sid!r}")
    try:
        sources = known_sources()
        reasons = known_reasons()
    except Exception as exc:  # pragma: no cover — registry failures surface elsewhere
        problems.append(f"concept registry unavailable: {exc}")
        sources, reasons = frozenset(), frozenset()

    for s in pkg.sections:
        where = s.path.name
        for lvl in ("conceptual", "detailed"):
            if not s.has(lvl) and lvl not in s.none_markers:
                problems.append(f"{where}: no '::: {lvl}' level and no explicit '{lvl}: none' marker in the frontmatter")
            if s.has(lvl) and lvl in s.none_markers:
                problems.append(f"{where}: '{lvl}: none' marker but a '::: {lvl}' level is present")
        if not s.levels:
            problems.append(f"{where}: no levels at all")
        for a in s.anchors:
            if not _TESTID_RE.match(a):
                problems.append(f"{where}: anchor {a!r} is not a kebab-case testid")
        found_testids: set[str] = set()
        for lvl_name, lvl in s.levels.items():
            for b in iter_blocks(lvl.blocks):
                attrs = b.get("attrs") or {}
                if "testid" in attrs:
                    found_testids.add(attrs["testid"])
                if b["type"] == "list":
                    for item in b["items"]:
                        if "testid" in (item.get("attrs") or {}):
                            found_testids.add(item["attrs"]["testid"])
                if b["type"] == "slot" and b["name"] not in KNOWN_SLOTS:
                    problems.append(f"{where} [{lvl_name}]: unknown block slot {b['name']!r}")
                if b["type"] == "container":
                    icon = attrs.get("icon")
                    if icon and icon not in KNOWN_ICONS:
                        problems.append(f"{where} [{lvl_name}]: unknown icon {icon!r}")
                    if b["kind"] == "badge":
                        if attrs.get("source") not in sources:
                            problems.append(f"{where} [{lvl_name}]: badge source {attrs.get('source')!r} is not in the registry")
                        if not attrs.get("name"):
                            problems.append(f"{where} [{lvl_name}]: badge container needs name=")
                    if b["kind"] == "stage" and not attrs.get("n"):
                        problems.append(f"{where} [{lvl_name}]: stage container needs n=")
            for n in iter_inline(lvl.blocks):
                t = n["type"]
                if t == "slot" and n["name"] not in KNOWN_SLOTS:
                    problems.append(f"{where} [{lvl_name}]: unknown inline slot {n['name']!r}")
                elif t == "badge" and n["source"] not in sources:
                    problems.append(f"{where} [{lvl_name}]: badge source {n['source']!r} is not in the registry")
                elif t == "reason" and n["code"] not in reasons:
                    problems.append(f"{where} [{lvl_name}]: reason code {n['code']!r} is not in the registry")
                elif t == "icon" and n["name"] not in KNOWN_ICONS:
                    problems.append(f"{where} [{lvl_name}]: unknown icon {n['name']!r}")
        for a in s.anchors:
            if a not in found_testids:
                problems.append(f"{where}: anchor {a!r} is listed in the frontmatter but no block carries testid={a}")
        for t in sorted(found_testids):
            if t not in s.anchors:
                problems.append(f"{where}: testid={t} is in the body but not listed under frontmatter anchors")
        if s.has("detailed"):
            problems.extend(_check_detailed_citations(s, repo_root=repo_root))
    return problems


def _check_detailed_citations(s: Section, *, repo_root: str | Path | None) -> list[str]:
    """Every run of non-blank lines in the ``detailed`` level that carries a
    digit must have a ``<!-- source: <path>[, <path>] -->`` comment inside it
    or in the blank gap right before it, and every path must exist when
    ``repo_root`` is given. Fence and attribute lines do not count as figures."""
    lvl = s.levels["detailed"]
    out: list[str] = []
    comment_lines = {ln: c for ln, c in lvl.comments}
    for ln, c in lvl.comments:
        if not _SOURCE_COMMENT_RE.match(c):
            out.append(f"{s.path.name}:{ln}: detailed comments must read 'source: <path>', got {c[:60]!r}")
            continue
        if repo_root is not None:
            paths = _PATH_RE.findall(c)
            if not paths:
                out.append(f"{s.path.name}:{ln}: source comment names no repository path")
            for p in paths:
                clean = re.sub(r"[\[\]].*$", "", p).rstrip(".,")
                if not (Path(repo_root) / clean).exists():
                    out.append(f"{s.path.name}:{ln}: cited source {clean!r} does not exist")
    lines = lvl.raw_lines
    numbers = sorted(lines)
    i = 0
    while i < len(numbers):
        ln = numbers[i]
        if not lines[ln].strip():
            i += 1
            continue
        run = [ln]
        j = i + 1
        while j < len(numbers) and lines[numbers[j]].strip():
            run.append(numbers[j])
            j += 1
        has_digit = any(
            _DIGIT_RE.search(_strip_markup(lines[k])) for k in run if not _FENCE_LINE_RE.match(lines[k].strip())
        )
        if has_digit:
            cited = any(k in comment_lines for k in run)
            # walk the blank gap above the run (comment residue is blank)
            k = run[0] - 1
            while not cited and k in lines and not lines[k].strip():
                if k in comment_lines:
                    cited = True
                k -= 1
            if not cited and (run[0] - 1) in comment_lines:
                cited = True
            if not cited:
                out.append(f"{s.path.name}:{run[0]}: detailed block states a figure with no '<!-- source: … -->' citation")
        i = j
    return out


def _strip_markup(line: str) -> str:
    """Drop attribute blocks, slot tokens and list markers so their digits
    ('Year 3' in a testid, 'BarChart3') do not count as figures."""
    if re.match(r"^\s*#{4,5}\s+", line):
        return ""  # a card title names the topic ("Year-1 …"), it states no figure
    s = re.sub(r"\{[^}]*\}", "", line)
    s = re.sub(r"^\s*(?:-|\d+\.)\s+", "", s)
    return s


__all__ = [
    "MethodologyError",
    "MethodologyPackage",
    "Section",
    "load_package",
    "package_root",
    "validate",
]
