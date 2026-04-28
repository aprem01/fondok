"""Hotel-investment-banker voice eval for ``InvestmentMemo``.

Adapts LogiCov's banker-voice rule set to hospitality underwriting:

* No marketing adjectives ("strong", "robust", "compelling") unless
  within 30 chars of a digit.
* No filler phrases ("it is worth noting", "going forward", etc.).
* Number formatting — flag long-form ``$36,400,000`` over compact
  ``$36.4M``; flag percent precision past 2 decimals.
* Hotel-specific: occupancy stated as a fraction in narrative
  (``0.762``) is reader-hostile — prefer ``76.2%``. ADR without a $
  prefix is sloppy. RevPAR should accompany ADR + Occupancy whenever
  the section discusses revenue performance.

Designed to catch regressions when the Analyst prompt changes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - hint only
    from fondok_schemas import InvestmentMemo, MemoSection

logger = logging.getLogger(__name__)


Severity = Literal["error", "warn", "info"]


class MemoFinding(BaseModel):
    """One rule's verdict against a section of the memo.

    ``location`` is shaped ``"<section_id>:char_start"`` so the Analyst
    UI can highlight the offending phrase in-place.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=80)
    severity: Severity
    snippet: str = Field(max_length=400)
    location: str = Field(max_length=200)
    explanation: str = Field(max_length=600)
    suggestion: str | None = Field(default=None, max_length=400)


