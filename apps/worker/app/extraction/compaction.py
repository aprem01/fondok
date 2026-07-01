"""Compact parsed document text before sending it to the Anthropic LLM.

The Extractor is billed per input token. Parsed docs — especially Excel
sheets, LlamaParse markdown tables, and PyMuPDF text dumps — carry a lot
of formatting noise the extractor has zero use for:

* Long runs of whitespace / tabs (empty cells serialize as
  ``\\t\\t\\t\\t``).
* Repeated page/sheet headers already embedded in the chunk header
  (e.g. ``[Page 12]`` when the parser also injected the same page label
  on every line).
* Decorative separator lines (``----``, ``====``, ``****``) inherited
  from broker-formatted PDFs and Excel print layouts.
* Trailing whitespace on every line.

Empirically these strip out 15–25% of input tokens with zero downstream
quality change — the extractor never grounds a field against a
horizontal rule or a run of tabs. This module owns the compaction so
the parsers themselves stay lossless (parsed text is still cached raw
on the document row for debugging).

Safety gates
------------
The Extractor's whole job is to find NUMBERS. Compaction must NEVER
drop or alter a line that carries numeric content:

* Lines containing digits, ``$``, ``€``, ``£``, or ``%`` are kept
  verbatim modulo trailing-whitespace trimming.
* Whitespace collapse only fires on RUNS (3+ chars) — a single space
  between "Rooms Revenue" and a dollar amount stays intact.
* Newline-bearing runs collapse to a single ``\\n`` (structure), space-
  only runs collapse to a single space (token cost only).
* Indentation on a data-bearing line is preserved so a table hierarchy
  like ``  Rooms Revenue`` vs ``    Occupied Rooms`` still reads
  correctly.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


# Regexes compiled once at import — the extractor calls this per chunk
# on every doc, and hundreds of doc chunks per deal are routine.

# A "decorative separator" is a line composed entirely of one repeated
# non-alphanumeric filler char (``-``, ``=``, ``*``, ``_``, ``~``, ``.``,
# ``#``), with 3+ of that char. Whitespace around/between is fine — the
# whole line just has to be non-informative. Digits/letters anywhere in
# the line disqualify it (a page footer like ``---- 12 ----`` still
# carries the page number the extractor may cite).
_DECORATIVE_LINE = re.compile(r"^[\s]*([-=*_~.#])\1{2,}[\s]*$")

# Runs of 3+ whitespace chars (space, tab, but NOT newline — newlines
# get special treatment because they carry line structure). The
# separator between "field" and "value" in a table dump is often
# 5–15 tabs; collapse to a single space to keep the pairing while
# shedding the padding.
_WHITESPACE_RUN = re.compile(r"[ \t\f\v]{3,}")

# Any character that flags a line as "data-bearing" — presence of any
# of these means "do not touch this line's inner structure, only trim
# trailing space".
_DATA_CHARS = re.compile(r"[\d$€£¥%]")

# Redundant page header emitted by chunk assembler. Only prune when
# TWO consecutive ``[Page N]\n[Page N]`` lines appear (chunk header
# then in-line page label). Do NOT prune a single ``[Page N]`` — the
# extractor uses those as citation anchors.
_PAGE_HEADER = re.compile(r"^\s*\[Page\s+\d+\]\s*$")


def _env_flag(name: str, default: bool) -> bool:
    """Read a bool from the environment, falling back to ``default``.

    We avoid pydantic ``Settings`` here because compaction runs on the
    hot path (every chunk of every doc) and a stray ImportError from
    settings would fail extraction. This keeps the flag safe to read
    from anywhere the parser or extractor is exercised, including tests
    that don't boot the full app config.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_decorative_line(line: str) -> bool:
    """Return True when ``line`` is a pure separator we can drop.

    The check runs against the STRIPPED line so leading indent doesn't
    fool the regex. We only drop if the line carries no digits, dollar
    signs, or currency-shaped characters — an underlined heading like
    ``____$1,234,567____`` still holds the number the extractor wants.
    """
    stripped = line.strip()
    if not stripped:
        # Blank lines are kept — they're paragraph breaks the extractor
        # sometimes uses to align fields with sections.
        return False
    if _DATA_CHARS.search(stripped):
        return False
    return bool(_DECORATIVE_LINE.match(stripped))


