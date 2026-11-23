"""FON-80 (export side) — the memo PDF and IC deck footers print the deal's
Model/Data Readiness, the same line as the web deal header, in place of the
retired "AI Confidence: NN%" badge.

* ``app.export.readiness`` mirrors ``apps/web/src/lib/project.ts``
  (``modelDataReadiness`` / ``readinessText``): same step order, thresholds
  and labels;
* the memo badge and the deck footer read ``Readiness: <step> · extraction
  NN%`` once a document has been scored, and state the missing score the
  export way (``not available: no extraction scored yet``) when none was;
* engines that produced output put the deal at "Model run"; ``state ==
  READY`` at "Validated"; the ``state`` key is optional on the appendix.

Hermetic: no DB, no LLM, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.export.readiness import (
    Readiness,
    model_data_readiness,
    readiness_footer,
    readiness_from_appendix,
    readiness_text,
)
from app.export.refusals import NOT_AVAILABLE_PREFIX
from tests.fixtures.kimpton_export_payload import (
    kimpton_deal,
    kimpton_memo,
    kimpton_model,
)

# ═════════════════ the helper mirrors project.ts ═════════════════


@pytest.mark.parametrize(
    ("document_count", "ai_confidence", "model_ran", "state", "step", "index", "label"),
    [
        # Precedence top-down, exactly as modelDataReadiness():
        (0, None, False, "READY", "validated", 4, "Validated"),
        (3, 0.82, True, "VALIDATING", "model_run", 3, "Model run"),
        (3, 0.82, False, "VALIDATING", "extracted", 2, "Extracted"),
        (3, 0.82, False, "ONBOARDING", "extracted", 2, "Extracted"),
        (3, None, False, "ONBOARDING", "uploaded", 1, "Documents uploaded"),
        (0, None, False, "ONBOARDING", "no_documents", 0, "No documents"),
        (0, None, False, None, "no_documents", 0, "No documents"),
        # A model run outranks the score and the document count.
        (0, None, True, None, "model_run", 3, "Model run"),
        # A score with no documents listed still reads Extracted (web parity).
        (None, 0.5, False, None, "extracted", 2, "Extracted"),
    ],
)
def test_step_selection_matches_the_web_thresholds(
    document_count, ai_confidence, model_ran, state, step, index, label
) -> None:
    got = model_data_readiness(
        document_count=document_count,
        ai_confidence=ai_confidence,
        model_ran=model_ran,
        state=state,
    )
    assert got is not None
    assert (got.step, got.index, got.label) == (step, index, label)


def test_nothing_known_is_none_and_prints_a_dash() -> None:
    assert (
        model_data_readiness(document_count=None, ai_confidence=None, model_ran=False, state=None)
        is None
    )
    assert readiness_text(None) == "—"


def test_readiness_text_matches_the_web_wording() -> None:
    assert readiness_text(Readiness("extracted", 2, "Extracted", 82)) == (
        "Extracted · extraction 82%"
    )
    assert readiness_text(Readiness("uploaded", 1, "Documents uploaded", None)) == (
        "Documents uploaded"
    )
    # displayConfidence(): whole percent, rounded; a missing score is never 0.
    assert readiness_from_appendix({"documents_reviewed": ["a"], "ai_confidence": 0.8749}).extraction_confidence == 87
    assert readiness_from_appendix({"documents_reviewed": ["a"], "ai_confidence": "oops"}).extraction_confidence is None
    assert readiness_from_appendix({"documents_reviewed": ["a"]}).extraction_confidence is None


def test_appendix_state_key_is_optional() -> None:
    base = {"documents_reviewed": ["om.pdf"], "engines_run": ["returns"], "ai_confidence": 0.9}
    assert readiness_from_appendix(base).step == "model_run"
    assert readiness_from_appendix({**base, "state": "VALIDATING"}).step == "model_run"
    assert readiness_from_appendix({**base, "state": "READY"}).step == "validated"
    assert readiness_from_appendix({}) is None
    assert readiness_from_appendix(None) is None


# ═════════════════ the one footer line ═════════════════


def test_footer_three_cases() -> None:
    scored = {"documents_reviewed": ["om.pdf"], "engines_run": [], "ai_confidence": 0.87}
    assert readiness_footer(scored) == "Readiness: Extracted · extraction 87%"

    unscored = {"documents_reviewed": [], "engines_run": []}
    assert readiness_footer(unscored) == (
        f"Readiness: No documents · extraction {NOT_AVAILABLE_PREFIX}: no extraction scored yet"
    )

    ran = {"documents_reviewed": ["om.pdf"], "engines_run": ["returns"], "ai_confidence": 0.87}
    assert readiness_footer(ran) == "Readiness: Model run · extraction 87%"

    validated = {**ran, "state": "READY"}
    assert readiness_footer(validated) == "Readiness: Validated · extraction 87%"

    assert readiness_footer({}) == (
        f"Readiness: {NOT_AVAILABLE_PREFIX}: no processing state recorded on the deal"
    )


# ═════════════════ memo PDF badge ═════════════════


def _memo_with_appendix(appendix: dict) -> dict:
    memo = kimpton_memo()
    memo["appendix"] = appendix
    return memo


def test_memo_badge_states_readiness_with_the_score() -> None:
    from app.export.memo_pdf import _render_html

    html_str = _render_html(
        _memo_with_appendix({"documents_reviewed": ["om.pdf"], "engines_run": [], "ai_confidence": 0.87}),
        kimpton_model(),
    )
    assert "Readiness: Extracted · extraction 87%" in html_str
    assert "AI Confidence" not in html_str


def test_memo_badge_states_readiness_without_a_score() -> None:
    from app.export.memo_pdf import _render_html

    html_str = _render_html(
        _memo_with_appendix({"documents_reviewed": ["om.pdf"], "engines_run": []}),
        kimpton_model(),
    )
    assert (
        f"Readiness: Documents uploaded · extraction {NOT_AVAILABLE_PREFIX}: no extraction scored yet"
        in html_str
    )
    assert "AI Confidence" not in html_str
    assert "extraction 0%" not in html_str


def test_memo_badge_reads_model_run_when_engines_ran() -> None:
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())  # fixture: 6 engines, 0.87
    assert "Readiness: Model run · extraction 87%" in html_str


# ═════════════════ deck footer ═════════════════


def _pptx_text(path: Path) -> str:
    from pptx import Presentation

    prs = Presentation(str(path))
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def test_deck_footer_states_readiness_with_the_score(tmp_path: Path) -> None:
    from app.export import build_pptx

    memo = _memo_with_appendix(
        {"documents_reviewed": ["om.pdf"], "engines_run": [], "ai_confidence": 0.87}
    )
    text_blob = _pptx_text(build_pptx(kimpton_deal(), kimpton_model(), memo, tmp_path / "a.pptx"))
    assert "Readiness: Extracted · extraction 87%   ·   Engines: 0" in text_blob
    assert "AI Confidence" not in text_blob


def test_deck_footer_states_readiness_without_a_score(tmp_path: Path) -> None:
    from app.export import build_pptx

    memo = _memo_with_appendix({"documents_reviewed": [], "engines_run": []})
    text_blob = _pptx_text(build_pptx(kimpton_deal(), kimpton_model(), memo, tmp_path / "b.pptx"))
    assert (
        f"Readiness: No documents · extraction {NOT_AVAILABLE_PREFIX}: no extraction scored yet"
        "   ·   Engines: 0"
    ) in text_blob
    assert "AI Confidence" not in text_blob
    assert "extraction 0%" not in text_blob


def test_deck_footer_reads_model_run_when_engines_ran(tmp_path: Path) -> None:
    from app.export import build_pptx

    text_blob = _pptx_text(
        build_pptx(kimpton_deal(), kimpton_model(), kimpton_memo(), tmp_path / "c.pptx")
    )
    assert "Readiness: Model run · extraction 87%   ·   Engines: 6" in text_blob
