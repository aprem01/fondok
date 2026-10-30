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
from typing import Any

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


#: Export-side prefix for a value the builder declined to invent. Where a
#: workbook / memo / deck cell would otherwise hold a number, the cell reads
#: ``not available: <reason>`` (go-live rule: an unknown is never 0 / 0% / $0).
NOT_AVAILABLE_PREFIX = "not available"


def unavailable(
    code: ReasonCode,
    detail: str,
    concept: str | None = None,
) -> str:
    """Record a refusal and return the export sentence ``not available: <detail>``.

    The glyph form (:func:`refuse`) suits dense tables whose header names the
    field; this form suits headline cells, label/value rows and section notes,
    where the reader needs the reason on the page itself. Both record the same
    typed :class:`Refusal`, so the Refusals sheet / memo footnote list it.
    """
    refuse(code, detail, concept)
    return f"{NOT_AVAILABLE_PREFIX}: {detail}"


def as_float(value: Any) -> float | None:
    """Coerce a payload scalar to float; ``None`` / blank / non-numeric stays
    ``None`` — a missing number is never read as 0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sum_strict(*values: float | None) -> float | None:
    """Sum only when EVERY component is present; ``None`` if any is missing.

    A total built over a missing component would silently read the gap as
    zero — a sum is stated only when every part is (go-live rule 1).
    """
    if any(v is None for v in values):
        return None
    return float(sum(values))  # type: ignore[arg-type]


# ── refusal-aware formatters — the ONE implementation the memo PDF and the
# deck share (each binds its own precision). A ``None`` means the payload
# omitted the key rather than carrying a zero, so the default code is
# ``no_source``; ``concept`` names what the dash stands for.


def fmt_usd(
    v: float | int | None,
    *,
    scale: int = 1,
    code: ReasonCode = ReasonCode.NO_SOURCE,
    concept: str | None = None,
) -> str:
    if v is None:
        return refuse(code, "no value on the export payload", concept)
    try:
        return f"${float(v) / scale:,.0f}"
    except (TypeError, ValueError):
        return refuse(code, f"value {v!r} is not a number", concept)


def fmt_usd_m(
    v: float | int | None,
    *,
    decimals: int = 2,
    code: ReasonCode = ReasonCode.NO_SOURCE,
    concept: str | None = None,
) -> str:
    if v is None:
        return refuse(code, "no value on the export payload", concept)
    try:
        return f"${float(v) / 1_000_000:.{decimals}f}M"
    except (TypeError, ValueError):
        return refuse(code, f"value {v!r} is not a number", concept)


def fmt_pct(
    v: float | None,
    *,
    decimals: int = 2,
    code: ReasonCode = ReasonCode.NO_SOURCE,
    concept: str | None = None,
) -> str:
    if v is None:
        return refuse(code, "no value on the export payload", concept)
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return refuse(code, f"value {v!r} is not a number", concept)


def fmt_mult(
    v: float | None,
    *,
    code: ReasonCode = ReasonCode.NO_SOURCE,
    concept: str | None = None,
) -> str:
    if v is None:
        return refuse(code, "no value on the export payload", concept)
    try:
        return f"{float(v):.2f}x"
    except (TypeError, ValueError):
        return refuse(code, f"value {v!r} is not a number", concept)


def fmt_int(
    v: float | int | None,
    *,
    code: ReasonCode = ReasonCode.NO_SOURCE,
    concept: str | None = None,
) -> str:
    if v is None:
        return refuse(code, "no value on the export payload", concept)
    try:
        return f"{round(float(v)):,}"
    except (TypeError, ValueError):
        return refuse(code, f"value {v!r} is not a number", concept)


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
    "NOT_AVAILABLE_PREFIX",
    "REFUSAL_GLYPH",
    "ReasonCode",
    "Refusal",
    "as_float",
    "collect_refusals",
    "dedupe",
    "distinct_codes",
    "fmt_int",
    "fmt_mult",
    "fmt_pct",
    "fmt_usd",
    "fmt_usd_m",
    "reason_explanation",
    "reason_label",
    "refuse",
    "sum_strict",
    "unavailable",
]
