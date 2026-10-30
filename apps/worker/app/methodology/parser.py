"""The methodology markdown dialect → a small JSON-serialisable AST (FON-120).

``packages/methodology/sections/<nn>-<id>.md`` is the ONE source for the
Methodology page, its admin detail, the engineering reference and the text
Atlas reads. This module is the only parser of that dialect: the web never
parses markdown — ``scripts/gen_methodology.py`` runs this parser and emits
the AST as TypeScript, and the web renders the AST (``render.tsx``). The
worker renders the same AST to plain text (``text.py``).

The dialect is deliberately restricted — a reviewer must be able to read a
section file side by side with the page, and nothing in it may smuggle
markup past the client-copy discipline:

File
    ``---`` YAML frontmatter ``---`` then the body. ``<!-- … -->`` comments
    are stripped (they carry source citations for ``detailed`` figures and
    the page.tsx provenance of the split) and never rendered.

Levels
    Top-level fences ``::: conceptual`` / ``::: detailed`` /
    ``::: engineering`` … ``:::``. A level fence may carry
    ``{title="…"}`` when that level's section title differs from the
    frontmatter title (the engineering page names its sections differently).

Containers
    ``::: <kind> {key=value key2="quoted"}`` … ``:::`` nested with a stack;
    the kinds are the page's own layout vocabulary (``intro``, ``card``,
    ``stages``, ``stage``, ``chain``, ``badges``, ``badge``, ``columns``,
    ``column``, ``deflist``, ``box``). Attributes carry the data-testids the
    tests pin (``testid=…``) and the component inputs (``source=``,
    ``name=``, ``n=``, ``icon=``, ``flush=``, ``tone=``).

Blocks
    ``#### `` / ``##### `` headings; ``- `` bullet and ``1. `` ordered
    lists (continuation lines indented); GFM pipe tables; a line holding
    only ``{{slot:<name>}}`` (a block slot); otherwise a paragraph. A block
    may start with ``{attrs}`` (``{testid=dash-rule} **A figure…``).

Inline
    ``**strong**``, ``*em*``, ```` `code` ```` (literal — no escapes inside),
    ``[text](href)`` with an internal href, ``{{slot:<name>}}``,
    ``{{badge:<source>}}``, ``{{reason:<code>}}``, ``{{icon:<Name>}}``, a
    hard break as ``\\`` at the end of a line, and backslash escapes for
    ``\\ * ` < > { } [ ] | _ #``. A raw ``<`` outside a code span is an
    error: there is no HTML in this dialect.

Everything the parser rejects is a :class:`MethodologyParseError` naming
the file and line, so an authoring mistake fails ``gen_methodology.py`` and
the worker's own tests rather than reaching a page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

LEVELS: tuple[str, ...] = ("conceptual", "detailed", "engineering")

CONTAINER_KINDS: frozenset[str] = frozenset(
    {
        "intro",
        "card",
        "stages",
        "stage",
        "chain",
        "badges",
        "badge",
        "columns",
        "column",
        "deflist",
        "box",
    }
)

# Attribute keys a container / block may carry. Anything else is a typo.
ATTR_KEYS: frozenset[str] = frozenset(
    {"testid", "source", "name", "n", "icon", "flush", "tone", "title", "none"}
)

INLINE_TOKEN_KINDS: tuple[str, ...] = ("slot", "badge", "reason", "icon")

Inline = dict[str, Any]
Block = dict[str, Any]


class MethodologyParseError(ValueError):
    def __init__(self, message: str, *, path: str | None = None, line: int | None = None):
        where = ""
        if path:
            where = f"{path}"
            if line is not None:
                where += f":{line}"
            where += ": "
        super().__init__(where + message)
        self.path = path
        self.line = line


@dataclass
class ParsedLevel:
    name: str
    title: str | None
    blocks: list[Block]
    #: ``<!-- … -->`` comments found inside this level, in order, with the
    #: 1-based line each one started on — the loader uses them to check that
    #: every ``detailed`` figure cites its source.
    comments: list[tuple[int, str]] = field(default_factory=list)
    #: raw body lines of the level (comments removed) keyed by line number,
    #: for the detailed-figure citation check.
    raw_lines: dict[int, str] = field(default_factory=dict)


@dataclass
class ParsedSection:
    path: str
    frontmatter: dict[str, Any]
    levels: dict[str, ParsedLevel]


# ─────────────────────────────── frontmatter ───────────────────────────────


def split_frontmatter(text: str, *, path: str | None = None) -> tuple[dict[str, Any], list[str], int]:
    """Return ``(frontmatter, body_lines, body_first_line_number)``."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise MethodologyParseError("file must start with a '---' YAML frontmatter block", path=path, line=1)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "\n".join(lines[1:i])
            try:
                fm = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                raise MethodologyParseError(f"frontmatter is not valid YAML: {exc}", path=path, line=2) from exc
            if not isinstance(fm, dict):
                raise MethodologyParseError("frontmatter must be a mapping", path=path, line=2)
            return fm, lines[i + 1 :], i + 2
    raise MethodologyParseError("frontmatter block is not closed with '---'", path=path, line=1)


