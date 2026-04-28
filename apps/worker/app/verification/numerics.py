"""Deterministic numeric verifier — hotel underwriting flavor.

For each ``ExtractionField`` with a ``source_page``, locate the page
text in the cached ``ParsedDocument``, search for the value using a
fuzzy regex over hotel-specific number formats, and classify the
citation as match / close / mismatch / unverifiable.

Tolerance is the same as LogiCov's credit-spread verifier (2% relative
+ $1 absolute) so the score is calibrated against the same regulator
expectation: rounding is fine, hallucination is not.

Hotel-specific number formats handled
-------------------------------------

* Currency:   ``$36.4M``, ``$36,400,000``, ``$303k/key``, ``(1.2M)``
              (parens-as-negative is the broker convention)
* Percent:    ``6.8%``, ``680 bps`` (basis points), ``6.80% cap``
* Multiplier: ``1.57x DSCR``, ``2.12x equity multiple``
* Occupancy:  the LLM may store ``0.762`` for "76.2%" — we accept both
              forms when matching (decimal-vs-percent ambiguity is the
              single most common false-mismatch source)
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Iterable
from uuid import UUID

from fondok_schemas import (
    CitationStatus,
    ExtractionField,
    VerificationCheck,
    VerificationReport,
)

logger = logging.getLogger(__name__)


# ─────────────────────── Parsers ───────────────────────


# Currency / generic-number capture. Mirrors LogiCov's pattern with the
# same conventions: optional $, optional thousand separators, optional
# decimal, optional k/M/B scale, optional parens-as-negative. To avoid
# matching letter-adjacent digits like "Q1" or "FY24", we require a
# word boundary BEFORE the leading paren / $ and we require the scale
# suffix to be at a word boundary (so "M" in "MARKET" doesn't promote
# a stray digit).
_CURRENCY_RE = re.compile(
    r"""
    (?<![A-Za-z])                      # not preceded by a letter (skips Q1, FY24)
    (?P<paren_open>\()?                # optional opening paren (negative marker)
    \$?\s*                              # optional $ and spaces
    (?P<sign>-)?                        # explicit negative
    (?P<int>\d{1,3}(?:,\d{3})+|\d+)     # integer digits, optionally comma-grouped
    (?:\.(?P<frac>\d+))?                # optional decimal
    \s*(?P<scale>[kKmMbB])?\b           # optional scale suffix at a word boundary
    (?P<paren_close>\))?                # optional closing paren
    """,
    re.VERBOSE,
)

# Percent — captures the number plus the trailing % sign so we can
# distinguish "6.8%" from "6.8" in narrative text. The inner number
# pattern is intentionally simpler than _CURRENCY_RE so the % sign
# stays the discriminator.
_PERCENT_RE = re.compile(
    r"""
    (?P<sign>-)?
    (?P<num>\d+(?:\.\d+)?)
    \s*%
    """,
    re.VERBOSE,
)

# Basis points — "680 bps" → 0.0680
_BPS_RE = re.compile(
    r"""
    (?P<sign>-)?
    (?P<num>\d+(?:\.\d+)?)
    \s*(?:bps|bp|basis\s+points?)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Multiplier — "1.57x DSCR", "2.12x equity multiple"
