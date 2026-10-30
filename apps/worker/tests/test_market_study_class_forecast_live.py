"""FON-116 — live-model check: the MARKET_STUDY schema extracts the CoStar
class-forecast appendix from the REAL report's text.

Feeds the text layer of the Miami Beach submarket report (``evals/golden-set/
kimpton-angler/input/costar_submarket_text.json`` — overview + appendix pages)
through ``run_extractor`` on the dynamic-schema path and reads the result with
``read_class_forecast``. Asserts Sam's Luxury & Upper Upscale reference series
(2026 -1.2% / +1.8%, 2027 0.0% / -0.6%, 2028 -1.2% / +3.8%, 2029 +3.0% /
+2.8%) on PDF page 26, with the forecast / actual tags.

Hits the real Anthropic API: marked ``live_llm`` (deselected in CI) and
skipped unless ``FONDOK_LIVE_LLM=1`` plus a real ``ANTHROPIC_API_KEY`` are
exported — same opt-in as ``test_agents.py``.

    FONDOK_LIVE_LLM=1 ANTHROPIC_API_KEY=sk-ant-... \\
        uv run pytest tests/test_market_study_class_forecast_live.py -v -m live_llm
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

REPO_ROOT = Path(__file__).resolve().parents[3]
TEXT_FIXTURE = REPO_ROOT / "evals" / "golden-set" / "kimpton-angler" / "input" / "costar_submarket_text.json"

_PLACEHOLDER_KEY_PREFIXES = ("sk-ant-test-", "sk-test-")


def _live_llm_skip_reason() -> str | None:
    if os.environ.get("FONDOK_LIVE_LLM", "").strip() != "1":
        return "live-model test: opt in with FONDOK_LIVE_LLM=1 (plus a real ANTHROPIC_API_KEY)."
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.startswith(_PLACEHOLDER_KEY_PREFIXES) or "dummy" in key:
        return "FONDOK_LIVE_LLM=1 but ANTHROPIC_API_KEY is unset or a test placeholder."
    return None


_SKIP_REASON = _live_llm_skip_reason()

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]

LUU = "luxury_upper_upscale"
LUU_EXPECTED = {2026: (-0.012, 0.018), 2027: (0.0, -0.006), 2028: (-0.012, 0.038), 2029: (0.03, 0.028)}


@pytest.mark.asyncio
async def test_schema_extracts_the_class_forecast_from_the_real_report_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fondok_schemas import DocType

    from app.agents.extractor import ExtractorDocument, ExtractorInput, run_extractor
    from app.services.market_fields import parse_extraction_records
    from app.services.market_study_reader import read_class_forecast

    monkeypatch.setenv("EXTRACTOR_USE_DYNAMIC_SCHEMAS", "1")
    text = json.loads(TEXT_FIXTURE.read_text(encoding="utf-8"))
    pages = text["pages"]
    content = "\n\n".join(f"=== Page {p['pdf_page']} ===\n{p['text']}" for p in pages)
    doc = ExtractorDocument(
        document_id="miami-beach-costar-submarket-2025-12-10",
        filename=text["source_pdf"],
        doc_type=DocType.MARKET_STUDY,
        content=content,
        source_pages=[p["pdf_page"] for p in pages],
    )
    out = await run_extractor(
        ExtractorInput(
            tenant_id="00000000-0000-0000-0000-000000000001",
            deal_id="00000000-0000-0000-0000-000000000116",
            documents=[doc],
        )
    )
    assert out.success, f"extractor failed: {out.error}"
    result = out.extracted_documents[0]
    assert result.success, f"per-doc extract failed: {result.error}"

    rows = parse_extraction_records(
        [
            {
                "fields": [f.model_dump() for f in result.fields],
                "document_id": doc.document_id,
                "filename": doc.filename,
                "doc_type": "MARKET_STUDY",
                "extraction_id": "live",
            }
        ]
    )
    reading = read_class_forecast(rows)
    assert reading.reason is None, reading.detail
    assert LUU in reading.buckets, sorted(reading.buckets)
    series = reading.series(LUU)
    assert series.fallback_reason is None
    for year, (occ, adr) in LUU_EXPECTED.items():
        y = series.year(year)
        assert y is not None, f"{year} missing from the {LUU} series"
        assert y.occupancy_change_pct == pytest.approx(occ, abs=1e-6), year
        assert y.adr_change_pct == pytest.approx(adr, abs=1e-6), year
        assert y.period == "forecast", year
        assert y.page == 26, (year, y.page)
    assert series.year(2024) is not None and series.year(2024).period == "actual"