# ──────────────────────────────── comments ─────────────────────────────────

_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def strip_comments(lines: list[str], first_line_no: int, *, path: str | None = None) -> tuple[list[str], list[tuple[int, str]]]:
    """Remove ``<!-- … -->`` (possibly multi-line) and return the comments
    with the line they started on. Line count is preserved so error lines
    stay accurate: a multi-line comment leaves blank lines behind."""
    joined = "\n".join(lines)
    comments: list[tuple[int, str]] = []

    def _repl(m: re.Match[str]) -> str:
        start_line = first_line_no + joined.count("\n", 0, m.start())
        comments.append((start_line, m.group(1).strip()))
        return "\n" * m.group(0).count("\n")

    cleaned = _COMMENT_RE.sub(_repl, joined)
    if "<!--" in cleaned:
        line = first_line_no + cleaned.count("\n", 0, cleaned.index("<!--"))
        raise MethodologyParseError("unterminated '<!--' comment", path=path, line=line)
    return cleaned.split("\n"), comments


# ─────────────────────────────── attributes ────────────────────────────────

_ATTR_RE = re.compile(r'\s*([A-Za-z_][\w-]*)=(?:"((?:[^"\\]|\\.)*)"|([^\s}]+))')


def parse_attrs(raw: str, *, path: str | None = None, line: int | None = None) -> dict[str, str]:
    """``{key=value key2="quoted \\" value"}`` → dict. Keys are validated."""
    s = raw.strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise MethodologyParseError(f"malformed attribute block {raw!r}", path=path, line=line)
    body = s[1:-1]
    out: dict[str, str] = {}
    pos = 0
    while pos < len(body):
        if body[pos].isspace():
            pos += 1
            continue
        m = _ATTR_RE.match(body, pos)
        if not m:
            raise MethodologyParseError(f"malformed attribute near {body[pos:pos+20]!r}", path=path, line=line)
        key = m.group(1)
        if key not in ATTR_KEYS:
            raise MethodologyParseError(f"unknown attribute {key!r} (known: {sorted(ATTR_KEYS)})", path=path, line=line)
        value = m.group(2) if m.group(2) is not None else m.group(3)
        if m.group(2) is not None:
            value = re.sub(r"\\(.)", r"\1", value)
        if key in out:
            raise MethodologyParseError(f"duplicate attribute {key!r}", path=path, line=line)
        out[key] = value
        pos = m.end()
    return out


# ───────────────────────────────── inline ──────────────────────────────────