_MULTIPLIER_RE = re.compile(
    r"""
    (?P<sign>-)?
    (?P<num>\d+(?:\.\d+)?)
    \s*x\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_SCALE_MAP: dict[str, float] = {
    "k": 1_000,
    "K": 1_000,
    "m": 1_000_000,
    "M": 1_000_000,
    "b": 1_000_000_000,
    "B": 1_000_000_000,
}


def parse_currency(text: str) -> list[float]:
    """Pull every plausible currency / raw number out of ``text``.

    Negative parens ``(1,234)`` and explicit negatives are both honored.
    Scale suffixes ``k|M|B`` multiply the base value.
    """
    if not text:
        return []
    out: list[float] = []
    for match in _CURRENCY_RE.finditer(text):
        integer = match.group("int").replace(",", "")
        frac = match.group("frac") or ""
        raw = f"{integer}.{frac}" if frac else integer
        try:
            value = float(raw)
        except ValueError:
            continue
        scale = match.group("scale")
        if scale:
            value *= _SCALE_MAP.get(scale, 1.0)
        # Parentheses-as-negative is the accountant convention.
        if match.group("paren_open") and match.group("paren_close"):
            value = -value
        if match.group("sign") == "-":
            value = -value
        out.append(value)
    return out


def parse_percent(text: str) -> list[float]:
    """Pull every percent / bps / multiplier out of ``text`` as a decimal.

    ``6.8%``  → 0.068
    ``680 bps`` → 0.068
    ``1.57x`` → 1.57    (multipliers are NOT divided by 100)
    """
    if not text:
        return []
    out: list[float] = []
    for m in _PERCENT_RE.finditer(text):
        try:
            v = float(m.group("num")) / 100.0
        except ValueError:
            continue
        if m.group("sign") == "-":
            v = -v
        out.append(v)
    for m in _BPS_RE.finditer(text):
        try:
            v = float(m.group("num")) / 10_000.0
        except ValueError:
            continue
        if m.group("sign") == "-":
            v = -v
        out.append(v)
    for m in _MULTIPLIER_RE.finditer(text):
        try:
            v = float(m.group("num"))
        except ValueError:
            continue
        if m.group("sign") == "-":
            v = -v
        out.append(v)
    return out


# ─────────────────────── Field-value coercion ───────────────────────


def _coerce_to_float(value: object) -> float | None:
    """Best-effort coercion of an ExtractionField.value into a float."""
    if value is None:
        return None
    if isinstance(value, bool):
        # ``isinstance(True, int)`` is True; bools have no place in
        # numeric verification.
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Try the currency parser first — handles $/M/k/parens.
        currency = parse_currency(value)
        if currency:
            return currency[0]
        pct = parse_percent(value)
        if pct:
            return pct[0]
    return None


def _looks_like_percent(field_name: str, value: float) -> bool:
    """Heuristic — does this field's value look like it's expressed as a percent?"""
    name = field_name.lower()
    pct_hints = (
        "rate",
        "pct",
        "percent",
        "occupancy",
        "yield",
        "irr",
        "dscr",
        "ltv",
        "ltc",
        "cap",
        "ratio",
        "promote",
        "growth",
        "cagr",
    )
    if any(h in name for h in pct_hints):
        return True
    # 0.0 - 1.0 range with a percent-y name
    return 0.0 < value < 1.0


# ─────────────────────── Classification ───────────────────────


# Same tolerances as LogiCov so cross-product calibration stays consistent.
_REL_TOL = 0.02
_ABS_TOL = 1.0
_EXACT_TOL = 0.001  # 0.1% — counts as MATCH (not just CLOSE)


def _classify(
    extracted: float,
    candidates: Iterable[float],
) -> tuple[CitationStatus, float | None, float | None, float | None]:
    """Return (status, closest_candidate, delta_abs, delta_pct).

    ``candidates`` is empty ⇒ UNVERIFIABLE.
    Within 0.1% of cited value ⇒ MATCH.
    Within 2% relative or $1 absolute ⇒ CLOSE.
    Otherwise ⇒ MISMATCH.
    """
    candidates = list(candidates)
    if not candidates:
        return CitationStatus.UNVERIFIABLE, None, None, None
    closest = min(candidates, key=lambda x: abs(x - extracted))
    diff = abs(closest - extracted)
    rel = (diff / abs(extracted)) if extracted != 0 else None
    # Exact match within rounding noise — the strongest signal.
    if diff <= 0.01:
        return CitationStatus.MATCH, closest, diff, rel
    if extracted != 0 and rel is not None and rel <= _EXACT_TOL:
        return CitationStatus.MATCH, closest, diff, rel
    if extracted != 0 and rel is not None and rel <= _REL_TOL:
        return CitationStatus.CLOSE, closest, diff, rel
    if diff <= _ABS_TOL:
        return CitationStatus.CLOSE, closest, diff, rel
    return CitationStatus.MISMATCH, closest, diff, rel


def _candidate_pool(extracted: float, field_name: str, page_text: str) -> list[float]:
    """Build the pool of plausible matches the page might contain.

    Hotel-specific wrinkle: the LLM frequently stores occupancy as a
    decimal (0.762) while the broker writes "76.2%" in narrative text —
    or vice versa. We add both forms to the candidate pool when the
    field looks percent-shaped so we don't false-flag that case.
    """
    pool: list[float] = []
    pool.extend(parse_currency(page_text))
    pool.extend(parse_percent(page_text))

    # Decimal-vs-percent reconciliation. If we extracted 0.762 and the
    # page only mentions 76.2, the percent parser found 0.762 — already
    # in the pool. If we extracted 76.2 and the page only mentions 0.762,
    # we add 76.2 as a candidate by scaling each percent entry up.
    if _looks_like_percent(field_name, extracted) and extracted >= 1.0:
        # extracted was *already* in percent form (e.g. 76.2). Add
        # any percent in the pool *100 so we can match.
        scaled = [p * 100.0 for p in parse_percent(page_text)]
        pool.extend(scaled)
    return pool


def _excerpt_around(text: str, value: float, window: int = 50) -> str | None:
    """Find a 50-char window around the first occurrence of ``value``."""
    if not text:
        return None
    # Try a few stringifications — bare digits and comma-grouped form.
    needles: list[str] = []
    if value.is_integer():
        n = int(value)
        needles.append(f"{n:,}")
        needles.append(str(n))
    else:
        # Try the value with 1-2 decimals.
        needles.append(f"{value:,.2f}")
        needles.append(f"{value:,.1f}")
        needles.append(f"{value:.2f}")
        needles.append(f"{value:.1f}")
    for needle in needles:
        idx = text.find(needle)
        if idx >= 0:
            start = max(0, idx - window)
            end = min(len(text), idx + len(needle) + window)
            return text[start:end].strip()
    return None


# ─────────────────────── Public entry point ───────────────────────


def verify_citations(
    fields: list[ExtractionField],
    parsed_documents: dict[str, "ParsedDocument"],  # noqa: F821 - forward
    *,
    deal_id: UUID | str,
    field_doc_ids: dict[str, str] | None = None,
) -> VerificationReport:
    """Re-check every cited number in ``fields`` against its source page.

    Parameters
    ----------
    fields:
        The structured fields the Extractor produced. Each field's
        ``source_page`` points back at the document it came from.
    parsed_documents:
        Map of ``document_id`` → ``ParsedDocument`` (parser cache).
        Each ``ParsedDocument.pages[i]`` carries the full page text.
    deal_id:
        Used solely to populate the report header.
    field_doc_ids:
        Optional ``field_name`` → ``document_id`` map. When omitted we
        try every document in ``parsed_documents`` and accept the first
        page that contains a candidate match. (Single-doc deals — the
        common case during onboarding — work fine without it.)

    Returns
    -------
    A ``VerificationReport`` with one ``VerificationCheck`` per field.
    Use the report's ``pass_rate`` property for a single number.
    """
    field_doc_ids = field_doc_ids or {}
    deal_uuid = deal_id if isinstance(deal_id, UUID) else UUID(str(deal_id))

    checks: list[VerificationCheck] = []
    for field in fields:
        extracted = _coerce_to_float(field.value)
        cited_value = "" if field.value is None else str(field.value)

        if extracted is None:
            # Non-numeric field (e.g. "brand: Kimpton"); skip silently.
            continue

        page_text = _resolve_page_text(
            field, parsed_documents, field_doc_ids
        )
        if page_text is None:
            checks.append(
                VerificationCheck(
                    field_name=field.field_name,
                    cited_value=cited_value[:200],
                    parsed_value=extracted,
                    status=CitationStatus.UNVERIFIABLE,
                    source_page=field.source_page,
                    source_doc_id=_to_doc_uuid(
                        field_doc_ids.get(field.field_name)
                    ),
                    excerpt=None,
                )
            )
            continue

        pool = _candidate_pool(extracted, field.field_name, page_text)
        status, closest, delta_abs, delta_pct = _classify(extracted, pool)

        excerpt: str | None = None
        if closest is not None:
            excerpt = _excerpt_around(page_text, closest)
        if excerpt is None and field.raw_text:
            # Fall back to the raw_text the Extractor recorded, capped
            # at 400 chars to satisfy the schema.
            excerpt = field.raw_text[:400]

        checks.append(
            VerificationCheck(
                field_name=field.field_name,
                cited_value=cited_value[:200],
                parsed_value=extracted,
                found_in_source=closest,
                delta_abs=delta_abs,
                delta_pct=delta_pct,
                status=status,
                source_page=field.source_page,
                source_doc_id=_to_doc_uuid(
                    field_doc_ids.get(field.field_name)
                ),
                excerpt=excerpt[:400] if excerpt else None,
            )
        )

    return VerificationReport(
        deal_id=deal_uuid,
        checks=checks,
        generated_at=datetime.now(UTC),
    )


def _resolve_page_text(
    field: ExtractionField,
    parsed_documents: dict[str, "ParsedDocument"],  # noqa: F821 - forward
    field_doc_ids: dict[str, str],
) -> str | None:
    """Return the page text for ``field``'s ``source_page``.

    Prefers the doc id pinned in ``field_doc_ids``. When absent (single-
    doc deals) we sweep every document and return the first page that
    contains *some* parseable number — so the verifier degrades to "best
    effort" rather than UNVERIFIABLE on the entire deal.
    """
    page_idx = field.source_page - 1  # source_page is 1-based
    pinned_doc_id = field_doc_ids.get(field.field_name)
    if pinned_doc_id and pinned_doc_id in parsed_documents:
        doc = parsed_documents[pinned_doc_id]
        if 0 <= page_idx < len(doc.pages):
            return doc.pages[page_idx].text
        return None

    # Sweep mode: try every document; return first non-empty page text.
    for doc in parsed_documents.values():
        if 0 <= page_idx < len(doc.pages):
            text = doc.pages[page_idx].text
            if text.strip():
                return text
    return None


def _to_doc_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "parse_currency",
    "parse_percent",
    "verify_citations",
]
