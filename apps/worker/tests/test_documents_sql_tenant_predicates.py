"""Every UPDATE/DELETE on ``documents`` in the documents API carries a
``tenant_id`` predicate.

The runtime guard (``tenant_middleware``) pages on a miss; this test fails
the build first. 2026-10-07: the ``/extract`` and ``/reprocess`` status
flips wrote ``WHERE id = :id`` alone and paged Sentry during a re-extract.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "api" / "documents.py"


def _statements() -> list[tuple[int, str]]:
    text = SRC.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    # A statement is the run of adjacent string-literal lines starting at a
    # line that opens with UPDATE/DELETE on documents and ending at the first
    # line that is not a string literal continuation.
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.search(r'"(UPDATE|DELETE FROM) documents\b', line):
            buf = [line]
            j = i + 1
            while j < len(lines) and re.match(r'\s*(f?")', lines[j]):
                buf.append(lines[j])
                j += 1
            out.append((i + 1, " ".join(x.strip() for x in buf)))
    return out


def test_every_documents_write_is_tenant_scoped() -> None:
    stmts = _statements()
    assert stmts, "no documents writes found — regex drifted"
    unscoped = [(ln, st) for ln, st in stmts if "tenant_id" not in st]
    assert not unscoped, "documents writes without a tenant_id predicate:\n" + "\n".join(
        f"  L{ln}: {st[:140]}" for ln, st in unscoped
    )
