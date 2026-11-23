"""Model/Data Readiness for the exports (FON-80, export side).

The web deal header stopped printing "AI Confidence: NN%" and prints the
deal's **Model/Data Readiness** instead — where the deal is in Fondok's
pipeline, read from facts the worker records, never a score
(``apps/web/src/lib/project.ts`` → ``modelDataReadiness`` / ``readinessText``).
The memo PDF and the IC deck footers must say the same thing, or an external
reviewer holding an export beside the screen sees two different verdicts.

This module is the worker-side mirror of that web logic. The step order,
thresholds and labels are copied verbatim from ``project.ts``; the one
difference is the "model run" fact: the web reads ``last_engine_run_at``,
the export payload carries the engines that produced output
(``appendix["engines_run"]``), and a non-empty list is the same fact.

Pure functions — the only side effect is the typed refusal that
:func:`readiness_footer` records (through :func:`unavailable`) when the deal
has no extraction score, which is the export-honesty convention: an unknown
is written as ``not available: <reason>``, never as 0%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fondok_schemas.reasons import ReasonCode

from .refusals import as_float, unavailable

ReadinessStep = Literal["no_documents", "uploaded", "extracted", "model_run", "validated"]

#: Number of steps, as ``READINESS_STEPS`` on the web.
READINESS_STEPS = 5

#: Step → (index, label), verbatim from ``modelDataReadiness`` in project.ts.
STEP_LABELS: dict[ReadinessStep, tuple[int, str]] = {
    "no_documents": (0, "No documents"),
    "uploaded": (1, "Documents uploaded"),
    "extracted": (2, "Extracted"),
    "model_run": (3, "Model run"),
    "validated": (4, "Validated"),
}

#: The deal state that alone reaches "Validated" (``deals.state``:
#: ONBOARDING → VALIDATING → READY).
VALIDATED_STATE = "READY"

#: Prefix of the export footers; the web pill has no prefix because its
#: column / tooltip names it.
FOOTER_PREFIX = "Readiness"


@dataclass(frozen=True)
class Readiness:
    step: ReadinessStep
    #: 0-4, the step's position — for a step indicator, never a percentage.
    index: int
    label: str
    #: The extraction confidence as a whole percent, when the deal has one.
    extraction_confidence: int | None


def display_confidence(ai_confidence: Any) -> int | None:
    """``displayConfidence`` on the web: a finite ratio → whole percent, else
    ``None`` (a missing score is never 0%)."""
    value = as_float(ai_confidence)
    if value is None or value != value or value in (float("inf"), float("-inf")):
        return None
    return round(value * 100)


def model_data_readiness(
    *,
    document_count: int | None,
    ai_confidence: Any,
    model_ran: bool,
    state: str | None,
) -> Readiness | None:
    """``modelDataReadiness`` on the web, same precedence top-down:

    READY state → Validated; a model run → Model run; an extraction score →
    Extracted; documents → Documents uploaded; zero documents → No documents;
    nothing known (no document count) → ``None``, which the web prints "—".
    """
    extraction_confidence = display_confidence(ai_confidence)
    docs = document_count if isinstance(document_count, int) else None

    def build(step: ReadinessStep) -> Readiness:
        index, label = STEP_LABELS[step]
        return Readiness(step, index, label, extraction_confidence)

    if state == VALIDATED_STATE:
        return build("validated")
    if model_ran:
        return build("model_run")
    if extraction_confidence is not None:
        return build("extracted")
    if docs is not None and docs > 0:
        return build("uploaded")
    if docs == 0:
        return build("no_documents")
    return None


def readiness_text(readiness: Readiness | None) -> str:
    """``readinessText`` on the web: the label, with the extraction confidence
    beside it once a document has been scored; "—" when nothing is known."""
    if readiness is None:
        return "—"
    if readiness.extraction_confidence is None:
        return readiness.label
    return f"{readiness.label} · extraction {readiness.extraction_confidence}%"


def readiness_from_appendix(appendix: dict[str, Any] | None) -> Readiness | None:
    """Read the readiness off a builder memo's ``appendix``.

    ``documents_reviewed`` is the document count (absent → unknown, as on the
    web when the list API reports no count), ``engines_run`` non-empty is the
    model run, ``ai_confidence`` the score and ``state`` the deal state —
    ``state`` is optional so payloads captured before it was carried still
    read the same.
    """
    appendix = appendix or {}
    docs = appendix.get("documents_reviewed")
    engines = appendix.get("engines_run") or []
    state = appendix.get("state")
    return model_data_readiness(
        document_count=len(docs) if isinstance(docs, list) else None,
        ai_confidence=appendix.get("ai_confidence"),
        model_ran=bool(engines),
        state=str(state) if state is not None else None,
    )


def readiness_footer(appendix: dict[str, Any] | None) -> str:
    """The one footer line the memo PDF and the deck print.

    ``Readiness: <step> · extraction NN%`` once a document has been scored —
    the web's :func:`readiness_text` behind the prefix. With no score the
    missing figure is stated the export way: ``Readiness: <step> · extraction
    not available: no extraction scored yet``. When even the step is unknown
    the whole line is a stated refusal rather than a guessed step.
    """
    readiness = readiness_from_appendix(appendix)
    if readiness is None:
        return f"{FOOTER_PREFIX}: " + unavailable(
            ReasonCode.NO_SOURCE, "no processing state recorded on the deal", "readiness"
        )
    if readiness.extraction_confidence is not None:
        return f"{FOOTER_PREFIX}: {readiness_text(readiness)}"
    return f"{FOOTER_PREFIX}: {readiness.label} · extraction " + unavailable(
        ReasonCode.NO_SOURCE, "no extraction scored yet", "ai_confidence"
    )


__all__ = [
    "FOOTER_PREFIX",
    "READINESS_STEPS",
    "STEP_LABELS",
    "VALIDATED_STATE",
    "Readiness",
    "ReadinessStep",
    "display_confidence",
    "model_data_readiness",
    "readiness_footer",
    "readiness_from_appendix",
    "readiness_text",
]
