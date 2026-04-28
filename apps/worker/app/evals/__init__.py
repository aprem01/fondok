"""Eval harnesses for grading agent output.

These run deterministic rule-based checks (banned phrases, structural
requirements, hotel-banker number conventions, citation discipline)
against the Analyst's drafted memos so we have a regression check
before/after prompt changes — without needing an LLM-as-judge call in
the inner loop.

Each rule fires in milliseconds, no network, no LLM. The result is a
list of ``MemoFinding`` the Analyst surfaces in ``InvestmentMemo``
metadata; critical errors trigger a "regenerate" suggestion in the
worker response, warnings are advisory.
"""

from .memo_quality import (
    MemoEvalResult,
    MemoFinding,
    Severity,
    evaluate_memo,
)

__all__ = [
    "MemoEvalResult",
    "MemoFinding",
    "Severity",
    "evaluate_memo",
]
