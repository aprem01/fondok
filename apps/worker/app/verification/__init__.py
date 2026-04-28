"""Deterministic chain-of-verification pass for hotel underwriting.

The Extractor agent (LLM) pulls structured fields out of source documents
and tags each ``ExtractionField`` with a ``source_page``. Before the
analyst signs off at Gate 1 — and before the IC sees a memo grounded in
those numbers — we run ``verify_citations`` which re-reads every
cited page, parses all numbers out of it deterministically, and reports
whether the extracted value appears.

This is a cheap guard against the #1 IC objection ("the model made up
a number") and the LP-disclosure risk that follows. The verifier is
*deterministic* on purpose — no LLM call, no network. An LP or auditor
can re-run it and reproduce the report exactly.
"""

from __future__ import annotations

from .numerics import verify_citations

__all__ = ["verify_citations"]
