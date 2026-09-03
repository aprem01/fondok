"""Deterministic analyst-override layer for the generated IC memo.

The IC Memo tab (``apps/web/.../ICMemoTab.tsx``) is a decision *workspace*:
the analyst records the committee decision — the verdict, an editable
thesis, curated highlights, curated risks — and those edits persist into
``deals.field_overrides`` under five keys:

    * ``memo_recommendation_override`` — IC verdict, one of
      ``"Proceed" | "Proceed with Conditions" | "Do Not Proceed"``.
    * ``memo_thesis``                  — the investment-thesis prose.
    * ``memo_thesis_edited``           — bookkeeping flag (not consumed here).
    * ``memo_highlights``              — list of ``{"t": str, "ai": bool}``.
    * ``memo_risks``                   — list of ``{"t": str, "ai": bool}``.

The Analyst agent (:mod:`app.agents.analyst`) drafts the memo as six
prose sections and never reads these keys back, so a regenerate/reload of
``GET /deals/{id}/memo`` previously discarded the analyst's edits. This
module is the smallest deterministic post-process that layers those
overrides *on top of* whatever the generator produced — it never touches
the LLM/generation logic itself.

Design contract
---------------
* **Opt-in / no-op safe.** When no meaningful ``memo_*`` override is
  present, :func:`apply_memo_overrides` returns the *exact* input list
  object unchanged — the memo is byte-identical to today. Sections that a
  present override does not target are passed through by reference, so
  only genuinely overridden sections are rewritten.
* **Deterministic.** No I/O, no clock, no LLM. Same inputs → same output.
* **Shape-preserving.** Sections stay ``dict`` with the same keys the
  ``MemoEnvelope`` / ``MemoStream`` consumer expects (``section_id``,
  ``title``, ``body``, ``citations``); only ``body`` is rewritten.
  Citations are left intact.

Section mapping (live 6-section memo — see ``REQUIRED_SECTION_ORDER`` in
``MemoStream.tsx``: ``investment_thesis``, ``market_analysis``,
``deal_overview``, ``financial_analysis``, ``risk_factors``,
``recommendation``):

    memo_thesis                  → ``investment_thesis``  body (replace)
    memo_highlights              → ``investment_thesis``  body (append
                                     a "Key highlights" bullet block, after
                                     any thesis replacement). The live memo
                                     has no dedicated highlights section and
                                     the viewer drops unknown section_ids,
                                     so highlights ride inside the thesis —
                                     "the case for it".
    memo_recommendation_override → ``recommendation``     body (authoritative
                                     verdict headline, generated rationale
                                     preserved beneath it)
    memo_risks                   → ``risk_factors``       body (replace with
                                     the analyst's curated bullet list)

An override whose target section is absent from the produced memo is a
no-op — there is nothing to layer it onto.
"""

from __future__ import annotations

from typing import Any

# Frontend verdict vocabulary — must match ICMemoTab.tsx exactly.
VALID_VERDICTS: frozenset[str] = frozenset(
    {"Proceed", "Proceed with Conditions", "Do Not Proceed"}
)

_THESIS_SECTION = "investment_thesis"
_RECOMMENDATION_SECTION = "recommendation"
_RISK_SECTION = "risk_factors"

# The override keys this layer consumes. ``memo_thesis_edited`` is
# intentionally excluded — it is UI bookkeeping, not memo content.
_OVERRIDE_KEYS: tuple[str, ...] = (
    "memo_thesis",
    "memo_highlights",
    "memo_recommendation_override",
    "memo_risks",
)


def _clean_str(value: Any) -> str | None:
    """A non-empty, stripped string, or ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _points(value: Any) -> list[str]:
    """Extract the ordered, non-empty point strings from a memo list.

    Accepts the frontend shape ``[{"t": "...", "ai": bool}, ...]`` and,
    defensively, a plain ``["...", ...]`` list. Anything else → ``[]``.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _clean_str(item.get("t"))
        else:
            text = _clean_str(item)
        if text is not None:
            out.append(text)
    return out


def _verdict(value: Any) -> str | None:
    """The analyst's verdict if it is one of the three valid values."""
    return value if isinstance(value, str) and value in VALID_VERDICTS else None


def _bullets(points: list[str]) -> str:
    return "\n".join(f"• {p}" for p in points)


def has_memo_overrides(overrides: dict[str, Any] | None) -> bool:
    """True iff ``overrides`` carries at least one *meaningful* memo_* key.

    Empty strings, empty lists, and out-of-vocabulary verdicts do not
    count — they collapse to the no-op path so generation is preserved.
    """
    if not isinstance(overrides, dict):
        return False
    if _clean_str(overrides.get("memo_thesis")) is not None:
        return True
    if _points(overrides.get("memo_highlights")):
        return True
    if _points(overrides.get("memo_risks")):
        return True
    if _verdict(overrides.get("memo_recommendation_override")) is not None:
        return True
    return False


def _thesis_body(original_body: str, overrides: dict[str, Any]) -> str:
    body = original_body
    thesis = _clean_str(overrides.get("memo_thesis"))
    if thesis is not None:
        body = thesis
    highlights = _points(overrides.get("memo_highlights"))
    if highlights:
        block = "Key highlights:\n" + _bullets(highlights)
        body = f"{body}\n\n{block}" if body else block
    return body


def _recommendation_body(original_body: str, overrides: dict[str, Any]) -> str:
    verdict = _verdict(overrides.get("memo_recommendation_override"))
    if verdict is None:
        return original_body
    headline = f"IC recommendation: {verdict}."
    return f"{headline}\n\n{original_body}" if original_body else headline


def _risk_body(original_body: str, overrides: dict[str, Any]) -> str:
    risks = _points(overrides.get("memo_risks"))
    if risks:
        return _bullets(risks)
    return original_body


# section_id → (does this override touch it?, body transform)
_SECTION_BUILDERS = {
    _THESIS_SECTION: _thesis_body,
    _RECOMMENDATION_SECTION: _recommendation_body,
    _RISK_SECTION: _risk_body,
}


def apply_memo_overrides(
    sections: list[dict[str, Any]],
    overrides: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Layer the analyst's ``memo_*`` overrides onto produced sections.

    Returns a list of section dicts with authoritative analyst content
    substituted into the mapped sections' ``body``. Opt-in: when no
    meaningful override is present the *same* ``sections`` object is
    returned unchanged (byte-identical to today). Untouched sections are
    passed through by reference; only overridden sections are copied and
    rewritten. Pure and deterministic — no I/O, no LLM.
    """
    if not has_memo_overrides(overrides) or not sections:
        return sections
    assert overrides is not None  # narrowed by has_memo_overrides

    out: list[dict[str, Any]] = []
    for section in sections:
        builder = _SECTION_BUILDERS.get(section.get("section_id"))
        if builder is None:
            out.append(section)
            continue
        original_body = section.get("body") or ""
        new_body = builder(original_body, overrides)
        if new_body == original_body:
            out.append(section)
            continue
        updated = dict(section)
        updated["body"] = new_body
        out.append(updated)
    return out


__all__ = ["apply_memo_overrides", "has_memo_overrides", "VALID_VERDICTS"]
