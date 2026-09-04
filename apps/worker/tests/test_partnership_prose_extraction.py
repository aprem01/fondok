"""FON-66 Part B — prose partnership / JV extraction wiring.

The deterministic template extractor (``template_extractors/partnership.py``)
already reads the STRUCTURED Fondok upload grid at $0. Part B teaches the LLM
extraction path to pull the SAME canonical fields out of a real prose operating
agreement, and confirms the read side (``_load_partnership_terms``) picks them
up under the exact names the engine consumes.

These are unit / light-integration tests of the field-catalog + loader path —
no live LLM. They prove:

  1. The extractor's requested field set (its SYSTEM_PROMPT, and the dynamic
     schema file) now advertises the partnership canonical paths, so a prose
     JV doc has somewhere to land its terms.
  2. A prose JV extraction — a persisted ``extraction_results`` row carrying
     those canonical field_names as 0..1 fractions — round-trips through
     ``_load_partnership_terms`` into the ``(scalars, waterfall)`` shape the
     partnership engine overlays.
  3. A prose PARTNERSHIP doc is exempt from the strict chunk-signal prefilter,
     so its narrative pages (where the terms live) aren't dropped before the
     LLM ever sees them.

No fabrication: every value asserted here is one supplied in the fixture; the
loader invents nothing.
"""

from __future__ import annotations

import json
from uuid import uuid4

from app.agents.extractor import SYSTEM_PROMPT
from app.services.engine_runner import (
    _load_partnership_terms,
    _parse_partnership_override_path,
)

# The three scalar canonical names the loader recognizes + the indexed
# waterfall stem. Kept here as the contract between the prompt (what the LLM
# is told to emit) and the loader (what it reads back).
_SCALAR_NAMES = (
    "partnership.gp_equity_pct",
    "partnership.lp_equity_pct",
    "partnership.pref_rate",
)


def test_extractor_prompt_advertises_partnership_fields() -> None:
    """The legacy (production-default) SYSTEM_PROMPT requests the partnership
    canonical paths, so a prose JV doc extracts them instead of nothing."""
    for name in _SCALAR_NAMES:
        assert name in SYSTEM_PROMPT, f"{name} missing from extractor SYSTEM_PROMPT"
    # Indexed waterfall tier fields (the LLM emits ``<idx>`` per tier found).
    assert "partnership.waterfall.<idx>.hurdle_rate" in SYSTEM_PROMPT
    assert "partnership.waterfall.<idx>.gp_split" in SYSTEM_PROMPT
    assert "partnership.waterfall.<idx>.lp_split" in SYSTEM_PROMPT
    # The units guard must survive: partnership values are 0..1 fractions, not
    # raw percents (the loader applies them verbatim).
    assert "fraction" in SYSTEM_PROMPT.lower()


def test_dynamic_schema_advertises_partnership_fields() -> None:
    """The opt-in dynamic-schema path exposes a partnership.md that advertises
    the same canonical paths, keeping both extraction paths in sync."""
    import os

    from app.agents.extraction_schemas.loader import (
        available_doc_types,
        build_system_prompt,
    )

    assert "PARTNERSHIP" in available_doc_types(), (
        "partnership.md not discovered — did you drop "
        "apps/worker/app/agents/extraction_schemas/partnership.md ?"
    )

    prev = os.environ.get("EXTRACTOR_USE_DYNAMIC_SCHEMAS")
    os.environ["EXTRACTOR_USE_DYNAMIC_SCHEMAS"] = "1"
    try:
        prompt = build_system_prompt("PARTNERSHIP")
        assert prompt is not None
        for name in _SCALAR_NAMES:
            assert name in prompt
        assert "partnership.waterfall.<idx>.hurdle_rate" in prompt
    finally:
        if prev is None:
            os.environ.pop("EXTRACTOR_USE_DYNAMIC_SCHEMAS", None)
        else:
            os.environ["EXTRACTOR_USE_DYNAMIC_SCHEMAS"] = prev


def test_prompted_tier_paths_parse_as_loader_override_keys() -> None:
    """Every indexed tier path the prompt tells the LLM to emit must parse as a
    valid partnership override key, or the loader would silently drop it."""
    for idx in range(4):
        for field in ("hurdle_rate", "gp_split", "lp_split"):
            path = f"partnership.waterfall.{idx}.{field}"
            parsed = _parse_partnership_override_path(path)
            assert parsed == (idx, field), f"{path} did not parse to ({idx}, {field})"


