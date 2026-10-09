"""Every SQL statement in the cell-comments and P&L round-trip modules carries
a ``tenant_id`` predicate (FON-41 E-011 / E-013 / E-017).

Same static guard as ``test_documents_sql_tenant_predicates.py``: the runtime
``tenant_middleware`` pages on a miss; this fails the build first.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "app" / "api"
MODULES = ("cell_comments.py", "pl_roundtrip.py")


def _statements(src: str) -> list[tuple[int, str]]:
    """Each ``text(...)`` call's literal SQL, with its starting line number."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"text\(\s*", src):
        start = m.end()
        depth, i = 1, start
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        body = src[start : i - 1]
        literals = re.findall(r'"""(.*?)"""|"((?:[^"\\]|\\.)*)"', body, flags=re.S)
        sql = " ".join(a or b for a, b in literals)
        if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", sql):
            out.append((src.count("\n", 0, m.start()) + 1, sql + " " + body))
    return out


@pytest.mark.parametrize("module", MODULES)
def test_every_statement_is_tenant_scoped(module: str) -> None:
    src = (API / module).read_text(encoding="utf-8")
    stmts = _statements(src)
    assert stmts, f"no SQL found in {module} — extractor drifted"
    unscoped = [(ln, st) for ln, st in stmts if "tenant" not in st]
    assert not unscoped, f"{module}: SQL without a tenant predicate:\n" + "\n".join(
        f"  L{ln}: {st[:160]}" for ln, st in unscoped
    )


def test_comment_reads_and_writes_filter_on_tenant_column() -> None:
    """Stronger than the generic check: every cell_comments statement names
    the ``tenant_id`` column (as a predicate or the inserted value)."""
    src = (API / "cell_comments.py").read_text(encoding="utf-8")
    stmts = [st for _ln, st in _statements(src) if "cell_comments" in st]
    assert len(stmts) >= 4  # list, insert, resolve, re-open
    for st in stmts:
        assert "tenant_id" in st, st[:200]
