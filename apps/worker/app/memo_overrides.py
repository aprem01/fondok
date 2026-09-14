"""Deterministic analyst-override layer for the generated IC memo.

The IC Memo tab (``apps/web/.../ICMemoTab.tsx``) is a decision *workspace*:
the analyst records the committee decision — the verdict, an editable
thesis, curated highlights, curated risks — and those edits persist into
``deals.field_overrides`` under these keys:

    * ``memo_recommendation_override``  — IC verdict the analyst selected, one
      of ``"Proceed" | "Proceed with Conditions" | "Do Not Proceed"``.
    * ``memo_recommendation_confirmed`` — ``True`` once the analyst confirmed
      that verdict (FON-54a). The IC recommendation is a *decision*, not an
      inference: until a verdict is both selected and confirmed every
      consumer (memo body, live export header) says
      :data:`PENDING_DECISION` — never the model's inferred verdict.
    * ``memo_thesis``                   — the investment-thesis prose.
    * ``memo_highlights``               — list of ``{"t": str, "ai": bool}``.
    * ``memo_risks``                    — list of ``{"t": str, "ai": bool}``.
    * ``memo_thesis_edited`` /
      ``memo_highlights_edited`` /
      ``memo_risks_edited``             — bookkeeping: this section is the
      ANALYST's writing, not a draft. The tab asks before a regenerate
      replaces one (FON-54); nothing here consumes them.
    * ``memo_thesis_run_id`` /
      ``memo_highlights_run_id`` /
      ``memo_risks_run_id``             — bookkeeping: the engine run each
      section was drafted against, stamped by the IC Memo tab and compared on
      read against the live run so prose that predates the current
      underwriting is FLAGGED rather than shown as current (FON-54, Sam
      2026-09-14 — an IC memo that quoted a 32.2% IRR in the thesis and a
      -2.5% IRR in the highlights). They are UI bookkeeping in exactly the
      sense ``*_edited`` is: never memo content, never layered into a body,
      and never enough on their own to make :func:`has_memo_overrides` true —
      a deal carrying only a stamp still gets the generator's memo verbatim.
      ``apps/worker/tests/test_memo_run_stamps.py`` pins that.
    * ``memo_diligence``                — FON-54a diligence status keyed by the
      variance *concept* (``rooms_revenue``, ``noi`` … — the ``concept`` on
      ``GET /analysis/{id}/variance`` flags):
      ``{concept: {"status": "Open"|"Resolved"|"Accepted", "note"?: str,
      "updated_at"?: iso-str}}``. Read back by the IC Memo tab (IC readiness)
      and the live Excel export (Variance sheet) through
      :func:`diligence_status` — one source of truth.

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
      + _confirmed                   verdict headline once CONFIRMED —
                                     "IC recommendation: Pending analyst
                                     decision." while a selected verdict is
                                     unconfirmed; generated rationale
                                     preserved beneath either headline)
    memo_risks                   → ``risk_factors``       body (replace with
                                     the analyst's curated bullet list)
    memo_diligence               → not a memo section; exposed via
                                     :func:`diligence_status` for the IC Memo
                                     tab + live export

An override whose target section is absent from the produced memo is a
no-op — there is nothing to layer it onto.
"""

from __future__ import annotations

from typing import Any

from fondok_schemas.reasons import ReasonCode

# Frontend verdict vocabulary — must match ICMemoTab.tsx exactly.
VALID_VERDICTS: frozenset[str] = frozenset(
    {"Proceed", "Proceed with Conditions", "Do Not Proceed"}
)

# Canonical wording (IC Memo banner, memo body, live export header) while the
# analyst has not selected + confirmed a verdict. Must match ICMemoTab.tsx.
PENDING_DECISION = "Pending analyst decision"

# Diligence vocabulary — must match the IC Memo tab's Resolve / Accept
# variance actions exactly. Anything else is treated as ``Open``.
DILIGENCE_STATUSES: frozenset[str] = frozenset({"Open", "Resolved", "Accepted"})

_THESIS_SECTION = "investment_thesis"
_RECOMMENDATION_SECTION = "recommendation"
_RISK_SECTION = "risk_factors"

