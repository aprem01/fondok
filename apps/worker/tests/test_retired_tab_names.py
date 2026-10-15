"""Sam #5 — worker-generated user-facing text uses the live tab names.

The web app's tabs are Data Room, Overview, CAPEX, Market Comps, P&L,
Financing, Partnership, Cash Flow, Investment Bridge, Scenario Analysis and IC
Memo (Returns is a section of Overview; P&L's sub-tabs are Historical P&L /
Future P&L). Provenance notes, value-trace notes, refusal reasons and export
labels the worker writes are shown to the analyst verbatim, so none may point
at a retired tab ("Debt → amortization schedule", "Financials → Projections").

Scans every string constant in ``app/`` with the AST — docstrings and comments
are code history and are skipped. ``STR_MARKET_OVERRIDE_NOTE`` is the one
allowed exception: it is a persisted protocol string matched by equality on
existing deals' ``field_overrides`` (renaming it would silently un-tag them).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

RETIRED = [
    re.compile(
        r"\b(?:Investment|Debt|Market|Financials|Historicals|Projections|Returns|Scenarios|Export)"
        r" tab(?:'s|s)?\b"
    ),
    re.compile(r"\b(?:Investment|Debt|Market|Financials|Historicals|Projections)\s*(?:→|->)"),
    re.compile(r"→\s*(?:Historicals|Projections)\b"),
    re.compile(r"\bfeed Projections\b"),
]

ALLOWED = {"STR comp-set market rates (Market tab)"}

# LLM-facing prompts, not user-facing copy: the extractor's system prompt tells
# the model which downstream reader consumes a path. Editing it changes the
# extraction prompt (and so the extraction cache key) for no user benefit.
ALLOWED_FILES = {"app/agents/extractor.py"}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _retired_strings() -> list[str]:
    hits: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.relative_to(APP.parent).as_posix() in ALLOWED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in skip or node.value in ALLOWED:
                continue
            if any(p.search(node.value) for p in RETIRED):
                hits.append(f"{path.relative_to(APP.parent)}:{node.lineno}: {node.value[:120]!r}")
    return hits


def test_no_worker_string_names_a_retired_tab() -> None:
    assert _retired_strings() == []


@pytest.mark.parametrize(
    "text",
    [
        "Debt → amortization schedule.",
        "Financials → Projections (NOI before FF&E reserve).",
        "The percentage is the Debt tab's senior origination fee",
        "Targets by year do not feed Projections yet",
    ],
)
def test_scanner_catches_the_old_copy(text: str) -> None:
    assert any(p.search(text) for p in RETIRED)


def test_cash_flow_notes_name_live_tabs() -> None:
    src = (APP / "engines" / "cash_flow.py").read_text(encoding="utf-8")
    assert 'note="Financing → amortization schedule."' in src
    assert 'note="P&L → Future P&L (NOI before FF&E reserve)."' in src
    assert 'note="CAPEX → Sources & Uses (total uses funded at close)."' in src
