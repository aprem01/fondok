"""Regenerate ``input/str_ang_2025_05_extracted.json`` — the deterministic
STR template extraction of the deal's May 2025 monthly STAR report
(``FL Miami South Beach Anglers (Eshan)/2 - STR Reports/ANG-20250500-USD-E.xlsx``).

That report's Running-12-Month block is the competitive set the reference
Index Analysis tab keys for 2025 (comp set 75.496% / $310.776, rounded there
to 75.5% / $310.78). The template extractor is deterministic (no LLM), so the
fixture is exactly what an upload of the file extracts today.

Usage (from the repo root)::

    DATABASE_URL=sqlite+aiosqlite:///./tmp-fixture.db \\
        apps/worker/.venv/bin/python evals/golden-set/kimpton-angler/scripts/build_str_fixture.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "apps" / "worker"))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./tmp-fixture.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

SOURCE = REPO / "FL Miami South Beach Anglers (Eshan)" / "2 - STR Reports" / "ANG-20250500-USD-E.xlsx"
OUT = REPO / "evals" / "golden-set" / "kimpton-angler" / "input" / "str_ang_2025_05_extracted.json"


async def main() -> None:
    from app.extraction.parser import parse_document
    from app.extraction.template_extractors.str_trend import try_template_extract

    parsed = await parse_document(file_bytes=SOURCE.read_bytes(), filename=SOURCE.name)
    result = try_template_extract(parsed, "STR_TREND")
    if result is None:
        raise SystemExit("the STR template extractor declined the report")
    fields = [f if isinstance(f, dict) else f.model_dump(mode="json") for f in result.fields]
    doc = {
        "comment": (
            "Deterministic STR template extraction (app.extraction.template_extractors.str_trend) of "
            "ANG-20250500-USD-E.xlsx, the May 2025 monthly STAR report in the deal's data room. "
            "Regenerate with scripts/build_str_fixture.py."
        ),
        "document_id": "kimpton-angler-str-2025-05",
        "filename": SOURCE.name,
        "doc_type": "STR_TREND",
        "coverage_note": result.coverage_note,
        "fields": fields,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {OUT.relative_to(REPO)} ({len(fields)} fields)")


if __name__ == "__main__":
    asyncio.run(main())