@dataclass
class MemoEvalResult:
    """Aggregate eval output."""

    findings: list[MemoFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[MemoFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[MemoFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def passed(self) -> bool:
        """No blocking errors. Warnings are advisory."""
        return not self.errors

    @property
    def needs_regeneration(self) -> bool:
        """Caller hint: any error means the memo should be redrafted."""
        return bool(self.errors)


# ─────────────────────── rule constants ───────────────────────


# Marketing adjectives banned unless within 30 chars of a digit.
# Hotel-banker addition: "premier", "best-in-class", "compelling",
# "exceptional" (the broker deck classics).
_MARKETING_ADJECTIVES: tuple[str, ...] = (
    "strong",
    "robust",
    "healthy",
    "compelling",
    "exceptional",
    "premier",
    "best-in-class",
    "best in class",
    "significant",
    "substantial",
    "remarkable",
)

# Filler phrases that add zero signal. Banned outright.
_FILLER_PHRASES: tuple[str, ...] = (
    "it is worth noting",
    "it is important to note",
    "it should be noted",
    "as previously mentioned",
    "going forward",
    "at the end of the day",
    "needless to say",
    "in this regard",
)

# 7+-digit raw currency (e.g. $36,400,000) — should be M/B form.
_RAW_BIG_NUMBER = re.compile(r"\$\s?\d{1,3}(?:,\d{3}){2,}")
# Percent with more than 2 decimals (banker memos round to 2 max).
_OVERSPEC_PERCENT = re.compile(r"\b\d+\.\d{3,}\s*%")
# Standalone "ADR ... 185.40" or "ADR was 185.40" without a $
_ADR_WITHOUT_DOLLAR = re.compile(
    r"\bADR\b[^$\n]{0,60}?\b(\d{2,3}(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)
# Occupancy stated as a decimal fraction in narrative ("occupancy of 0.762").
# Requires the explicit leading "0." so we don't false-flag "76.2%" — the
# percent sign prevents that match here, but a bare ".762" without %
# is unusual enough to ignore.
_OCC_AS_FRACTION = re.compile(
    r"\boccupancy\b[^.%\n]{0,40}?\b(0\.\d+)(?!\d*\s*%)",
    re.IGNORECASE,
)
# When ADR + Occupancy appear in the same section, RevPAR should too.
_ADR_MENTION = re.compile(r"\bADR\b", re.IGNORECASE)
_OCCUPANCY_MENTION = re.compile(r"\boccupancy\b", re.IGNORECASE)
_REVPAR_MENTION = re.compile(r"\bRevPAR\b", re.IGNORECASE)


# ─────────────────────── rule helpers ───────────────────────


def _has_digit_near(text: str, start: int, end: int, *, window: int = 30) -> bool:
    chunk = text[max(0, start - window) : min(len(text), end + window)]
    return bool(re.search(r"\d", chunk))


def _section_id_str(section: "MemoSection") -> str:
    sid = getattr(section, "section_id", None)
    if hasattr(sid, "value"):
        return str(sid.value)
    return str(sid) if sid is not None else "unknown"


def _section_body(section: "MemoSection") -> str:
    """Read the section body. Schema field is ``body`` in Fondok."""
    return getattr(section, "body", "") or ""


def _location(section_id: str, char_offset: int) -> str:
    return f"{section_id}:{char_offset}"


def _snippet_around(body: str, start: int, end: int, *, pad: int = 30) -> str:
    s = max(0, start - pad)
    e = min(len(body), end + pad)
    return body[s:e].strip()


def _suggested_compact(value_str: str) -> str:
    """Convert ``$36,400,000`` → ``$36.4M`` for the suggestion blurb."""
    digits = re.sub(r"[^0-9.]", "", value_str)
    try:
        v = float(digits)
    except ValueError:
        return ""
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B".replace(".0B", "B")
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 1_000:
        return f"${v / 1_000:.1f}k".replace(".0k", "k")
    return f"${v:,.0f}"


# ─────────────────────── per-section rules ───────────────────────


def _rule_no_marketing_adjectives_without_numbers(
    section: "MemoSection",
) -> list[MemoFinding]:
    sid = _section_id_str(section)
    body = _section_body(section)
    findings: list[MemoFinding] = []
    for phrase in _MARKETING_ADJECTIVES:
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", body, flags=re.IGNORECASE):
            if _has_digit_near(body, m.start(), m.end()):
                continue
            findings.append(
                MemoFinding(
                    rule_id="marketing_adjective_without_number",
                    severity="warn",
                    snippet=_snippet_around(body, m.start(), m.end()),
                    location=_location(sid, m.start()),
                    explanation=(
                        f"{m.group(0)!r} used without a nearby digit. "
                        "Either back it with a metric or cut the adjective."
                    ),
                    suggestion="Replace with a quantified claim or remove.",
                )
            )
    return findings


def _rule_no_filler_phrases(section: "MemoSection") -> list[MemoFinding]:
    sid = _section_id_str(section)
    body = _section_body(section)
    findings: list[MemoFinding] = []
    body_lower = body.lower()
    for phrase in _FILLER_PHRASES:
        idx = body_lower.find(phrase)
        while idx >= 0:
            findings.append(
                MemoFinding(
                    rule_id="filler_phrase",
                    severity="error",
                    snippet=_snippet_around(body, idx, idx + len(phrase)),
                    location=_location(sid, idx),
                    explanation=(
                        f"Filler phrase {phrase!r} adds no signal — cut it."
                    ),
                    suggestion="Remove the phrase entirely.",
                )
            )
            idx = body_lower.find(phrase, idx + len(phrase))
    return findings


def _rule_number_formatting(section: "MemoSection") -> list[MemoFinding]:
    sid = _section_id_str(section)
    body = _section_body(section)
    findings: list[MemoFinding] = []
    for m in _RAW_BIG_NUMBER.finditer(body):
        suggested = _suggested_compact(m.group(0))
        findings.append(
            MemoFinding(
                rule_id="number_formatting_long_currency",
                severity="error",
                snippet=_snippet_around(body, m.start(), m.end()),
                location=_location(sid, m.start()),
                explanation=(
                    f"{m.group(0)} is too verbose. Banker memos use "
                    "$X.XM / $X.XB compact form."
                ),
                suggestion=f"Use {suggested}." if suggested else None,
            )
        )
    for m in _OVERSPEC_PERCENT.finditer(body):
        # Suggest 2-decimal rounding.
        try:
            v = float(m.group(0).rstrip("%").strip())
            suggested = f"{v:.2f}%"
        except ValueError:
            suggested = None
        findings.append(
            MemoFinding(
                rule_id="number_formatting_overspec_percent",
                severity="warn",
                snippet=_snippet_around(body, m.start(), m.end()),
                location=_location(sid, m.start()),
                explanation=(
                    f"{m.group(0)} — banker memos round percents to "
                    "at most 2 decimals."
                ),
                suggestion=f"Use {suggested}." if suggested else None,
            )
        )
    return findings


def _rule_hotel_occupancy_form(section: "MemoSection") -> list[MemoFinding]:
    """Occupancy expressed as a fraction in narrative is reader-hostile."""
    sid = _section_id_str(section)
    body = _section_body(section)
    findings: list[MemoFinding] = []
    for m in _OCC_AS_FRACTION.finditer(body):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if not (0 < v < 1):
            continue
        suggested = f"{v * 100:.1f}%"
        findings.append(
            MemoFinding(
                rule_id="hotel_occupancy_as_fraction",
                severity="warn",
                snippet=_snippet_around(body, m.start(), m.end()),
                location=_location(sid, m.start()),
                explanation=(
                    f"Occupancy stated as decimal {m.group(1)} — IC readers "
                    "expect percent form."
                ),
                suggestion=f"Use {suggested}.",
            )
        )
    return findings


def _rule_adr_has_dollar_prefix(section: "MemoSection") -> list[MemoFinding]:
    """ADR is a dollar value; a bare number is sloppy."""
    sid = _section_id_str(section)
    body = _section_body(section)
    findings: list[MemoFinding] = []
    for m in _ADR_WITHOUT_DOLLAR.finditer(body):
        # Only flag if the number itself isn't $-prefixed inside the match.
        if "$" in m.group(0):
            continue
        # Skip "ADR was 8 percent higher" — the trailing digit is unitless.
        amount = m.group(1)
        try:
            v = float(amount)
        except ValueError:
            continue
        # Skip implausibly small numbers — those are growth pcts not ADR.
        if v < 50:
            continue
        findings.append(
            MemoFinding(
                rule_id="hotel_adr_missing_dollar",
                severity="warn",
                snippet=_snippet_around(body, m.start(), m.end()),
                location=_location(sid, m.start()),
                explanation=(
                    "ADR is a currency figure; missing $ prefix reads as a "
                    "raw count."
                ),
                suggestion=f"Use ${amount}.",
            )
        )
    return findings


def _rule_revpar_accompanies_adr_occ(
    section: "MemoSection",
) -> list[MemoFinding]:
    """If a section invokes ADR + Occupancy, RevPAR should be present too."""
    sid = _section_id_str(section)
    body = _section_body(section)
    if not (_ADR_MENTION.search(body) and _OCCUPANCY_MENTION.search(body)):
        return []
    if _REVPAR_MENTION.search(body):
        return []
    # Locate the first ADR mention as the location anchor.
    m = _ADR_MENTION.search(body)
    assert m is not None  # we just confirmed
    return [
        MemoFinding(
            rule_id="hotel_revpar_paired",
            severity="warn",
            snippet=_snippet_around(body, m.start(), m.end()),
            location=_location(sid, m.start()),
            explanation=(
                "ADR + Occupancy without RevPAR is incomplete for an IC "
                "audience — RevPAR is the headline number."
            ),
            suggestion="Add RevPAR (= ADR × Occupancy).",
        )
    ]


# ─────────────────────── orchestration ───────────────────────


_SECTION_RULES: tuple = (
    _rule_no_marketing_adjectives_without_numbers,
    _rule_no_filler_phrases,
    _rule_number_formatting,
    _rule_hotel_occupancy_form,
    _rule_adr_has_dollar_prefix,
    _rule_revpar_accompanies_adr_occ,
)


def _iter_sections(memo: "InvestmentMemo") -> Iterable["MemoSection"]:
    sections = getattr(memo, "sections", None) or []
    return sections


def evaluate_memo(memo: "InvestmentMemo") -> MemoEvalResult:
    """Run the full hotel-banker rule suite against a memo.

    Returns a ``MemoEvalResult`` whose ``findings`` is the flat list of
    rule violations. ``passed`` is True iff there are no errors.
    """
    findings: list[MemoFinding] = []
    for section in _iter_sections(memo):
        for rule in _SECTION_RULES:
            try:
                findings.extend(rule(section))
            except Exception as exc:  # noqa: BLE001 - never crash the pipeline
                logger.warning(
                    "memo_quality: rule %s raised on section %s: %s",
                    rule.__name__,
                    _section_id_str(section),
                    exc,
                )
    return MemoEvalResult(findings=findings)


__all__ = [
    "MemoEvalResult",
    "MemoFinding",
    "Severity",
    "evaluate_memo",
]