_ESCAPABLE = set("\\*`<>{}[]|_#")
_TOKEN_RE = re.compile(r"\{\{(slot|badge|reason|icon):([A-Za-z0-9_][\w.-]*)\}\}")
_LINK_RE = re.compile(r"\[((?:[^\]\\]|\\.)*)\]\(([^)\s]+)\)")


def _find_close(text: str, start: int, marker: str) -> int:
    """Index of the closing ``marker`` at or after ``start``, skipping
    escapes and code spans; -1 when absent."""
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            j = text.find("`", i + 1)
            if j == -1:
                return -1
            i = j + 1
            continue
        if text.startswith(marker, i):
            # ``*`` must not match the first char of a ``**``
            if marker == "*" and text.startswith("**", i):
                i += 2
                continue
            return i
        i += 1
    return -1


def parse_inline(text: str, *, path: str | None = None, line: int | None = None) -> list[Inline]:
    out: list[Inline] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append({"type": "text", "value": "".join(buf)})
            buf.clear()

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                flush()
                out.append({"type": "br"})
                i += 2
                continue
            if i + 1 < n and text[i + 1] in _ESCAPABLE:
                buf.append(text[i + 1])
                i += 2
                continue
            raise MethodologyParseError(
                f"unknown escape {text[i:i+2]!r} (escapable: {''.join(sorted(_ESCAPABLE))})", path=path, line=line
            )
        if ch == "{" and text.startswith("{{", i):
            m = _TOKEN_RE.match(text, i)
            if not m:
                raise MethodologyParseError(f"malformed token near {text[i:i+30]!r}", path=path, line=line)
            flush()
            kind, value = m.group(1), m.group(2)
            key = {"slot": "name", "badge": "source", "reason": "code", "icon": "name"}[kind]
            out.append({"type": kind, key: value})
            i = m.end()
            continue
        if ch == "`":
            j = text.find("`", i + 1)
            if j == -1:
                raise MethodologyParseError("unterminated code span", path=path, line=line)
            flush()
            out.append({"type": "code", "value": text[i + 1 : j]})
            i = j + 1
            continue
        if ch == "*":
            marker = "**" if text.startswith("**", i) else "*"
            j = _find_close(text, i + len(marker), marker)
            if j == -1:
                raise MethodologyParseError(f"unmatched {marker!r} near {text[i:i+30]!r}", path=path, line=line)
            inner = text[i + len(marker) : j]
            if not inner.strip():
                raise MethodologyParseError(f"empty {marker!r} span", path=path, line=line)
            flush()
            out.append(
                {
                    "type": "strong" if marker == "**" else "em",
                    "children": parse_inline(inner, path=path, line=line),
                }
            )
            i = j + len(marker)
            continue
        if ch == "[":
            m = _LINK_RE.match(text, i)
            if not m:
                raise MethodologyParseError(f"malformed link near {text[i:i+30]!r} (escape a literal '[' as '\\[')", path=path, line=line)
            href = m.group(2)
            if not (href.startswith("/") or href.startswith("#") or href.startswith("https://")):
                raise MethodologyParseError(f"link href must be internal (/… or #…) or https://, got {href!r}", path=path, line=line)
            flush()
            out.append({"type": "link", "href": href, "children": parse_inline(m.group(1), path=path, line=line)})
            i = m.end()
            continue
        if ch == "<":
            raise MethodologyParseError("raw HTML is not allowed; escape a literal '<' as '\\<'", path=path, line=line)
        if ch == ">" and (i == 0 or text[i - 1] == "\n"):
            raise MethodologyParseError("block quotes are not part of the dialect; escape '>' as '\\>'", path=path, line=line)
        buf.append(ch)
        i += 1
    flush()
    return out


# ───────────────────────────────── blocks ──────────────────────────────────