# The override keys this layer consumes. The ``memo_*_edited`` flags and the
# ``memo_*_run_id`` stamps are intentionally excluded — they are UI
# bookkeeping, not memo content.
_OVERRIDE_KEYS: tuple[str, ...] = (
    "memo_thesis",
    "memo_highlights",
    "memo_recommendation_override",
    "memo_recommendation_confirmed",
    "memo_risks",
    "memo_diligence",
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


def recommendation_decision(
    overrides: dict[str, Any] | None,
) -> tuple[str | None, bool]:
    """``(selected_verdict, confirmed)`` from the persisted memo overrides.

    ``selected_verdict`` is ``None`` unless ``memo_recommendation_override``
    is in vocabulary; ``confirmed`` is ``True`` only when
    ``memo_recommendation_confirmed`` is literally ``True`` *and* a valid
    verdict is selected. Legacy rows that carry a verdict but no
    ``_confirmed`` key are unconfirmed — the decision was never explicitly
    recorded under the FON-54a rule, so it is reported as pending.
    """
    if not isinstance(overrides, dict):
        return None, False
    verdict = _verdict(overrides.get("memo_recommendation_override"))
    confirmed = verdict is not None and overrides.get(
        "memo_recommendation_confirmed"
    ) is True
    return verdict, confirmed


def ic_recommendation_label(overrides: dict[str, Any] | None) -> str:
    """The IC recommendation as every consumer must print it.

    The confirmed verdict, else :data:`PENDING_DECISION` — never the
    model's inferred verdict, never a selected-but-unconfirmed verdict.
    """
    verdict, confirmed = recommendation_decision(overrides)
    return verdict if (verdict is not None and confirmed) else PENDING_DECISION


def ic_recommendation_reason(overrides: dict[str, Any] | None) -> ReasonCode | None:
    """Why the IC recommendation is a dash, as a :class:`ReasonCode`.

    Phase 4.3: :func:`ic_recommendation_label` has always said
    :data:`PENDING_DECISION` until an analyst selects *and* confirms a
    verdict; that string is a refusal wearing prose. This is the same fact,
    machine-readable — ``awaiting_analyst`` while pending, ``None`` once the
    verdict is confirmed (there is nothing being refused any more).

    The label itself is untouched: a consumer that prints the string keeps
    printing exactly what it printed before.
    """
    _verdict, confirmed = recommendation_decision(overrides)
    return None if confirmed else ReasonCode.AWAITING_ANALYST


def diligence_status(overrides: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Normalised ``memo_diligence`` map: ``concept → {"status", "note"?, "updated_at"?}``.

    Accepts the frontend shape (``{concept: {"status": ..., ...}}``) and,
    defensively, ``{concept: "Resolved"}``. Entries whose status is not in
    :data:`DILIGENCE_STATUSES` are dropped (i.e. read as ``Open``); an
    absent concept is ``Open``. Pure — no clock, no I/O.
    """
    if not isinstance(overrides, dict):
        return {}
    raw = overrides.get("memo_diligence")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for concept, entry in raw.items():
        if not isinstance(concept, str) or not concept.strip():
            continue
        if isinstance(entry, str):
            status: Any = entry
            note: str | None = None
            updated_at: str | None = None
        elif isinstance(entry, dict):
            status = entry.get("status")
            note = _clean_str(entry.get("note"))
            updated_at = _clean_str(entry.get("updated_at"))
        else:
            continue
        if not isinstance(status, str) or status not in DILIGENCE_STATUSES:
            continue
        normalised: dict[str, Any] = {"status": status}
        if note is not None:
            normalised["note"] = note
        if updated_at is not None:
            normalised["updated_at"] = updated_at
        out[concept.strip()] = normalised
    return out


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
    verdict, confirmed = recommendation_decision(overrides)
    if verdict is None:
        return original_body
    # A selected-but-unconfirmed verdict is not a decision yet: the memo says
    # so rather than printing the draft verdict as if it were recorded.
    headline = f"IC recommendation: {verdict if confirmed else PENDING_DECISION}."
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


__all__ = [
    "DILIGENCE_STATUSES",
    "PENDING_DECISION",
    "VALID_VERDICTS",
    "apply_memo_overrides",
    "diligence_status",
    "has_memo_overrides",
    "ic_recommendation_label",
    "ic_recommendation_reason",
    "recommendation_decision",
]
