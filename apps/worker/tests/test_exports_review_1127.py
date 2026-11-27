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

from app.export.labels import (  # noqa: E402
    ENTRY_CAP_LABEL,
    ENTRY_CAP_PROJECTED_LABEL,
    ENTRY_CAP_PROJECTED_SUB,
    ENTRY_NOI_BASIS,
    ENTRY_NOI_PROJECTED_LABEL,
)
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
    # No twelve-month statement: the cap cell is the (one) dash, the figure line the plain reason.
    assert tiles["In-place Cap (T-12)"].endswith(f"{REFUSAL_GLYPH} no twelve-month statement")
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
    # Nit 4 (review 2026-11-27): the tile and its figure are LABELLED projected —
    # "undisplaced" is never said about the bare projected Year 1.
    assert "Entry Cap (Year 1, undisplaced)" not in tiles
    entry = tiles[ENTRY_CAP_PROJECTED_LABEL]
    assert entry == (
        f"{ENTRY_CAP_PROJECTED_LABEL} {ENTRY_CAP_PROJECTED_SUB} 14.58% {ENTRY_NOI_PROJECTED_LABEL} $5.31M"
    )
    assert ENTRY_CAP_PROJECTED_LABEL == "Entry Cap (projected Year 1)"
    assert ENTRY_NOI_PROJECTED_LABEL == "Entry NOI (projected Year 1)"
    assert ENTRY_CAP_PROJECTED_SUB == "Projected Year 1 NOI ÷ price · re-run the model for the entry basis"
    assert ENTRY_NOI_BASIS not in entry and "undisplaced" not in entry
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
    tiles = _tiles(_render_html(kimpton_memo(), model))
    assert ENTRY_CAP_LABEL not in tiles
    entry = tiles[ENTRY_CAP_PROJECTED_LABEL]
    assert ENTRY_CAP_PROJECTED_SUB in entry and "4.52%" in entry and f"{ENTRY_NOI_PROJECTED_LABEL} $1.65M" in entry
    assert ENTRY_NOI_BASIS not in entry and "undisplaced" not in entry


# ═══════════════ review round 2 — refusals the cap tiles record ═══════════════
#
# Finding 1: a figure absent BY DESIGN (a closed hotel not yet open; a deal
# with no twelve-month statement) is not a refusal — the tile says why, once,
# and the footnote lists nothing for it. Where a refusal IS right (a payload
# that carries the cap but not its numerator) it carries the cap cell's code
# (``no_source``), never ``engine_skipped``.
# Finding 2: a refused cap (out-of-date T-12 / not yet open) shows no basis —
# not "<basis> ÷ price", not "Entry NOI $X" — it says "See Yield on Cost".


def _tiles_and_refusals(model: dict) -> tuple[dict[str, str], list[tuple[str, str | None, str | None]]]:
    """The Executive Summary tiles and every refusal the two cap tiles recorded
    (``(code, concept, detail)``) during the body render."""
    from app.export.memo_pdf import _render_html_body
    from app.export.refusals import collect_refusals

    with collect_refusals() as log:
        body = _render_html_body(kimpton_memo(), model)
    cap = [
        (r.code.value, r.concept, r.detail)
        for r in log
        if r.concept in ("entry_noi", "t12_noi", "entry_cap_rate")
    ]
    return _tiles(body), cap


def test_memo_not_yet_open_entry_tile_says_why_and_records_no_refusal() -> None:
    from app.export.memo_pdf import ENTRY_CAP_NOT_YET_OPEN_DEFINITION, ENTRY_CAP_REFUSED_SUB, _render_html

    model = kimpton_model()
    inv = model["investment_engine"]
    inv.pop("entry_cap_rate_year1_uw")
    inv.pop("entry_noi_usd")
    inv["entry_cap_not_yet_open"] = True  # the live builder's shape: closed, no entry NOI
    tiles, refusals = _tiles_and_refusals(model)
    entry = tiles[ENTRY_CAP_LABEL]
    assert entry == f"{ENTRY_CAP_LABEL} {ENTRY_CAP_NOT_YET_OPEN_DEFINITION} n/a, not yet open {ENTRY_CAP_REFUSED_SUB}"
    assert ENTRY_CAP_NOT_YET_OPEN_DEFINITION == "Closed at purchase: no entry NOI to quote a cap on"
    assert ENTRY_CAP_REFUSED_SUB == "See Yield on Cost"
    assert REFUSAL_GLYPH not in entry and "÷ price" not in entry and ENTRY_NOI_BASIS not in entry
    assert refusals == [], "an absence by design is not a refusal"
    # The T-12 tile beside it is untouched, and the memo earns no footnote line for the pair.
    assert tiles["In-place Cap (T-12)"].endswith("11.48% T-12 NOI $4.18M")
    assert "engine_skipped" not in _render_html(kimpton_memo(), model)