_FENCE_RE = re.compile(r"^:::(?:\s+([A-Za-z][\w-]*))?(?:\s+(\{.*\}))?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LIST_RE = re.compile(r"^(?:-|\d+\.)\s+(.*)$")
_ORDERED_RE = re.compile(r"^\d+\.\s")
_LEADING_ATTRS_RE = re.compile(r"^(\{[^}]*\})\s*(.*)$", re.DOTALL)
_BLOCK_SLOT_RE = re.compile(r"^\{\{slot:([A-Za-z0-9_][\w.-]*)\}\}$")


def _split_leading_attrs(text: str, *, path: str | None, line: int) -> tuple[dict[str, str], str]:
    if text.startswith("{") and not text.startswith("{{"):
        m = _LEADING_ATTRS_RE.match(text)
        if not m:
            raise MethodologyParseError("malformed leading attribute block", path=path, line=line)
        return parse_attrs(m.group(1), path=path, line=line), m.group(2)
    return {}, text


def _join_soft(parts: list[str]) -> str:
    """Join the lines of one block: a soft line break is a space (the text
    node a reader copies has no stray newlines — ``toContain`` in the tests
    reads it raw), a line ending in ``\\`` keeps its newline so the inline
    parser sees the hard break."""
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(part)
        if i < len(parts) - 1:
            out.append("\n" if part.endswith("\\") else " ")
    return "".join(out)


class _BlockParser:
    def __init__(self, lines: list[str], first_line_no: int, *, path: str | None):
        self.lines = lines
        self.base = first_line_no
        self.path = path

    def lineno(self, idx: int) -> int:
        return self.base + idx

    def parse_blocks(self, start: int, stop: int) -> list[Block]:
        """Parse ``lines[start:stop]`` into blocks (containers included)."""
        blocks: list[Block] = []
        pending_attrs: dict[str, str] | None = None
        i = start
        while i < stop:
            raw = self.lines[i]
            stripped = raw.strip()
            if not stripped:
                i += 1
                continue
            fm = _FENCE_RE.match(stripped)
            if fm:
                name = fm.group(1)
                if name is None:
                    raise MethodologyParseError("unexpected closing ':::' (no open container)", path=self.path, line=self.lineno(i))
                if name in LEVELS:
                    raise MethodologyParseError(f"level fence '{name}' must be top-level", path=self.path, line=self.lineno(i))
                if name not in CONTAINER_KINDS:
                    raise MethodologyParseError(f"unknown container kind {name!r} (known: {sorted(CONTAINER_KINDS)})", path=self.path, line=self.lineno(i))
                attrs = parse_attrs(fm.group(2), path=self.path, line=self.lineno(i)) if fm.group(2) else {}
                if pending_attrs:
                    attrs = {**pending_attrs, **attrs}
                    pending_attrs = None
                close = self._find_fence_close(i + 1, stop)
                children = self.parse_blocks(i + 1, close)
                blocks.append({"type": "container", "kind": name, "attrs": attrs, "children": children})
                i = close + 1
                continue
            # a lone attribute line applies to the next block
            if stripped.startswith("{") and not stripped.startswith("{{") and stripped.endswith("}") and i + 1 < stop:
                try:
                    pending_attrs = parse_attrs(stripped, path=self.path, line=self.lineno(i))
                    i += 1
                    continue
                except MethodologyParseError:
                    pass
            hm = _HEADING_RE.match(stripped)
            if hm:
                level = len(hm.group(1))
                if level not in (4, 5):
                    raise MethodologyParseError("headings inside a section are '####' (card title) or '#####' (box title); the section title comes from the frontmatter", path=self.path, line=self.lineno(i))
                blocks.append({"type": "heading", "level": level, "inline": parse_inline(hm.group(2), path=self.path, line=self.lineno(i))})
                pending_attrs = None
                i += 1
                continue
            sm = _BLOCK_SLOT_RE.match(stripped)
            if sm:
                blocks.append({"type": "slot", "name": sm.group(1)})
                pending_attrs = None
                i += 1
                continue
            if _LIST_RE.match(stripped) and not raw.startswith(" "):
                block, i = self._parse_list(i, stop)
                if pending_attrs:
                    block["attrs"] = {**pending_attrs, **block.get("attrs", {})}
                    pending_attrs = None
                blocks.append(block)
                continue
            if stripped.startswith("|"):
                block, i = self._parse_table(i, stop)
                if pending_attrs:
                    block["attrs"] = {**pending_attrs, **block["attrs"]}
                    pending_attrs = None
                blocks.append(block)
                continue
            block, i = self._parse_paragraph(i, stop)
            if pending_attrs:
                block["attrs"] = {**pending_attrs, **block["attrs"]}
                pending_attrs = None
            blocks.append(block)
        if pending_attrs:
            raise MethodologyParseError("attribute block with no following block", path=self.path, line=self.lineno(stop - 1))
        return blocks

    def _find_fence_close(self, start: int, stop: int) -> int:
        depth = 0
        for j in range(start, stop):
            m = _FENCE_RE.match(self.lines[j].strip())
            if not m:
                continue
            if m.group(1) is None:
                if depth == 0:
                    return j
                depth -= 1
            else:
                depth += 1
        raise MethodologyParseError("container is not closed with ':::'", path=self.path, line=self.lineno(start - 1))

    def _is_block_start(self, idx: int) -> bool:
        raw = self.lines[idx]
        s = raw.strip()
        if not s:
            return True
        if _FENCE_RE.match(s) or _HEADING_RE.match(s) or s.startswith("|") or _BLOCK_SLOT_RE.match(s):
            return True
        if _LIST_RE.match(s) and not raw.startswith(" "):
            return True
        return False

    def _parse_paragraph(self, start: int, stop: int) -> tuple[Block, int]:
        i = start
        parts: list[str] = []
        while i < stop and not (i > start and self._is_block_start(i)):
            parts.append(self.lines[i].rstrip())
            i += 1
        text = _join_soft(parts)
        attrs, text = _split_leading_attrs(text, path=self.path, line=self.lineno(start))
        return {"type": "paragraph", "attrs": attrs, "inline": parse_inline(text, path=self.path, line=self.lineno(start))}, i

    def _parse_list(self, start: int, stop: int) -> tuple[Block, int]:
        ordered = bool(_ORDERED_RE.match(self.lines[start].strip()))
        items: list[dict[str, Any]] = []
        i = start
        while i < stop:
            raw = self.lines[i]
            s = raw.strip()
            m = _LIST_RE.match(s)
            if not m or raw.startswith(" "):
                break
            if bool(_ORDERED_RE.match(s)) != ordered:
                raise MethodologyParseError("list mixes '-' and '1.' items", path=self.path, line=self.lineno(i))
            item_line = self.lineno(i)
            parts = [m.group(1)]
            i += 1
            while i < stop and self.lines[i].startswith(" ") and self.lines[i].strip():
                parts.append(self.lines[i].strip())
                i += 1
            text = _join_soft(parts)
            attrs, text = _split_leading_attrs(text, path=self.path, line=item_line)
            items.append({"attrs": attrs, "inline": parse_inline(text, path=self.path, line=item_line)})
        return {"type": "list", "ordered": ordered, "attrs": {}, "items": items}, i

    def _parse_table(self, start: int, stop: int) -> tuple[Block, int]:
        rows: list[list[str]] = []
        i = start
        while i < stop and self.lines[i].strip().startswith("|"):
            rows.append(self._split_row(self.lines[i].strip(), self.lineno(i)))
            i += 1
        if len(rows) < 2 or not all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in rows[1]):
            raise MethodologyParseError("a table needs a header row and a '| --- |' separator row", path=self.path, line=self.lineno(start))
        width = len(rows[0])
        for k, r in enumerate(rows):
            if len(r) != width:
                raise MethodologyParseError(f"table row has {len(r)} cells, header has {width}", path=self.path, line=self.lineno(start + k))
        head = [parse_inline(c.strip(), path=self.path, line=self.lineno(start)) for c in rows[0]]
        body = [
            [parse_inline(c.strip(), path=self.path, line=self.lineno(start + 2 + k)) for c in r]
            for k, r in enumerate(rows[2:])
        ]
        return {"type": "table", "attrs": {}, "head": head, "rows": body}, i

    @staticmethod
    def _split_row(line: str, lineno: int) -> list[str]:
        cells: list[str] = []
        buf: list[str] = []
        i = 1  # skip the leading pipe
        n = len(line)
        in_code = False
        while i < n:
            ch = line[i]
            if ch == "\\" and i + 1 < n:
                buf.append(line[i : i + 2])
                i += 2
                continue
            if ch == "`":
                in_code = not in_code
            if ch == "|" and not in_code:
                cells.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        if buf and "".join(buf).strip():
            cells.append("".join(buf))
        return cells


