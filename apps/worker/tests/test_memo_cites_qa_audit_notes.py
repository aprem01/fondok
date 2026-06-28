"""Tests that broker Q&A audit notes flow into the Analyst memo prompt.

We don't run a real LLM here — we capture the rendered user prompt and
assert the footnote-formatted block is present + the instruction to cite
``[footnote N]`` inline is included.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-memo-cites.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


def test_format_qa_audit_notes_renders_numbered_block() -> None:
    """Three audit notes render as a numbered ``=== BROKER Q&A AUDIT NOTES ===`` block."""
    from app.agents.analyst import _format_qa_audit_notes

    notes = [
        "Per broker reply: F&B contract reset Nov-24.",
        "Per broker reply: insurance hardening confirmed.",
        "Per broker reply: utilities baseline corrected.",
    ]
    rendered = _format_qa_audit_notes(notes)

    assert "=== BROKER Q&A AUDIT NOTES" in rendered
    assert "[footnote N]" in rendered  # instruction line
    # Order preserved + numbered 1, 2, 3.
    assert "1. Per broker reply: F&B contract reset Nov-24." in rendered
    assert "2. Per broker reply: insurance hardening confirmed." in rendered
    assert "3. Per broker reply: utilities baseline corrected." in rendered


def test_format_qa_audit_notes_empty_returns_no_footnotes_section() -> None:
    """No notes → still emits the section header (cache stability)."""
    from app.agents.analyst import _format_qa_audit_notes

    rendered = _format_qa_audit_notes([])
    assert "=== BROKER Q&A AUDIT NOTES" in rendered
    assert "no resolved broker q&a pairs" in rendered.lower()


def test_analyst_input_carries_audit_notes_into_user_prompt() -> None:
    """The audit notes wired through ``AnalystInput`` appear in the prompt."""
    from app.agents.analyst import AnalystInput, _build_user_prompt

    payload = AnalystInput(
        tenant_id="t-1",
        deal_id="d-1",
        broker_qa_audit_notes=[
            "Per broker reply: F&B operator reset Nov-24, self-managed since.",
        ],
    )
    prompt = _build_user_prompt(payload)

    assert "BROKER Q&A AUDIT NOTES" in prompt
    assert "F&B operator reset Nov-24" in prompt
    assert "[footnote N]" in prompt
