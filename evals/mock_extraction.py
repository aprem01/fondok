"""The canned extraction backend for runs that must never call an LLM.

C7 (go-live audit, 2026-10-30): the worker's extraction pipeline used to read
an ``EVALS_MOCK`` environment variable INSIDE ``app/api/documents.py`` and,
when set, return the canned fields below as the document's extraction — a
production module one mis-set env var away from serving fixture numbers.
The canned payload now lives here, outside the worker package, and reaches
the pipeline only through the pipeline's injection seam
(``app.api.documents.set_extraction_backend``) when a harness installs it:

* the worker test suite (``apps/worker/tests/conftest.py`` installs it for
  every test; a test that drives the real backend opts out with
  ``@pytest.mark.real_extraction_backend``);
* the golden-set harness (``evals/run.py`` installs it before running cases,
  so no case can reach an LLM — CI runs it without an API key).

``noi_year_1`` / ``occupancy_year_1`` are deliberately not real USALI field
names any engine reads: the canned extraction exercises the pipeline's wiring
(status transitions, persistence, audit), never a model number.
"""

from __future__ import annotations

from typing import Any

MOCK_BACKEND_NAME = "mock-evals"


def mock_extraction_payload() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Canned fields + confidence report."""
    fields = [
        {
            "field_name": "noi_year_1",
            "value": 1234567.0,
            "unit": "USD",
            "source_page": 1,
            "confidence": 0.92,
            "raw_text": "Net Operating Income: $1,234,567",
        },
        {
            "field_name": "occupancy_year_1",
            "value": 0.74,
            "unit": "ratio",
            "source_page": 1,
            "confidence": 0.88,
            "raw_text": "Occupancy: 74%",
        },
    ]
    confidence = {
        "overall": 0.9,
        "by_field": {"noi_year_1": 0.92, "occupancy_year_1": 0.88},
        "low_confidence_fields": [],
        "requires_human_review": False,
    }
    return fields, confidence


async def _mock_extract(request: Any) -> tuple[list[dict[str, Any]], dict[str, Any], str, None]:
    fields, confidence = mock_extraction_payload()
    # No classification: the pipeline keeps its own (filename / analyst
    # tag) doc type exactly as the old env short-circuit did.
    return fields, confidence, MOCK_BACKEND_NAME, None


def mock_extraction_backend() -> Any:
    """The canned :class:`app.api.documents.ExtractionBackend` (built lazily
    so importing this module never imports the worker)."""
    from app.api.documents import ExtractionBackend

    return ExtractionBackend(
        name=MOCK_BACKEND_NAME,
        extract=_mock_extract,
        critic_narrative_pass=False,
    )


def install_mock_extraction_backend() -> Any:
    """Install the canned backend; returns the previous backend."""
    from app.api.documents import set_extraction_backend

    return set_extraction_backend(mock_extraction_backend())
