"""The IC Memo's run stamps are BOOKKEEPING — they never become memo prose.

FON-54 (Sam, 2026-09-14): "the model-driven IC Memo figures update, but
previously generated AI content can remain based on the old underwriting …
Regenerating updated the Investment Thesis to the current 32.2% IRR / 3.44x
EM, but the Key Highlights still referenced the old -2.5% IRR / 0.88x EM /
0.78x DSCR."

The defect and its fix live in ``apps/web/.../ICMemoTab.tsx``: the tab now
regenerates the three AI-drafted sections as one set, and stamps each with the
engine run it was drafted against so prose that predates the current
underwriting is flagged instead of shown as current. That introduced five new
``field_overrides`` keys —

    memo_thesis_run_id / memo_highlights_run_id / memo_risks_run_id
    memo_highlights_edited / memo_risks_edited

— which this worker must treat exactly as it already treats
``memo_thesis_edited``:

  1. they pass the override-justification gate (the ``^memo_`` exemption), so
     stamping a section is never refused with a 422 asking the analyst to
     justify a note about a note;
  2. they are NOT memo content. A deal carrying only stamps gets the
     generator's memo byte-identical, and a deal carrying stamps beside real
     overrides gets a memo in which no stamp appears anywhere.

Pure and hermetic: no DB, no LLM, no clock.
"""

from __future__ import annotations

from app.api.deals import _override_needs_note
from app.memo_overrides import apply_memo_overrides, has_memo_overrides
from app.services.engine_runner import _OVERRIDE_NON_ENGINE_KEYS

# Every bookkeeping key the IC Memo tab writes beside the prose.
_BOOKKEEPING_KEYS = (
    "memo_thesis_edited",
    "memo_highlights_edited",
    "memo_risks_edited",
    "memo_thesis_run_id",
    "memo_highlights_run_id",
    "memo_risks_run_id",
)

_RUN_NOW = "run-2026-09-14"
_RUN_THEN = "run-2026-09-01"


def _sections() -> list[dict]:
    return [
        {
            "section_id": "investment_thesis",
            "title": "Investment Thesis",
            "body": "Generated thesis prose.",
            "citations": [],
        },
        {
            "section_id": "risk_factors",
            "title": "Risk Factors",
            "body": "Generated risk prose.",
            "citations": [],
        },
        {
            "section_id": "recommendation",
            "title": "Recommendation",
            "body": "Generated rationale.",
            "citations": [],
        },
    ]


# ─────────────────────────── the note gate ────────────────────────────────


def test_run_stamps_and_edited_flags_need_no_justification() -> None:
    """A stamp records which run the prose describes. There is nothing to
    justify — it is not an override of a sourced value, it is the provenance
    OF an override. The ``^memo_`` pattern already says so; this pins that the
    new key names actually fall under it rather than 422-ing the regenerate.
    """
    for key in _BOOKKEEPING_KEYS:
        assert _override_needs_note(key, _OVERRIDE_NON_ENGINE_KEYS) is False, key


def test_the_gate_still_bites_on_a_real_engine_input() -> None:
    """The exemption is scoped, not a hole: an engine input still needs one."""
    assert _override_needs_note("exit_cap_rate", _OVERRIDE_NON_ENGINE_KEYS) is True


# ──────────────────── stamps are never memo content ───────────────────────


def test_stamps_alone_are_not_a_memo_override() -> None:
    """A deal that has only been stamped has authored nothing.

    ``has_memo_overrides`` must stay false so ``apply_memo_overrides`` returns
    the SAME list object and the memo is byte-identical to the generator's —
    the module's opt-in guarantee.
    """
    overrides = {
        "memo_thesis_run_id": _RUN_NOW,
        "memo_highlights_run_id": _RUN_NOW,
        "memo_risks_run_id": _RUN_NOW,
        "memo_thesis_edited": False,
        "memo_highlights_edited": False,
        "memo_risks_edited": False,
    }
    assert has_memo_overrides(overrides) is False

    sections = _sections()
    assert apply_memo_overrides(sections, overrides) is sections


def test_no_stamp_leaks_into_a_memo_body() -> None:
    """With real overrides present, the stamps beside them stay invisible."""
    overrides = {
        "memo_thesis": "Analyst thesis wins.",
        "memo_thesis_edited": True,
        "memo_thesis_run_id": _RUN_THEN,
        "memo_highlights": [{"t": "Levered IRR of 32.2% clears target.", "ai": True}],
        "memo_highlights_edited": False,
        "memo_highlights_run_id": _RUN_NOW,
        "memo_risks": [{"t": "Exit cap expansion compresses the multiple.", "ai": True}],
        "memo_risks_edited": False,
        "memo_risks_run_id": _RUN_NOW,
    }
    out = apply_memo_overrides(_sections(), overrides)

    bodies = "\n".join(s["body"] for s in out)
    # The analyst's content IS there…
    assert "Analyst thesis wins." in bodies
    assert "Levered IRR of 32.2% clears target." in bodies
    assert "Exit cap expansion compresses the multiple." in bodies
    # …and not one scrap of bookkeeping is.
    assert _RUN_NOW not in bodies
    assert _RUN_THEN not in bodies
    for key in _BOOKKEEPING_KEYS:
        assert key not in bodies


def test_a_stamp_cannot_stand_in_for_missing_prose() -> None:
    """A stamped-but-empty section is still empty.

    Guards the "never fabricate" rule from the other side: a run id is not
    content, so it can never be printed where the prose should have been.
    """
    overrides = {
        "memo_thesis": "   ",
        "memo_thesis_run_id": _RUN_NOW,
        "memo_highlights": [],
        "memo_highlights_run_id": _RUN_NOW,
        "memo_risks": [],
        "memo_risks_run_id": _RUN_NOW,
    }
    assert has_memo_overrides(overrides) is False
    sections = _sections()
    assert apply_memo_overrides(sections, overrides) is sections