async def _seed_partnership_extraction(
    *,
    deal_id: str,
    tenant_id: str,
    fields: list[dict],
) -> None:
    """Insert a PARTNERSHIP document + an extraction_results row whose ``fields``
    are exactly what the LLM extractor would persist for a prose JV doc."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = str(uuid4())
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status)
                VALUES (:id, :tenant, :name, 'Draft')
                """
            ),
            {"id": deal_id, "tenant": tenant_id, "name": "Prose JV Hotel"},
        )
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status
                ) VALUES (:id, :deal, :tenant, :fn, 'PARTNERSHIP', 'EXTRACTED')
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": "Operating_Agreement.pdf",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields, created_at
                ) VALUES (:id, :doc, :deal, :tenant, :fields, :created)
                """
            ),
            {
                "id": str(uuid4()),
                "doc": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fields": json.dumps(fields),
                "created": "2026-09-04T00:00:00+00:00",
            },
        )
        await session.commit()


async def test_load_partnership_terms_reads_prose_style_fields() -> None:
    """A prose JV extraction (scalars + two promote tiers, all 0..1 fractions)
    round-trips through ``_load_partnership_terms`` into the exact
    ``(scalars, waterfall)`` shape the partnership engine overlays."""
    from app.database import get_session_factory

    deal_id = str(uuid4())
    tenant_id = str(uuid4())

    # Shaped exactly like a persisted LLM extraction: long-form field rows with
    # ``field_name`` + a 0..1 fractional ``value`` + a confidence.
    fields = [
        {"field_name": "partnership.gp_equity_pct", "value": 0.10, "unit": "ratio", "confidence": 0.9},
        {"field_name": "partnership.lp_equity_pct", "value": 0.90, "unit": "ratio", "confidence": 0.9},
        {"field_name": "partnership.pref_rate", "value": 0.08, "unit": "ratio", "confidence": 0.88},
        # Tier 0 — preferred band.
        {"field_name": "partnership.waterfall.0.hurdle_rate", "value": 0.08, "unit": "ratio", "confidence": 0.85},
        {"field_name": "partnership.waterfall.0.gp_split", "value": 0.00, "unit": "ratio", "confidence": 0.85},
        {"field_name": "partnership.waterfall.0.lp_split", "value": 1.00, "unit": "ratio", "confidence": 0.85},
        # Tier 1 — first promote band.
        {"field_name": "partnership.waterfall.1.hurdle_rate", "value": 0.15, "unit": "ratio", "confidence": 0.82},
        {"field_name": "partnership.waterfall.1.gp_split", "value": 0.20, "unit": "ratio", "confidence": 0.82},
        {"field_name": "partnership.waterfall.1.lp_split", "value": 0.80, "unit": "ratio", "confidence": 0.82},
        # A non-partnership field must be ignored by the loader.
        {"field_name": "property_overview.keys", "value": 220, "unit": "keys", "confidence": 0.95},
    ]
    await _seed_partnership_extraction(deal_id=deal_id, tenant_id=tenant_id, fields=fields)

    factory = get_session_factory()
    async with factory() as session:
        scalars, waterfall = await _load_partnership_terms(
            session, deal_id=deal_id, tenant_id=tenant_id
        )

    assert scalars == {
        "gp_equity_pct": 0.10,
        "lp_equity_pct": 0.90,
        "pref_rate": 0.08,
    }
    assert waterfall == {
        0: {"hurdle_rate": 0.08, "gp_split": 0.00, "lp_split": 1.00},
        1: {"hurdle_rate": 0.15, "gp_split": 0.20, "lp_split": 0.80},
    }


async def test_load_partnership_terms_omits_absent_terms() -> None:
    """A prose doc that states only ownership (no pref, no waterfall) yields
    just those scalars — the loader never fabricates the missing terms."""
    from app.database import get_session_factory

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    fields = [
        {"field_name": "partnership.gp_equity_pct", "value": 0.15, "unit": "ratio", "confidence": 0.8},
        {"field_name": "partnership.lp_equity_pct", "value": 0.85, "unit": "ratio", "confidence": 0.8},
    ]
    await _seed_partnership_extraction(deal_id=deal_id, tenant_id=tenant_id, fields=fields)

    factory = get_session_factory()
    async with factory() as session:
        scalars, waterfall = await _load_partnership_terms(
            session, deal_id=deal_id, tenant_id=tenant_id
        )

    assert scalars == {"gp_equity_pct": 0.15, "lp_equity_pct": 0.85}
    assert waterfall == {}


def test_partnership_exempt_from_strict_chunk_prefilter() -> None:
    """A prose JV operating agreement must skip the strict signal prefilter, or
    its narrative pages (where the terms live, low on dollar/P&L vocab) would be
    dropped before the LLM sees them."""
    from app.api.documents import _PREFILTER_SKIP_DOC_TYPES

    assert "PARTNERSHIP" in _PREFILTER_SKIP_DOC_TYPES