# ──────────────────────────────── sections ─────────────────────────────────


def parse_section(text: str, *, path: str | None = None) -> ParsedSection:
    fm, body, first = split_frontmatter(text, path=path)
    body, comments = strip_comments(body, first, path=path)
    levels: dict[str, ParsedLevel] = {}
    i = 0
    while i < len(body):
        s = body[i].strip()
        if not s:
            i += 1
            continue
        m = _FENCE_RE.match(s)
        if not m or m.group(1) is None:
            raise MethodologyParseError(
                "only level fences ('::: conceptual' / '::: detailed' / '::: engineering') may appear at the top level",
                path=path,
                line=first + i,
            )
        name = m.group(1)
        if name not in LEVELS:
            raise MethodologyParseError(f"top-level fence must be a level, got {name!r}", path=path, line=first + i)
        if name in levels:
            raise MethodologyParseError(f"level {name!r} appears twice", path=path, line=first + i)
        attrs = parse_attrs(m.group(2), path=path, line=first + i) if m.group(2) else {}
        bp = _BlockParser(body, first, path=path)
        close = bp._find_fence_close(i + 1, len(body))
        blocks = bp.parse_blocks(i + 1, close)
        lvl = ParsedLevel(name=name, title=attrs.get("title"), blocks=blocks)
        lo, hi = first + i, first + close
        lvl.comments = [(ln, c) for ln, c in comments if lo <= ln <= hi]
        lvl.raw_lines = {first + k: body[k] for k in range(i + 1, close)}
        levels[name] = lvl
        i = close + 1
    return ParsedSection(path=path or "<memory>", frontmatter=fm, levels=levels)


