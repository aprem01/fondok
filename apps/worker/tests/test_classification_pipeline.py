"""Direct table-tests for the extracted doc-type classification funcs.

Wave 5 dispatch refactor pulled the two scattered classification phases
out of ``_run_graph_extraction`` into pure(-ish) functions:

* ``classify_for_extraction`` — Phase 1 (pre-extraction): pre-router
  structural override → Router LLM → filename-hint fallback.
* ``refine_doc_type_post_extraction`` — Phase 2 (post-extraction):
  ``_refine_pnl_doc_type`` narrowing → Bug H (non-financial → P&L) →
  Bug J (financial → STR_TREND) → misclassification banner set/clear.

These tests exercise BOTH functions directly (no LLM, no DB) by
monkeypatching the recognizer / router seams. They pin the decision
contract the refactor promised to preserve byte-for-byte.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Isolate the test — SQLite DSN before any worker imports so a
# developer's shell-level DATABASE_URL doesn't bleed in.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-classification-pipeline.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")


# ── Fakes for the recognizer / router seams ─────────────────────────


@dataclass
class _FakeSignals:
    """Mirror of ``StructuralSignals`` surface the refine fn reads."""

    is_pnl: bool = False
    pnl_score: float = 0.0
    is_str: bool = False
    str_score: float = 0.0
    reason: str = "test-signals"


@dataclass
class _FakeTextSignals:
    """Mirror of ``TextSignals`` surface classify_for_extraction reads."""

    looks_str: bool = False
    str_marker_hits: int = 0
    pnl_marker_hits: int = 0
    str_markers_matched: list[str] = field(default_factory=list)


@dataclass
class _FakeRouterOut:
    doc_type: str | None
    route: str = "extract"


def _patch_classify(monkeypatch, signals: _FakeSignals) -> None:
    monkeypatch.setattr(
        "app.services.structural_recognizer.classify_structure",
        lambda payload: signals,
    )


def _patch_detect(monkeypatch, text_signals: _FakeTextSignals) -> None:
    monkeypatch.setattr(
        "app.services.structural_recognizer.detect_text_signals",
        lambda content: text_signals,
    )


# ══════════════════════════════════════════════════════════════════
# Phase 2 — refine_doc_type_post_extraction
# ══════════════════════════════════════════════════════════════════


def _refine(monkeypatch, *, classified, fields, user, signals):
    """Call the refine fn with the recognizer patched to ``signals``."""
    from app.api.documents import refine_doc_type_post_extraction

    _patch_classify(monkeypatch, signals)
    return refine_doc_type_post_extraction(
        classified_doc_type=classified,
        route=None,
        fields=fields,
        user_provided_doc_type=user,
        doc_id="doc-test",
    )


def test_refine_narrows_t12_to_pnl_monthly(monkeypatch) -> None:
    """_refine_pnl_doc_type narrowing: monthly period_type → PNL_MONTHLY."""
    fields = [
        {"field_name": "p_and_l_usali.period_type", "value": "month"},
    ]
    res = _refine(
        monkeypatch,
        classified="T12",
        fields=fields,
        user=None,
        signals=_FakeSignals(),  # neutral — no structural override
    )
    assert res.doc_type == "PNL_MONTHLY"
    assert res.misclassified is False
    # No user tag + no structural override → nothing proposed.
    assert res.ai_proposed_doc_type is None
    assert res.router_overridden_original is None


def test_refine_bug_h_property_info_to_t12(monkeypatch) -> None:
    """Bug H: non-financial router label + confident P&L → T12 lane."""
    res = _refine(
        monkeypatch,
        classified="PROPERTY_INFO",
        fields=[],  # no period_type → T12
        user=None,
        signals=_FakeSignals(is_pnl=True, pnl_score=1.0),
    )
    assert res.doc_type == "T12"
    assert res.classified_doc_type == "T12"
    assert res.misclassified is False
    # Router's original call preserved for the banner.
    assert res.router_overridden_original == "PROPERTY_INFO"
    assert res.ai_proposed_doc_type == "PROPERTY_INFO"


def test_refine_bug_h_below_threshold_no_override(monkeypatch) -> None:
    """Bug H gate: pnl_score under 0.85 floor does NOT override."""
    res = _refine(
        monkeypatch,
        classified="PROPERTY_INFO",
        fields=[],
        user=None,
        signals=_FakeSignals(is_pnl=True, pnl_score=0.5),
    )
    # Stays PROPERTY_INFO (via _refine_pnl_doc_type passthrough).
    assert res.doc_type == "PROPERTY_INFO"
    assert res.router_overridden_original is None


def test_refine_bug_j_t12_to_str_trend(monkeypatch) -> None:
    """Bug J: financial router label + confident STR → STR_TREND."""
    res = _refine(
        monkeypatch,
        classified="T12",
        fields=[],
        user=None,
        signals=_FakeSignals(is_str=True, str_score=0.9, is_pnl=False),
    )
    assert res.doc_type == "STR_TREND"
    assert res.classified_doc_type == "STR_TREND"
    assert res.router_overridden_original == "T12"
    assert res.ai_proposed_doc_type == "T12"


def test_refine_bug_j_not_when_is_pnl(monkeypatch) -> None:
    """Bug J belt-and-braces: is_pnl guard keeps it on the router call."""
    res = _refine(
        monkeypatch,
        classified="T12",
        fields=[],
        user=None,
        signals=_FakeSignals(is_str=True, str_score=0.9, is_pnl=True),
    )
    assert res.doc_type == "T12"
    assert res.router_overridden_original is None


def test_refine_misclassified_set_when_tags_disagree(monkeypatch) -> None:
    """User tagged STR_TREND, AI read OM → misclassified + proposal set."""
    res = _refine(
        monkeypatch,
        classified="OM",
        fields=[],
        user="STR_TREND",
        signals=_FakeSignals(),  # neutral, no P&L override
    )
    assert res.misclassified is True
    assert res.ai_proposed_doc_type == "OM"


def test_refine_misclassified_cleared_when_no_change(monkeypatch) -> None:
    """No user tag, no override → not misclassified, proposal cleared."""
    res = _refine(
        monkeypatch,
        classified="T12",
        fields=[],
        user=None,
        signals=_FakeSignals(),
    )
    assert res.misclassified is False
    assert res.ai_proposed_doc_type is None  # ai_proposed clear path


def test_refine_v4_trusts_user_pnl_tag(monkeypatch) -> None:
    """v4: P&L under a P&L tag + recognizer confirms → clear misclassified."""
    # Router said T12, user said PNL_MONTHLY — canonical differ so the
    # raw compare would flag misclassified, but the recognizer confirms
    # P&L shape and the user tag is in the P&L family → cleared, user
    # tag adopted as the doc_type.
    res = _refine(
        monkeypatch,
        classified="T12",
        fields=[],
        user="PNL_MONTHLY",
        signals=_FakeSignals(is_pnl=True, pnl_score=1.0),
    )
    assert res.misclassified is False
    assert res.doc_type == "PNLMONTHLY"  # canonical user tag adopted


def test_refine_v4_pnl_under_wrong_tag_flags(monkeypatch) -> None:
    """v4: recognizer says P&L but analyst tagged non-P&L → misclassified."""
    res = _refine(
        monkeypatch,
        classified="T12",
        fields=[],
        user="OM",
        signals=_FakeSignals(is_pnl=True, pnl_score=1.0),
    )
    assert res.misclassified is True
    assert res.ai_proposed_doc_type == "T12"


def test_refine_threads_route_through(monkeypatch) -> None:
    from app.api.documents import refine_doc_type_post_extraction

    _patch_classify(monkeypatch, _FakeSignals())
    res = refine_doc_type_post_extraction(
        classified_doc_type="T12",
        route="router:extract;extractor",
        fields=[],
        user_provided_doc_type=None,
        doc_id="doc-test",
    )
    assert res.route == "router:extract;extractor"


# ══════════════════════════════════════════════════════════════════
# Phase 1 — classify_for_extraction
# ══════════════════════════════════════════════════════════════════


async def _classify(monkeypatch, *, filename, router_out, looks_str):
    """Call classify_for_extraction with the router + detector patched."""
    from app.api.documents import classify_for_extraction

    called = {"router": False}

    async def _fake_run_router(_inp):
        called["router"] = True
        if isinstance(router_out, Exception):
            raise router_out
        return router_out

    monkeypatch.setattr("app.agents.router.run_router", _fake_run_router)
    _patch_detect(
        monkeypatch,
        _FakeTextSignals(
            looks_str=looks_str,
            str_marker_hits=6,
            pnl_marker_hits=1,
            str_markers_matched=["mpi", "ari"],
        ),
    )
    decision = await classify_for_extraction(
        filename=filename,
        pages=[{"text": "some content"}],
        tenant_id="t-1",
        deal_id="d-1",
        doc_id="doc-1",
    )
    return decision, called


async def test_classify_pre_router_str_override(monkeypatch) -> None:
    """looks_str → STR_TREND, router LLM skipped entirely."""
    decision, called = await _classify(
        monkeypatch,
        filename="mystery.xlsx",
        router_out=_FakeRouterOut(doc_type="T12"),
        looks_str=True,
    )
    assert decision.doc_type == "STR_TREND"
    assert decision.route == "extract-pre-router-structural-override"
    assert decision.source == "pre-router-override"
    assert called["router"] is False  # LLM bypassed


async def test_classify_router_pass_through(monkeypatch) -> None:
    """Valid router doc_type flows through with its route."""
    decision, called = await _classify(
        monkeypatch,
        filename="whatever.pdf",
        router_out=_FakeRouterOut(doc_type="OM", route="extract"),
        looks_str=False,
    )
    assert decision.doc_type == "OM"
    assert decision.route == "extract"
    assert decision.source == "router"
    assert called["router"] is True


async def test_classify_invalid_router_falls_back_to_hint(monkeypatch) -> None:
    """UNKNOWN sentinel (off-enum) → filename-hint fallback route."""
    from app.api.documents import _guess_doc_type

    decision, _ = await _classify(
        monkeypatch,
        filename="T12 2024.xlsx",
        router_out=_FakeRouterOut(doc_type="UNKNOWN", route="extract"),
        looks_str=False,
    )
    assert decision.doc_type == _guess_doc_type("T12 2024.xlsx")
    assert decision.route == "extract-hint-fallback"
    assert decision.source == "hint-fallback"


async def test_classify_router_exception_falls_back(monkeypatch) -> None:
    """run_router raising → hint + extract-fallback route."""
    from app.api.documents import _guess_doc_type

    decision, _ = await _classify(
        monkeypatch,
        filename="Coral_Bay_OM_v2.pdf",
        router_out=RuntimeError("boom"),
        looks_str=False,
    )
    hint = _guess_doc_type("Coral_Bay_OM_v2.pdf")
    assert decision.doc_type == hint
    # OM is a valid DocType so the fallback route survives the valid check.
    assert decision.route == "extract-fallback"
    assert decision.source == "router-fallback"