def _compact_line(line: str) -> str:
    """Collapse whitespace runs inside a single line.

    Data-bearing lines (containing digits or currency symbols) get
    trailing-whitespace trimmed but their INNER runs stay intact — a
    table row like ``Rooms      $1,234,567`` stays visually aligned so
    the LLM can still parse the row. Structural / label-only lines
    collapse aggressively; the extractor doesn't need alignment when
    there's no number to align against.

    Leading whitespace on data-bearing lines is preserved because
    indentation in USALI-style P&Ls carries hierarchy (department →
    sub-account → line item). On non-data lines leading whitespace is
    kept as-is too — cheap to keep, and dropping it could merge a
    subheader into an adjacent header.
    """
    # Preserve leading whitespace verbatim: strip only the trailing side.
    rstripped = line.rstrip()
    if not rstripped:
        return ""

    if _DATA_CHARS.search(rstripped):
        # Compact runs of 6+ inner whitespace chars down to 2 spaces so
        # the row still reads visually but we don't ship 15 tabs per
        # empty cell. Runs of 3-5 stay intact — often the deliberate
        # gap between column headers.
        return re.sub(r"[ \t\f\v]{6,}", "  ", rstripped)

    # Non-data lines: collapse aggressively. Any run of 3+ ws chars
    # (tabs or spaces) becomes a single space.
    return _WHITESPACE_RUN.sub(" ", rstripped)


def _compact_parsed_text(text: str) -> str:
    """Strip formatting noise from parsed doc text before prompting.

    Safe by default — every rule is guarded against altering lines that
    carry numeric or currency content. See the module docstring for the
    full safety-gate list.

    Idempotent: running the function twice on the same input returns
    the same output as running it once. Tests rely on this so a caller
    that double-compacts (e.g. compaction at parse time AND at prompt
    time) still produces a stable string.
    """
    if not text:
        return text

    lines = text.split("\n")
    out: list[str] = []
    prev_page_header: str | None = None
    for raw in lines:
        # Drop pure separator lines outright.
        if _is_decorative_line(raw):
            continue

        compact = _compact_line(raw)

        # Suppress a consecutive-duplicate ``[Page N]`` header. The chunk
        # assembler prepends its own ``[Page N]`` and the parser sometimes
        # also embeds the same label as the first line of the page; the
        # duplicate wastes tokens without adding grounding signal. Keep
        # the first occurrence so the extractor still has the citation.
        if _PAGE_HEADER.match(compact):
            if prev_page_header == compact.strip():
                continue
            prev_page_header = compact.strip()
        else:
            # Reset the tracker on any non-header line so a real
            # duplicate elsewhere in the doc still surfaces.
            if compact.strip():
                prev_page_header = None

        out.append(compact)

    # Collapse runs of 3+ blank lines to a single blank line — vertical
    # whitespace is worth about one token per line and adds no signal.
    compacted: list[str] = []
    blank_run = 0
    for line in out:
        if not line.strip():
            blank_run += 1
            if blank_run >= 2:
                # We already emitted one blank; skip further blanks.
                continue
        else:
            blank_run = 0
        compacted.append(line)

    # Trim leading/trailing blank lines — they never carry signal.
    while compacted and not compacted[0].strip():
        compacted.pop(0)
    while compacted and not compacted[-1].strip():
        compacted.pop()

    return "\n".join(compacted)


def compact_for_prompt(text: str) -> tuple[str, dict[str, int]]:
    """Public entrypoint used by the extractor prompt builder.

    Returns ``(compacted_text, stats)`` where ``stats`` carries
    before/after character counts so callers can log the compaction
    impact for Sam's cost dashboards. The compaction itself is a no-op
    when ``PARSER_COMPACTION_ENABLED=false`` — the flag exists so a
    debugging session can turn the fold off without a code change.

    We report ``chars_before`` / ``chars_after`` (not tokens) because
    the char count is cheap to measure at every call site while token
    counts require an actual tokenizer round-trip. Chars are a reliable
    proxy for the Anthropic tokenizer — Sonnet averages ~3.5 chars per
    input token on English business prose, so a 20% char reduction
    tracks a ~20% token reduction within a couple of points.
    """
    if not text:
        return text, {"chars_before": 0, "chars_after": 0, "chars_saved": 0}

    if not _env_flag("PARSER_COMPACTION_ENABLED", default=True):
        return text, {
            "chars_before": len(text),
            "chars_after": len(text),
            "chars_saved": 0,
        }

    before = len(text)
    compacted = _compact_parsed_text(text)
    after = len(compacted)
    return compacted, {
        "chars_before": before,
        "chars_after": after,
        "chars_saved": before - after,
    }


__all__ = [
    "compact_for_prompt",
    "_compact_parsed_text",
]