def test_memo_out_of_date_t12_entry_tile_prints_the_reason_not_the_basis() -> None:
    """Finding 2: the live builder keeps ``entry_noi_usd`` when the T-12 is out
    of date — the tile used to print "n/a, T-12 ends …" over "<basis> ÷ price"
    and "Entry NOI $5.31M". A refused cap shows no basis and no figure."""
    from app.export.memo_pdf import ENTRY_CAP_REFUSED_SUB, ENTRY_CAP_T12_OUT_OF_DATE_DEFINITION, _entry_cap_definition

    model = kimpton_model()
    inv = model["investment_engine"]
    inv.pop("entry_cap_rate_year1_uw")
    inv["entry_cap_t12_out_of_date"] = "T-12 ends Mar 2025, 14 months before close"
    assert inv["entry_noi_usd"] == 5_308_245  # still on the payload, as live
    tiles, refusals = _tiles_and_refusals(model)
    entry = tiles[ENTRY_CAP_LABEL]
    assert entry == (
        f"{ENTRY_CAP_LABEL} {ENTRY_CAP_T12_OUT_OF_DATE_DEFINITION} "
        f"n/a, T-12 ends Mar 2025, 14 months before close {ENTRY_CAP_REFUSED_SUB}"
    )
    assert "$5.31M" not in entry and "÷ price" not in entry and ENTRY_NOI_BASIS not in entry
    assert REFUSAL_GLYPH not in entry
    assert refusals == []
    assert _entry_cap_definition(inv) == ENTRY_CAP_T12_OUT_OF_DATE_DEFINITION
    # The projected marker never outranks the refusal.
    inv["entry_noi_basis"] = "projected_year1"
    assert _entry_cap_definition(inv) == ENTRY_CAP_T12_OUT_OF_DATE_DEFINITION


def test_memo_no_twelve_month_statement_records_one_no_source_refusal() -> None:
    from app.export.memo_pdf import IN_PLACE_CAP_DEFINITION, IN_PLACE_CAP_NO_T12

    model = kimpton_model()
    inv = model["investment_engine"]
    inv.pop("t12_noi_before_reserve_usd")
    inv.pop("entry_cap_rate_t12")
    tiles, refusals = _tiles_and_refusals(model)
    assert tiles["In-place Cap (T-12)"] == (
        f"In-place Cap (T-12) {IN_PLACE_CAP_DEFINITION} {REFUSAL_GLYPH} no twelve-month statement"
    )
    # ONE refusal, the cap cell's, with the cap cell's code — not a second
    # ``engine_skipped`` beside it for the figure line.
    assert refusals == [("no_source", "entry_cap_rate", IN_PLACE_CAP_NO_T12)]
    assert IN_PLACE_CAP_NO_T12 == "no T-12 NOI before reserve on the basis statement"
    # The entry tile is untouched.
    assert tiles[ENTRY_CAP_LABEL].endswith("14.58% Entry NOI $5.31M")


def test_memo_normal_cap_tiles_record_no_refusal() -> None:
    tiles, refusals = _tiles_and_refusals(kimpton_model())
    assert refusals == []
    assert tiles[ENTRY_CAP_LABEL].endswith("14.58% Entry NOI $5.31M")
    assert tiles["In-place Cap (T-12)"].endswith("11.48% T-12 NOI $4.18M")


def test_memo_cap_without_its_numerator_is_a_no_source_dash_never_engine_skipped() -> None:
    """A pre-upgrade payload: the cap is there, its NOI is not, and nothing
    explains the gap — that IS a refusal, with the cap cell's code."""
    model = kimpton_model()
    model["investment_engine"].pop("entry_noi_usd")
    tiles, refusals = _tiles_and_refusals(model)
    assert tiles[ENTRY_CAP_LABEL].endswith(f"14.58% Entry NOI {REFUSAL_GLYPH}")
    assert refusals == [("no_source", "entry_noi", "no entry NOI on the export payload")]
    assert all(code != "engine_skipped" for code, _, _ in refusals)


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


#: Every ``investment_engine`` component the live builder reads off a Sources &
#: Uses line (``_build_investment``); their sum IS ``total_capital_usd``.
_INVESTMENT_USE_COMPONENTS = (
    "purchase_price_usd",
    "closing_costs_usd",
    "renovation_budget_usd",
    "soft_costs_usd",
    "contingency_usd",
    "working_capital_usd",
    "loan_costs_usd",
)


def test_fixture_uses_foot_to_its_sources_and_total_capital() -> None:
    model = kimpton_model()
    inv = model["investment_engine"]
    uses = sum(u["amount"] for u in model["uses"])
    sources = sum(s["amount"] for s in model["sources"])
    assert uses == sources == inv["total_capital_usd"]
    assert next(u for u in model["uses"] if u["label"] == "Purchase Price")["amount"] == inv["purchase_price_usd"]
    # Review 2026-11-27 (nit 5): the investment block foots to ITSELF — the
    # components the live builder sets from uses lines sum to the total. A
    # ``contingency_usd`` with no Contingency uses line over-footed it by $528,000.
    assert sum(inv.get(k) or 0 for k in _INVESTMENT_USE_COMPONENTS) == inv["total_capital_usd"]
    # And a component is on the block only when its uses line is (the live rule).
    labels = [u["label"].lower() for u in model["uses"]]
    for key, needle in (("soft_costs_usd", "soft cost"), ("contingency_usd", "contingency")):
        assert (key in inv) == any(needle in label for label in labels), key


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