def iter_inline(blocks: list[Block]):
    """Yield every inline node in ``blocks`` (depth-first)."""
    for b in blocks:
        t = b["type"]
        if t == "container":
            yield from iter_inline(b["children"])
        elif t in ("heading", "paragraph"):
            yield from _walk_inline(b["inline"])
        elif t == "list":
            for it in b["items"]:
                yield from _walk_inline(it["inline"])
        elif t == "table":
            for cell in b["head"]:
                yield from _walk_inline(cell)
            for row in b["rows"]:
                for cell in row:
                    yield from _walk_inline(cell)


def _walk_inline(nodes: list[Inline]):
    for n in nodes:
        yield n
        if "children" in n:
            yield from _walk_inline(n["children"])


def iter_blocks(blocks: list[Block]):
    """Yield every block (containers and their children, depth-first)."""
    for b in blocks:
        yield b
        if b["type"] == "container":
            yield from iter_blocks(b["children"])


__all__ = [
    "ATTR_KEYS",
    "CONTAINER_KINDS",
    "LEVELS",
    "Block",
    "Inline",
    "MethodologyParseError",
    "ParsedLevel",
    "ParsedSection",
    "iter_blocks",
    "iter_inline",
    "parse_attrs",
    "parse_inline",
    "parse_section",
    "split_frontmatter",
]
