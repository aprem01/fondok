"""Sibling-template percent↔ratio scale regression (wave5-siblingfix).

Confirms the percent-scale bug fix: an occupancy grid cell rendered as
``74%`` (parsed to ``74.0``) must learn/apply against the extractor's
0..1 ratio value (``0.74``) — not record ``nomatch`` and not emit the
100× value ``74.0`` on the sibling.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

# Force SQLite + a temp DB before app imports (same pattern as the
# sibling reuse suite) — keeps tests isolated from any local fondok.db.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-sibling-percent.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

from app.services.sibling_template import (  # noqa: E402
    apply_mapping,
    learn_mapping,
)


def _page(
    grid: list[list[str]],
    *,
    sheet_name: str = "P&L",
    page_num: int = 1,
) -> dict[str, Any]:
    return {
        "page_num": page_num,
        "text": "\n".join("\t".join(r) for r in grid),
        "tables": [grid],
        "metadata": {
            "source": "xls",
            "sheet_name": sheet_name,
            "sheet_state": "visible",
        },
    }


def _source_grid() -> list[list[str]]:
    """Occupancy rendered as a PERCENT string ('74%' → 74.0)."""
    return [
        ["Anglers Hotel", "", "", "", ""],
        ["", "Jan", "Feb", "Mar", "Total"],
        ["Revenues", "", "", "", ""],
        ["Rooms", "101000", "102000", "103000", "306000"],
        ["Food & Beverage", "51000", "52000", "53000", "156000"],
        ["Statistics", "", "", "", ""],
        ["Occupancy", "72%", "74%", "76%", "74%"],
        ["ADR", "241.5", "243.5", "245.5", "243.5"],
    ]


def _source_fields() -> list[dict[str, Any]]:
    """Extractor emits occupancy as a 0..1 ratio (schema ge=0, le=1)."""
    return [
        {"field_name": "p_and_l_usali.revenues.rooms_usd", "value": 306000},
        {"field_name": "p_and_l_usali.revenues.fb_usd", "value": 156000},
        {"field_name": "ttm_summary_per_om.occupancy_pct", "value": 0.74},
        {"field_name": "ttm_summary_per_om.adr_usd", "value": 243.5},
    ]


def _sibling_grid() -> list[list[str]]:
    """Same template, next year: occupancy 71% (percent string)."""
    return [
        ["Anglers Hotel", "", "", "", ""],
        ["", "Jan", "Feb", "Mar", "Total"],
        ["Revenues", "", "", "", ""],
        ["Rooms", "111000", "112000", "113000", "336000"],
        ["Food & Beverage", "61000", "62000", "63000", "186000"],
        ["Statistics", "", "", "", ""],
        ["Occupancy", "69%", "71%", "73%", "71%"],
        ["ADR", "251.5", "253.5", "255.5", "253.5"],
    ]


def test_percent_occupancy_learns_not_nomatch():
    entries, stats = learn_mapping([_page(_source_grid())], _source_fields())

    assert stats["nomatch"] == 0, stats
    assert "ttm_summary_per_om.occupancy_pct" in entries, entries
    occ = entries["ttm_summary_per_om.occupancy_pct"]
    # 0.74 (ratio) matched a 74.0 (percent) grid cell → scale 0.01.
    assert occ["scale"] == 0.01, occ
    # dollar fields must still match at unit scale — no regression.
    assert entries["p_and_l_usali.revenues.rooms_usd"]["scale"] == 1.0
    assert entries["ttm_summary_per_om.adr_usd"]["scale"] == 1.0


def test_percent_occupancy_applies_as_ratio_not_100x():
    entries, _ = learn_mapping([_page(_source_grid())], _source_fields())
    fields, apply_stats = apply_mapping([_page(_sibling_grid())], entries)

    by_name = {f["field_name"]: f["value"] for f in fields}
    assert "ttm_summary_per_om.occupancy_pct" in by_name, by_name
    # Sibling occupancy 71% must emit 0.71, NOT 71.0 (the 100× bug).
    assert by_name["ttm_summary_per_om.occupancy_pct"] == 0.71, by_name
    # Non-percent fields unaffected.
    assert by_name["p_and_l_usali.revenues.rooms_usd"] == 336000
    assert by_name["ttm_summary_per_om.adr_usd"] == 253.5


def test_ratio_guard_divides_stray_percent():
    """If a mapping ever stores scale 1.0 for a ratio field pointing at a
    percent cell (legacy mapping), the apply-time guard still corrects it."""
    entries = {
        "ttm_summary_per_om.occupancy_pct": {
            "keys": [["p&l", "statistics", "occupancy", "total"]],
            "scale": 1.0,  # legacy: no scale learned
            "unit": None,
        }
    }
    fields, _ = apply_mapping([_page(_sibling_grid())], entries)
    by_name = {f["field_name"]: f["value"] for f in fields}
    assert by_name["ttm_summary_per_om.occupancy_pct"] == 0.71, by_name
