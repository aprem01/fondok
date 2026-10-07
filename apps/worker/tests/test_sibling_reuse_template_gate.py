"""Sibling cell-mapping reuse never pre-empts a deterministic template.

2026-10-07: two STR monthly trend workbooks were served by ``template:sibling``
(placeholder hotel names, no TTM block) instead of ``template:str_trend``.
"""

from __future__ import annotations

import inspect

import pytest

from app.api import documents as docs_module


@pytest.mark.parametrize(
    "dt", ["STR", "STR_TREND", "STR_SEGMENTATION", "CBRE_HORIZONS", "PARTNERSHIP", " str_trend "]
)
def test_template_handled_types_are_never_sibling_candidates(dt: str) -> None:
    assert docs_module._sibling_reuse_allowed(dt) is False


@pytest.mark.parametrize("dt", ["T12", "PNL", "PNL_MONTHLY", "PNL_YTD", "OM", "CAPEX", None, ""])
def test_other_types_may_use_sibling_reuse(dt: str | None) -> None:
    assert docs_module._sibling_reuse_allowed(dt) is True


def test_gate_covers_every_type_the_template_dispatcher_handles() -> None:
    """The dispatcher and the gate must not drift apart."""
    from app.extraction import template_extractors as te

    src = inspect.getsource(te.try_template_extract)
    dispatched = {
        tok
        for tok in ("STR", "STR_TREND", "CBRE_HORIZONS", "PARTNERSHIP", "STR_SEGMENTATION")
        if f'"{tok}"' in src
    }
    assert dispatched, "dispatcher parse found no doc types"
    missing = dispatched - docs_module._TEMPLATE_HANDLED_DOC_TYPES
    assert not missing, f"template-handled types missing from the sibling gate: {missing}"


def test_pipeline_gates_attempt_hit_and_learning() -> None:
    """All three gate sites exist in the pipeline body: the attempt is gated
    on the analyst's tag, a hit is rejected by its source type, and learning
    is gated on the classified type."""
    src = inspect.getsource(docs_module._run_extraction_pipeline_inner)
    flat = src.replace("\n", "").replace(" ", "")
    assert "SIBLING_TEMPLATE_REUSE_ENABLEDand_sibling_reuse_allowed(user_provided_doc_type)" in flat
    assert "sibling reuse REJECTED" in src
    assert "fieldsand_sibling_reuse_allowed(classified_doc_type)" in flat
