"""FON-66 Slice B — deterministic Partnership/JV template extraction.

A structured partnership upload (ownership split + preferred return + promote
waterfall in a labeled grid) is parsed at $0 into the same canonical fields the
Partnership engine consumes — the Kimpton benchmark (GP 10 / LP 90, pref 10%,
tiers 10/15/20/25/30/>30 with 0/100 → 50/50 splits). Conservative contract:
anything that isn't clearly a waterfall returns None → LLM fallback.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from app.extraction.parser import parse_document
from app.extraction.template_extractors import (
    TemplateExtractResult,
    try_template_extract,
)
from app.services.engine_runner import _parse_partnership_override_path


def _by_name(result: TemplateExtractResult) -> dict[str, Any]:
    return {f["field_name"]: f["value"] for f in result.fields}


def _kimpton_partnership_xlsx() -> bytes:
    """A Kimpton-style partnership upload template (percentages as whole nums)."""
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Partnership Waterfall"
    rows = [
        ["Partnership Waterfall Terms"],
        [],
        ["Sponsor / GP Ownership", 10],
        ["LP Investor Ownership", 90],
        ["Preferred Return", 10],
        [],
        ["Promote Waterfall"],
        ["Tier", "IRR Hurdle", "GP Split", "LP Split"],
        ["Preferred", 10, 0, 100],
        ["Tier 2", 15, 20, 80],
        ["Tier 3", 20, 25, 75],
        ["Tier 4", 25, 25, 75],
        ["Tier 5", 30, 25, 75],
        ["Tier 6 (>30%)", 50, 50, 50],
    ]
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _plain_pnl_xlsx() -> bytes:
    """A workbook with no waterfall — must NOT match (falls through to LLM)."""
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.title = "P&L"
    for row in [["Line", "2024"], ["Rooms Revenue", 8_000_000], ["NOI", 1_300_000]]:
        sheet.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_extracts_kimpton_waterfall_and_ownership() -> None:
    parsed = await parse_document(
        file_bytes=_kimpton_partnership_xlsx(),
        filename="Kimpton_Fondok_Partnership_Upload_Template.xlsx",
    )
    result = try_template_extract(parsed, "PARTNERSHIP")
    assert result is not None
    assert result.template_name == "partnership"
    f = _by_name(result)

    # Ownership + preferred return (percent → fraction).
    assert f["partnership.gp_equity_pct"] == pytest.approx(0.10)
    assert f["partnership.lp_equity_pct"] == pytest.approx(0.90)
    assert f["partnership.pref_rate"] == pytest.approx(0.10)

    # Six promote tiers matching the Kimpton benchmark.
    assert f["partnership.waterfall.0.hurdle_rate"] == pytest.approx(0.10)
    assert f["partnership.waterfall.0.gp_split"] == pytest.approx(0.0)
    assert f["partnership.waterfall.0.lp_split"] == pytest.approx(1.0)
    assert f["partnership.waterfall.1.gp_split"] == pytest.approx(0.20)
    assert f["partnership.waterfall.1.lp_split"] == pytest.approx(0.80)
    assert f["partnership.waterfall.5.hurdle_rate"] == pytest.approx(0.50)
    assert f["partnership.waterfall.5.gp_split"] == pytest.approx(0.50)
    # 6 tiers × 3 fields.
    tier_fields = [k for k in f if k.startswith("partnership.waterfall.")]
    assert len(tier_fields) == 18


async def test_extracted_paths_parse_as_override_keys() -> None:
    # Every emitted waterfall field must be a valid override path so the
    # engine-runner loader can route it into partnership_waterfall_overrides.
    parsed = await parse_document(
        file_bytes=_kimpton_partnership_xlsx(), filename="p.xlsx"
    )
    result = try_template_extract(parsed, "PARTNERSHIP")
    assert result is not None
    for field in result.fields:
        name = field["field_name"]
        if name.startswith("partnership.waterfall."):
            assert _parse_partnership_override_path(name) is not None


async def test_wire_shape_contract() -> None:
    parsed = await parse_document(
        file_bytes=_kimpton_partnership_xlsx(), filename="p.xlsx"
    )
    result = try_template_extract(parsed, "PARTNERSHIP")
    assert result is not None
    for field in result.fields:
        assert set(field) >= {"field_name", "value", "confidence", "unit"}
        assert field["confidence"] == 1.0


async def test_wrong_doc_type_returns_none() -> None:
    parsed = await parse_document(
        file_bytes=_kimpton_partnership_xlsx(), filename="p.xlsx"
    )
    assert try_template_extract(parsed, "T12") is None


async def test_non_waterfall_workbook_returns_none() -> None:
    parsed = await parse_document(file_bytes=_plain_pnl_xlsx(), filename="pnl.xlsx")
    assert try_template_extract(parsed, "PARTNERSHIP") is None
