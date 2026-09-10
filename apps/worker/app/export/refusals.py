"""Typed refusals for the export builders (Phase 4.2).

Every ``"—"`` an export writes is a refusal: the builder had no value and
declined to invent one (``feedback_no_fake_data``). Until now that refusal
was an untyped glyph — an LP reading the deck, the memo PDF or the workbook
could see the dash but not *why* it was there, and nothing downstream could
count or classify them.

This module is the one seam that changes that. :func:`refuse` writes the
same glyph the builders wrote before (:data:`REFUSAL_GLYPH`, byte-identical)
*and* records a typed :class:`~fondok_schemas.reasons.Refusal` on the
collector that is active for the current build. Nothing about the rendered
cell / paragraph changes; the codes travel as a parallel, additive channel:

    * ``live_payload`` → ``model["refusals"]`` (the payload's own list),
    * ``memo_pdf``     → a footnote listing the distinct reasons it dashed,
    * ``excel``        → the "Refusals" sheet.

The collector is a :class:`~contextvars.ContextVar` rather than a parameter
threaded through every helper because the dash writes live inside leaf
formatters (``_fmt_usd``, ``_humanize_field``) that a dozen call sites reach.
Outside a :func:`collect_refusals` block :func:`refuse` is a pure function
returning the glyph — importing this module can never change what a builder
renders.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from fondok_schemas.reasons import REASON_META, REFUSAL_GLYPH, ReasonCode, Refusal

#: The refusal list the current build is recording into, or ``None`` when no
#: build is in flight (``refuse`` then only returns the glyph).
_ACTIVE: contextvars.ContextVar[list[Refusal] | None] = contextvars.ContextVar(
    "fondok_export_refusals", default=None
)


def refuse(
    code: ReasonCode,
    detail: str | None = None,
    concept: str | None = None,
) -> str:
    """Write a dash **and** record why.

    Returns :data:`REFUSAL_GLYPH` — the exact string the builders wrote as a
    literal before — so routing a dash write through this helper never
    changes a rendered value. When a :func:`collect_refusals` block is active
    the matching :class:`Refusal` is appended to it.
    """
    log = _ACTIVE.get()
    if log is not None:
        log.append(Refusal(code=code, detail=detail, concept=concept))
    return REFUSAL_GLYPH


@contextmanager
def collect_refusals(into: list[Refusal] | None = None) -> Iterator[list[Refusal]]:
    """Record every :func:`refuse` call made inside the block.

    Nestable and task-safe (``ContextVar`` token reset). Pass ``into`` to
    append onto an existing list — used by ``build_excel`` to merge the
    workbook's own dashes with the ones the payload already carried.
    """
    log: list[Refusal] = [] if into is None else into
    token = _ACTIVE.set(log)
    try:
        yield log
    finally:
        _ACTIVE.reset(token)


def _field(item: object, name: str) -> object:
    """Read ``name`` off a :class:`Refusal` or the same shape as a dict.

    A payload that has been round-tripped through JSON carries dicts; one
    held in memory carries models. Both read the same here.
    """
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _code_value(item: object) -> str | None:
    raw = _field(item, "code")
    if raw is None:
        return None
    return str(getattr(raw, "value", raw))


def dedupe(refusals: Iterable[object]) -> list[object]:
    """First occurrence of each ``(code, concept, detail)``, order preserved.

    A leaf formatter can refuse the same thing on every row it renders; the
    payload / footnote / sheet want the distinct set, in the order the build
    hit them, so the output is deterministic for the same inputs.
    """
    seen: set[tuple[str | None, object, object]] = set()
    out: list[object] = []
    for r in refusals:
        key = (_code_value(r), _field(r, "concept"), _field(r, "detail"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def distinct_codes(refusals: Iterable[object]) -> list[ReasonCode]:
    """The distinct :class:`ReasonCode` set, in first-seen order."""
    seen: set[str] = set()
    out: list[ReasonCode] = []
    for r in refusals:
        value = _code_value(r)
        if value is None or value in seen:
            continue
        try:
            code = ReasonCode(value)
        except ValueError:  # pragma: no cover — closed enum on both sides
            continue
        seen.add(value)
        out.append(code)
    return out


def reason_label(code: ReasonCode) -> str:
    """Short human label for ``code`` — from ``REASON_META``, never typed."""
    return REASON_META[code]["label"]


def reason_explanation(code: ReasonCode) -> str:
    """One-sentence meaning of ``code`` — from ``REASON_META``, never typed."""
    return REASON_META[code]["explanation"]


__all__ = [
    "REFUSAL_GLYPH",
    "ReasonCode",
    "Refusal",
    "collect_refusals",
    "dedupe",
    "distinct_codes",
    "reason_explanation",
    "reason_label",
    "refuse",
]
