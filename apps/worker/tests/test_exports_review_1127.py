"""Release 2026-11-27 review — what a reader of the memo and the deck sees.

    3. Memo, Executive Summary: the entry-cap tile prints the Entry NOI it is
       struck on ("Entry NOI $5.31M") under the percent and its sub-line reads
       "<basis> ÷ price" like the T-12 tile ("T-12 NOI $4.18M" under 11.48%);
       a cap that fell back to the projected Year 1 says so instead of
       claiming the T-12-index basis.
    5. Deck: slide 3 (Hotel Facts) and slide 5 (Uses) print the deal's
       purchase price identically. They read two payload fields —
       ``investment_engine.purchase_price_usd`` and the ``uses`` Purchase
       Price line — which the live payload builds from the SAME capital
       engine row; the Kimpton export fixture carried two different numbers.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from fondok_schemas.reasons import REFUSAL_GLYPH  # noqa: E402

from app.export.labels import ENTRY_CAP_PROJECTED_SUB, ENTRY_NOI_BASIS  # noqa: E402
from tests.fixtures.kimpton_export_payload import kimpton_deal, kimpton_memo, kimpton_model  # noqa: E402


def _tiles(html_text: str) -> dict[str, str]:
    """Executive Summary metric tiles: label → the tile's visible text (label,
    definition, value, figure behind it), one space between its lines, tags
    stripped and entities decoded — what the reader sees."""
    out: dict[str, str] = {}
    for chunk in html_text.split('<div class="metric">')[1:]:
        parts = [
            html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
            for text in re.findall(r'<div class="[kvds]">(.*?)</div>', chunk, re.S)
        ]
        out[parts[0]] = " ".join(p for p in parts if p)
    return out


# ═══════════════ 3. the memo's two cap tiles ═══════════════


def test_memo_entry_cap_tile_prints_the_entry_noi_and_the_t12_tile_its_noi() -> None:
    from app.export.memo_pdf import ENTRY_CAP_DEFINITION, IN_PLACE_CAP_DEFINITION, _render_html

    tiles = _tiles(_render_html(kimpton_memo(), kimpton_model()))
    entry = tiles["Entry Cap (Year 1, undisplaced)"]
    assert entry == f"Entry Cap (Year 1, undisplaced) {ENTRY_CAP_DEFINITION} 14.58% Entry NOI $5.31M"
    assert ENTRY_CAP_DEFINITION == f"{ENTRY_NOI_BASIS} ÷ price"
    in_place = tiles["In-place Cap (T-12)"]
    assert in_place == f"In-place Cap (T-12) {IN_PLACE_CAP_DEFINITION} 11.48% T-12 NOI $4.18M"
    assert IN_PLACE_CAP_DEFINITION == "T-12 NOI before reserve ÷ price"
    # Both sub-lines share the "<numerator> ÷ price" shape.
    assert ENTRY_CAP_DEFINITION.endswith(" ÷ price") and IN_PLACE_CAP_DEFINITION.endswith(" ÷ price")


def test_memo_cap_tiles_dash_the_noi_the_payload_lacks_and_never_derive_it() -> None:
    from app.export.memo_pdf import _render_html

    model = kimpton_model()
    inv = model["investment_engine"]
    inv.pop("t12_noi_before_reserve_usd")
    inv.pop("entry_cap_rate_t12")
    inv.pop("entry_noi_usd")  # a pre-upgrade worker: the cap without its numerator
    tiles = _tiles(_render_html(kimpton_memo(), model))
    assert tiles["In-place Cap (T-12)"].endswith(f"{REFUSAL_GLYPH} T-12 NOI {REFUSAL_GLYPH}")
    assert tiles["Entry Cap (Year 1, undisplaced)"].endswith(f"14.58% Entry NOI {REFUSAL_GLYPH}")
    assert "$4.18M" not in tiles["In-place Cap (T-12)"]


def test_memo_entry_cap_on_a_projected_year1_fallback_does_not_claim_the_t12_basis() -> None:
    """``entry_noi_basis == "projected_year1"`` (an open hotel's run that
    predates FON-122): the sub-line says what the cap IS on and asks for a
    re-run; the basis string appears nowhere on the tile."""
    from app.export.memo_pdf import ENTRY_CAP_DEFINITION, _render_html

    model = kimpton_model()
    model["investment_engine"]["entry_noi_basis"] = "projected_year1"
    tiles = _tiles(_render_html(kimpton_memo(), model))
    entry = tiles["Entry Cap (Year 1, undisplaced)"]
    assert entry == f"Entry Cap (Year 1, undisplaced) {ENTRY_CAP_PROJECTED_SUB} 14.58% Entry NOI $5.31M"
    assert ENTRY_CAP_PROJECTED_SUB == "Projected Year 1 NOI ÷ price · re-run the model for the entry basis"
    assert ENTRY_NOI_BASIS not in entry
    # The engine's own entry NOI, and a payload without the marker, keep the basis.
    model["investment_engine"]["entry_noi_basis"] = "run_rate"
    assert ENTRY_CAP_DEFINITION in _tiles(_render_html(kimpton_memo(), model))["Entry Cap (Year 1, undisplaced)"]
    del model["investment_engine"]["entry_noi_basis"]
    assert ENTRY_CAP_DEFINITION in _tiles(_render_html(kimpton_memo(), model))["Entry Cap (Year 1, undisplaced)"]


def test_live_payload_memo_prints_the_projected_wording_off_a_real_fallback_block() -> None:
    """End to end on the payload side: ``_build_investment`` with no
    ``run_rate_noi`` marks the block, the memo reads the mark."""
    from app.engines.capital import CapitalEngine, CapitalEngineInput
    from app.export.live_payload import _build_investment
    from app.export.memo_pdf import _render_html

    cap = CapitalEngine().run(CapitalEngineInput(deal_id=uuid4(), purchase_price=36_400_000, keys=132))
    model = kimpton_model()
    model["investment_engine"] = _build_investment(cap.model_dump(mode="json"), 132, 1_646_245.0)
    assert model["investment_engine"]["entry_noi_basis"] == "projected_year1"
    entry = _tiles(_render_html(kimpton_memo(), model))["Entry Cap (Year 1, undisplaced)"]
    assert ENTRY_CAP_PROJECTED_SUB in entry and "4.52%" in entry and "Entry NOI $1.65M" in entry
    assert ENTRY_NOI_BASIS not in entry


# ═══════════════ 5. the deck's purchase price, once ═══════════════


def _slides(path: Path) -> list[dict[str, object]]:
    """Per slide: the free text (one string) and the table rows (cell texts)."""
    from pptx import Presentation

    out: list[dict[str, object]] = []
    for slide in Presentation(str(path)).slides:
        texts: list[str] = []
        rows: list[list[str]] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    rows.append([c.text for c in row.cells])
        out.append({"text": "\n".join(texts), "rows": rows})
    return out


def test_deck_slide_3_and_slide_5_print_the_same_purchase_price(tmp_path: Path) -> None:
    from app.export import build_pptx

    model = kimpton_model()
    slides = _slides(build_pptx(kimpton_deal(), model, kimpton_memo(), tmp_path / "deck.pptx"))
    facts = next(s for s in slides if "Hotel Facts" in str(s["text"]))
    su = next(s for s in slides if any(r and r[0] == "Total Uses" for r in s["rows"]))  # type: ignore[union-attr]
    assert slides.index(facts) == 2 and slides.index(su) == 4  # slide 3, slide 5

    price = model["investment_engine"]["purchase_price_usd"]
    want = f"${price:,.0f}"
    assert want == "$36,400,000"
    assert want in str(facts["text"])
    uses_row = next(r for r in su["rows"] if r[0] == "Purchase Price")  # type: ignore[union-attr]
    assert uses_row[1] == want
    # Nowhere in the deck does the stale $36,436,802 survive, and the stack foots.
    blob = "\n".join(str(s["text"]) + "\n" + "\n".join(" | ".join(r) for r in s["rows"]) for s in slides)  # type: ignore[union-attr]
    assert "36,436,802" not in blob
    total_uses = next(r for r in su["rows"] if r[0] == "Total Uses")[1]  # type: ignore[union-attr]
    total_sources = next(r for r in su["rows"] if r[0] == "Total Sources")[1]  # type: ignore[union-attr]
    assert total_uses == total_sources == f"${model['investment_engine']['total_capital_usd']:,.0f}"


def test_fixture_uses_foot_to_its_sources_and_total_capital() -> None:
    model = kimpton_model()
    uses = sum(u["amount"] for u in model["uses"])
    sources = sum(s["amount"] for s in model["sources"])
    assert uses == sources == model["investment_engine"]["total_capital_usd"]
    assert next(u for u in model["uses"] if u["label"] == "Purchase Price")["amount"] == model["investment_engine"]["purchase_price_usd"]


def test_live_payload_reads_both_purchase_prices_off_the_same_capital_row() -> None:
    """The live shape: ``investment_engine.purchase_price_usd`` and the
    ``uses`` Purchase Price line are the one ``capital.uses`` row, so the two
    slides cannot disagree on a real run."""
    from app.engines.capital import CapitalEngine, CapitalEngineInput
    from app.export.live_payload import _build_investment, _build_sources_uses

    cap = CapitalEngine().run(
        CapitalEngineInput(deal_id=uuid4(), purchase_price=36_400_000, keys=132, closing_costs_pct=0.02,
                           renovation_budget=5_280_000, working_capital=500_000, ltv=0.65)
    ).model_dump(mode="json")
    inv = _build_investment(cap, 132, None)
    _sources, uses = _build_sources_uses(cap)
    assert next(u for u in uses if u["label"] == "Purchase Price")["amount"] == inv["purchase_price_usd"] == 36_400_000
    assert sum(u["amount"] for u in uses) == pytest.approx(inv["total_capital_usd"])
