"""FON-61 / FON-41 E-028 — Index Analysis methodology + editable assumptions.

Pinned here (pure — no DB, no LLM):

* the default method is today's STR comp set, and it resolves from the STR
  report's subject TTM ÷ MPI / ARI with the rows it read;
* the market / chain-scale benchmark reads ``ttm_performance.segment.*`` and
  ``pnl_benchmark.market.*`` with document + page provenance, choosing the
  segment by the analyst's pick → the deal's service level → the market-wide
  figure;
* the CoStar Property Analytics comp set is DISABLED, with the reason, when no
  such extraction exists — never filled from another source;
* each assumption shows its document source, a computed basis, or "your
  override" (with the analyst's note) — and ``None`` with a reason otherwise;
* (topline v2, 2026-11-02) this module is the Index Analysis PANEL's reader;
  the model runs on ``services.topline`` — see ``test_topline_resolvers.py``,
  ``test_topline_loader.py`` and ``test_engine_runner.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.services.index_methodology import (
    KEY_ADR_GROWTH,
    KEY_ARI_TARGET,
    KEY_MPI_TARGET,
    KEY_OCC_GROWTH,
    METHOD_COSTAR_COMP_SET,
    METHOD_MARKET_BENCHMARK,
    METHOD_STR_COMP_SET,
    SOURCE_INDEX_ASSUMPTION,
    read_costar_comp_set,
    read_market_benchmark,
    resolve_index_methodology,
    rows_from_records,
)
from app.services.market_fields import FieldRow

STR_DOC = "Subject STR Trend May 2025.xlsx"
MARKET_DOC = "Miami Beach-Hospitality-Submarket-2025-12-10.pdf"
COSTAR_DOC = "CoStar Property Analytics Comp Set.xlsx"


def _row(name: str, value: Any, *, doc: str, doc_type: str, page: int | None = 2, period: str | None = None) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=None, page=page, doc_name=doc,
        doc_id=f"doc-{doc_type.lower()}", extraction_id=f"ext-{doc_type.lower()}",
        doc_type=doc_type, period=period,
    )


STR_ROWS = [
    _row("ttm_performance.subject.occupancy_pct", 84.6, doc=STR_DOC, doc_type="STR_TREND", page=3),
    _row("ttm_performance.subject.adr_usd", 255.0, doc=STR_DOC, doc_type="STR_TREND", page=3),
    _row("ttm_performance.indices.mpi_occupancy_index", 110.0, doc=STR_DOC, doc_type="STR_TREND", page=4),
    _row("ttm_performance.indices.ari_adr_index", 102.0, doc=STR_DOC, doc_type="STR_TREND", page=4),
]
MARKET_ROWS = [
    _row("pnl_benchmark.market.occupancy_pct", 0.712, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=5),
    _row("pnl_benchmark.market.adr_usd", 231.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=5),
    _row("ttm_performance.segment.luxury.occupancy_pct", 68.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=7),
    _row("ttm_performance.segment.luxury.adr_usd", 480.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=7),
    _row("ttm_performance.segment.upscale.occupancy_pct", 74.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=7),
    _row("ttm_performance.segment.upscale.adr_usd", 210.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=7),
    # Unrelated pipeline row under the same namespace — never read as a level.
    _row("ttm_performance.segment.luxury.under_construction", 726, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=4),
    _row("pnl_benchmark.market.adr_change_2026_forecast", 0.031, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
    _row("pnl_benchmark.market.adr_change_2027_forecast", 0.028, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
    _row("pnl_benchmark.market.occupancy_change_2026_forecast", 0.005, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
]


# ─────────────────────────── selection + STR comp set ───────────────────────────


def test_default_method_is_the_str_comp_set_and_resolves_with_provenance() -> None:
    r = resolve_index_methodology([*STR_ROWS, *MARKET_ROWS], {})
    assert r.selected == METHOD_STR_COMP_SET
    assert r.selected_source == "default"
    assert r.toggle_on is False
    m = r.method(METHOD_STR_COMP_SET)
    assert m is not None and m.available
    # comp = subject ÷ index
    assert m.occupancy.value == pytest.approx(0.846 / 1.10, abs=1e-4)
    assert m.adr.value == pytest.approx(255.0 / 1.02, abs=1e-3)
    assert m.occupancy.source == "computed"
    occ_inputs = {ref.field_name: ref for ref in m.occupancy.inputs}
    assert occ_inputs["ttm_performance.subject.occupancy_pct"].doc_name == STR_DOC
    assert occ_inputs["ttm_performance.subject.occupancy_pct"].page == 3
    assert occ_inputs["ttm_performance.indices.mpi_occupancy_index"].page == 4
    # The subject's STR TTM rides along for the reconciliation line.
    assert r.subject_occupancy.value == pytest.approx(0.846)
    assert r.subject_adr.value == pytest.approx(255.0)
    # MPI / ARI defaults are the STR-published indices (a document, not a calc).
    mpi = r.assumptions[KEY_MPI_TARGET]
    assert mpi.source == "document" and mpi.value == pytest.approx(110.0)
    assert mpi.inputs[0].field_name == "ttm_performance.indices.mpi_occupancy_index"


def test_an_unknown_method_override_falls_back_to_the_default() -> None:
    r = resolve_index_methodology(STR_ROWS, {"index_methodology": {"value": "made_up"}})
    assert r.selected == METHOD_STR_COMP_SET and r.selected_source == "default"


def test_the_saved_method_is_read_from_the_override() -> None:
    r = resolve_index_methodology(
        [*STR_ROWS, *MARKET_ROWS], {"index_methodology": {"value": METHOD_MARKET_BENCHMARK}}
    )
    assert r.selected == METHOD_MARKET_BENCHMARK and r.selected_source == "override"


def test_str_comp_set_unavailable_without_an_str_report() -> None:
    r = resolve_index_methodology(MARKET_ROWS, {})
    m = r.method(METHOD_STR_COMP_SET)
    assert m is not None and not m.available
    assert "STR Trend" in (m.disabled_reason or "")
    assert m.occupancy.value is None


# ─────────────────────────── market / chain-scale benchmark ───────────────────────────


def test_market_benchmark_reads_the_market_wide_figure_with_document_and_page() -> None:
    m = read_market_benchmark(MARKET_ROWS)
    assert m.available and m.segment is None
    assert m.occupancy.value == pytest.approx(0.712)
    assert m.adr.value == pytest.approx(231.0)
    assert m.occupancy.source == "document"
    assert m.occupancy.inputs[0].field_name == "pnl_benchmark.market.occupancy_pct"
    assert m.occupancy.inputs[0].doc_name == MARKET_DOC and m.occupancy.inputs[0].page == 5
    assert m.segments_available == ["luxury", "upscale"]


def test_market_benchmark_follows_the_deals_service_level_then_the_analysts_pick() -> None:
    lux = read_market_benchmark(MARKET_ROWS, service_hint="Luxury")
    assert lux.segment == "luxury"
    assert lux.occupancy.value == pytest.approx(0.68) and lux.adr.value == pytest.approx(480.0)
    assert lux.adr.inputs[0].field_name == "ttm_performance.segment.luxury.adr_usd"
    picked = read_market_benchmark(MARKET_ROWS, segment="upscale", service_hint="Luxury")
    assert picked.segment == "upscale" and picked.adr.value == pytest.approx(210.0)


def test_market_benchmark_with_only_segments_and_no_hint_names_them() -> None:
    seg_only = [r for r in MARKET_ROWS if r.field_name.startswith("ttm_performance.segment.")]
    m = read_market_benchmark(seg_only)
    assert not m.available
    assert "luxury" in (m.disabled_reason or "") and "upscale" in (m.disabled_reason or "")


def test_market_benchmark_unavailable_without_market_rows() -> None:
    m = read_market_benchmark(STR_ROWS)
    assert not m.available
    assert "market reports" in (m.disabled_reason or "") and "ttm_performance" not in (m.disabled_reason or "")


def test_market_benchmark_penetration_default_is_computed_from_both_documents() -> None:
    r = resolve_index_methodology(
        [*STR_ROWS, *MARKET_ROWS], {"index_methodology": METHOD_MARKET_BENCHMARK}
    )
    mpi = r.assumptions[KEY_MPI_TARGET]
    assert mpi.source == "computed"
    assert mpi.value == pytest.approx(0.846 / 0.712 * 100, abs=0.01)
    docs = {ref.doc_name for ref in mpi.inputs}
    assert docs == {STR_DOC, MARKET_DOC}


# ─────────────────────────── CoStar Property Analytics ───────────────────────────


def test_costar_comp_set_is_disabled_with_the_reason_when_absent() -> None:
    m = read_costar_comp_set([*STR_ROWS, *MARKET_ROWS])
    assert m.method == METHOD_COSTAR_COMP_SET
    assert not m.available
    assert "No CoStar Property Analytics comp-set extraction" in (m.disabled_reason or "")
    assert m.occupancy.value is None and m.adr.value is None


def test_costar_comp_set_resolves_when_the_extraction_exists() -> None:
    rows = [
        _row("costar_comp_set.occupancy_pct", 76.2, doc=COSTAR_DOC, doc_type="COSTAR", page=1),
        _row("costar_comp_set.adr_usd", 248.0, doc=COSTAR_DOC, doc_type="COSTAR", page=1),
    ]
    m = read_costar_comp_set(rows)
    assert m.available
    assert m.occupancy.value == pytest.approx(0.762)
    assert m.adr.inputs[0].doc_name == COSTAR_DOC
    assert m.documents == [COSTAR_DOC]


def test_costar_extraction_without_both_figures_says_so() -> None:
    rows = [_row("costar_comp_set.adr_usd", 248.0, doc=COSTAR_DOC, doc_type="COSTAR")]
    m = read_costar_comp_set(rows)
    assert not m.available
    assert COSTAR_DOC in (m.disabled_reason or "")


# ─────────────────────────── assumptions ───────────────────────────


def test_growth_defaults_name_the_document_or_say_there_is_none() -> None:
    r = resolve_index_methodology([*STR_ROWS, *MARKET_ROWS], {})
    adr_g = r.assumptions[KEY_ADR_GROWTH]
    assert adr_g.source == "document"
    assert adr_g.value == pytest.approx(0.031)  # the EARLIEST forecast year
    assert adr_g.period_label == "2026 forecast"
    assert adr_g.inputs[0].doc_name == MARKET_DOC and adr_g.inputs[0].page == 6
    assert r.assumptions[KEY_OCC_GROWTH].value == pytest.approx(0.005)

    bare = resolve_index_methodology(STR_ROWS, {})
    assert bare.assumptions[KEY_ADR_GROWTH].value is None
    assert bare.assumptions[KEY_ADR_GROWTH].source is None
    assert "enter your own" in (bare.assumptions[KEY_ADR_GROWTH].detail or "")


def test_an_override_reads_as_your_override_with_its_note() -> None:
    r = resolve_index_methodology(
        STR_ROWS,
        {
            KEY_OCC_GROWTH: {"value": 0.01, "note": "Submarket recovery"},
            KEY_ARI_TARGET: {"value": 105, "note": "Post-PIP repositioning"},
        },
    )
    occ_g = r.assumptions[KEY_OCC_GROWTH]
    assert occ_g.source == "override" and occ_g.value == pytest.approx(0.01)
    assert occ_g.detail == "Submarket recovery"
    assert r.assumptions[KEY_ARI_TARGET].value == pytest.approx(105)


def test_lane_tagging_routes_an_str_tagged_market_study_to_the_market_lane() -> None:
    """A CoStar market report tagged STR_TREND but extracted in the
    MARKET_STUDY lane must not feed the STR comp-set blend."""
    import json

    recs = [
        {
            "extraction_id": str(uuid4()), "document_id": str(uuid4()), "filename": MARKET_DOC,
            "doc_type": "STR_TREND", "agent_version": "router:x;dt:MARKET_STUDY;extractor",
            "fields": json.dumps([{"field_name": "ttm_performance.subject.occupancy_pct", "value": 50.0}]),
        }
    ]
    rows = rows_from_records(recs)
    assert rows[0].doc_type == "MARKET_STUDY"
    r = resolve_index_methodology(rows, {})
    assert r.subject_occupancy.value is None


# ─────────────────────────── the engine seam ───────────────────────────


# ─────────────────────────── endpoint shape ───────────────────────────


def test_endpoint_serializes_every_method_and_assumption() -> None:
    from app.api.market import index_methodology_response

    reading = resolve_index_methodology([*STR_ROWS, *MARKET_ROWS], {})
    deal_id = uuid4()
    out = index_methodology_response(deal_id, reading).model_dump(mode="json")
    assert out["selected"] == METHOD_STR_COMP_SET
    assert [m["method"] for m in out["methods"]] == [
        METHOD_STR_COMP_SET, METHOD_MARKET_BENCHMARK, METHOD_COSTAR_COMP_SET,
    ]
    costar = out["methods"][2]
    assert costar["available"] is False and costar["disabled_reason"]
    assert set(out["assumptions"]) == {KEY_OCC_GROWTH, KEY_ADR_GROWTH, KEY_MPI_TARGET, KEY_ARI_TARGET}
    assert out["assumptions"][KEY_ADR_GROWTH]["inputs"][0]["page"] == 6
    assert out["subject_period_label"]


def test_index_assumption_source_is_in_the_ontology_registry() -> None:
    from app.ontology.registry import get_registry

    assert SOURCE_INDEX_ASSUMPTION in set(get_registry().sources)


def test_method_choice_keys_are_note_exempt_and_values_are_not() -> None:
    from app.api.deals import _NOTE_EXEMPT_KEYS, _override_needs_note

    # Review I-6 — the retired method key is read as a forced benchmark level
    # (it moves numbers): it needs a note. The segment choice moves nothing.
    assert "index_methodology" not in _NOTE_EXEMPT_KEYS
    assert _override_needs_note("index_methodology", frozenset()) is True
    assert "index_market_segment" in _NOTE_EXEMPT_KEYS
    for key in (KEY_OCC_GROWTH, KEY_ADR_GROWTH, KEY_MPI_TARGET, KEY_ARI_TARGET):
        assert _override_needs_note(key, frozenset()) is True
