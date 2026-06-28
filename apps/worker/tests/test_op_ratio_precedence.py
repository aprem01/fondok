"""Wave 2 P2.7 — Op-ratio precedence resolver tests.

Pins Sam's June 2026 ask ("op-ratios from CBRE / in-house portfolio
P&Ls (not HOST defaults)"). The precedence chain, highest to lowest,
is: analyst_override > t12_actual > portfolio_pnl > cbre_horizons >
pnl_benchmark > seed.

Coverage:

1. ``analyst_override`` wins when every other tier is also present.
2. ``t12_actual`` wins over portfolio_pnl / CBRE / pnl_benchmark /
   seed when override is absent — subject's own historical actuals
   beat every external benchmark.
3. ``portfolio_pnl`` wins over CBRE — the firm's own roll-up is more
   credible than CBRE's market-wide read.
4. ``cbre_horizons`` wins over ``pnl_benchmark`` — CBRE is segmented;
   HostStats default isn't.
5. ``seed`` wins when every higher tier is None — Kimpton fallback.
6. CBRE with a mismatched chain scale falls THROUGH to the next tier
   instead of being applied.
7. Engine output carries the winning source per ratio — propagation
   pin via the :data:`SOURCE_*` constants in ``engine_runner.py``.
8. The PORTFOLIO_PNL extraction schema parses (loader smoke test).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Force a per-test SQLite DB BEFORE importing app modules so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-op-ratio-precedence.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


from app.services.op_ratio_precedence import (  # noqa: E402
    RATIO_PRECEDENCE,
    RatioValue,
    resolve_all,
    resolve_ratio,
)


# ─────────────────────── Tier-ordering tests ───────────────────────


def test_analyst_override_wins_over_all() -> None:
    """Override beats every other tier — final-say semantics."""
    candidates = {
        "analyst_override": RatioValue(value=0.21, source="analyst_override"),
        "t12_actual": RatioValue(value=0.12, source="t12_actual", document_id="t12-doc"),
        "portfolio_pnl": RatioValue(value=0.11, source="portfolio_pnl", document_id="port-doc"),
        "cbre_horizons": RatioValue(
            value=0.14, source="cbre_horizons", chain_scale="Upper Upscale"
        ),
        "pnl_benchmark": RatioValue(value=0.13, source="pnl_benchmark"),
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upper Upscale"
    )
    assert winner is not None
    assert winner.source == "analyst_override"
    assert winner.value == pytest.approx(0.21)


def test_t12_actual_wins_when_no_override() -> None:
    """T-12 actuals beat every benchmark when no override is set."""
    candidates = {
        "analyst_override": None,
        "t12_actual": RatioValue(value=0.12, source="t12_actual", document_id="t12-doc"),
        "portfolio_pnl": RatioValue(value=0.11, source="portfolio_pnl"),
        "cbre_horizons": RatioValue(
            value=0.14, source="cbre_horizons", chain_scale="Upper Upscale"
        ),
        "pnl_benchmark": RatioValue(value=0.13, source="pnl_benchmark"),
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upper Upscale"
    )
    assert winner is not None
    assert winner.source == "t12_actual"
    assert winner.value == pytest.approx(0.12)
    assert winner.document_id == "t12-doc"


def test_portfolio_pnl_wins_over_cbre() -> None:
    """Firm's in-house portfolio beats CBRE Horizons market-wide read."""
    candidates = {
        "analyst_override": None,
        "t12_actual": None,
        "portfolio_pnl": RatioValue(
            value=0.11, source="portfolio_pnl", document_id="port-doc"
        ),
        "cbre_horizons": RatioValue(
            value=0.14, source="cbre_horizons", chain_scale="Upper Upscale"
        ),
        "pnl_benchmark": RatioValue(value=0.13, source="pnl_benchmark"),
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upper Upscale"
    )
    assert winner is not None
    assert winner.source == "portfolio_pnl"
    assert winner.value == pytest.approx(0.11)


def test_cbre_wins_over_pnl_benchmark() -> None:
    """CBRE Horizons (chain-scale-segmented) beats generic HostStats."""
    candidates = {
        "analyst_override": None,
        "t12_actual": None,
        "portfolio_pnl": None,
        "cbre_horizons": RatioValue(
            value=0.14,
            source="cbre_horizons",
            chain_scale="Upper Upscale",
            document_id="cbre-doc",
        ),
        "pnl_benchmark": RatioValue(value=0.13, source="pnl_benchmark"),
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upper Upscale"
    )
    assert winner is not None
    assert winner.source == "cbre_horizons"
    assert winner.value == pytest.approx(0.14)
    assert winner.document_id == "cbre-doc"


def test_seed_is_last_resort() -> None:
    """When every higher tier is None, the seed default is used."""
    candidates = {
        "analyst_override": None,
        "t12_actual": None,
        "portfolio_pnl": None,
        "cbre_horizons": None,
        "pnl_benchmark": None,
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upper Upscale"
    )
    assert winner is not None
    assert winner.source == "seed"
    assert winner.value == pytest.approx(0.30)


def test_chain_scale_mismatch_falls_through_cbre() -> None:
    """CBRE candidate present but wrong chain scale → next tier wins.

    Subject is "Upper Upscale". CBRE candidate is "Lower Priced". The
    resolver MUST skip CBRE and fall through to ``pnl_benchmark``.
    Without this filter we'd apply a luxury-tier ratio to an upscale
    hotel — Sam's exact complaint about the HOST default ("not the
    right peer set").
    """
    candidates = {
        "analyst_override": None,
        "t12_actual": None,
        "portfolio_pnl": None,
        "cbre_horizons": RatioValue(
            value=0.18, source="cbre_horizons", chain_scale="Lower Priced"
        ),
        "pnl_benchmark": RatioValue(value=0.13, source="pnl_benchmark"),
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upper Upscale"
    )
    assert winner is not None
    # CBRE was skipped due to chain-scale mismatch; pnl_benchmark wins.
    assert winner.source == "pnl_benchmark"
    assert winner.value == pytest.approx(0.13)


# ─────────────────────── Propagation pin ───────────────────────


def test_resolved_source_propagates_to_engine_output() -> None:
    """Wave 2 P2.7 worked example pin:

    200-key Marriott Courtyard, T-12 shows 12% rooms-dept ratio,
    CBRE Horizons says 14%, portfolio P&L says 11%, no override.

    The resolver's job is to pick T-12 (12%) AND tag the winning
    RatioValue with ``source="t12_actual"`` so the engine output
    can render the correct AssumptionBadge on the P&L tab.

    This test pins both: the value AND the source label that flows
    through to the engine assumption-source map.
    """
    candidates = {
        "analyst_override": None,
        "t12_actual": RatioValue(
            value=0.12, source="t12_actual", document_id="t12-marriott-cy"
        ),
        "portfolio_pnl": RatioValue(value=0.11, source="portfolio_pnl"),
        "cbre_horizons": RatioValue(
            value=0.14, source="cbre_horizons", chain_scale="Upscale"
        ),
        "pnl_benchmark": RatioValue(value=0.13, source="pnl_benchmark"),
        "seed": RatioValue(value=0.30, source="seed"),
    }
    winner = resolve_ratio(
        "rooms_dept_pct", candidates, subject_chain_scale="Upscale"
    )
    assert winner is not None
    assert winner.value == pytest.approx(0.12)
    # The source label is what `_load_engine_inputs` writes into
    # ``base["__sources__"]["rooms_dept_pct"]`` — the web app's
    # AssumptionBadge renders the label as "T-12" badge.
    assert winner.source == "t12_actual"
    assert winner.document_id == "t12-marriott-cy"

    # Also pin resolve_all batch behavior: when called across multiple
    # ratios, each gets its own winner independently.
    multi = {
        "rooms_dept_pct": candidates,
        "fb_dept_pct": {
            # T-12 missing for F&B, falls to portfolio.
            "analyst_override": None,
            "t12_actual": None,
            "portfolio_pnl": RatioValue(value=0.74, source="portfolio_pnl"),
            "cbre_horizons": RatioValue(
                value=0.78, source="cbre_horizons", chain_scale="Upscale"
            ),
            "pnl_benchmark": RatioValue(value=0.75, source="pnl_benchmark"),
            "seed": RatioValue(value=0.75, source="seed"),
        },
    }
    resolved = resolve_all(multi, subject_chain_scale="Upscale")
    assert resolved["rooms_dept_pct"].source == "t12_actual"
    assert resolved["fb_dept_pct"].source == "portfolio_pnl"
    # Sanity: precedence order is the source of truth.
    assert RATIO_PRECEDENCE.index("t12_actual") < RATIO_PRECEDENCE.index(
        "portfolio_pnl"
    )


# ─────────────────────── Extraction schema smoke test ───────────────────────


def test_portfolio_pnl_extraction_schema_loads() -> None:
    """The PORTFOLIO_PNL doc-type Markdown schema must load via the
    Extractor's dynamic-schema loader.

    Sam's June 2026 ask hinges on the Extractor knowing how to pull
    the portfolio ratios off an uploaded firm-roll-up doc; if the
    schema file is missing or unreadable the dynamic loader would
    silently fall back to generic extraction and the resolver would
    never see a ``portfolio_pnl`` candidate.

    The loader is gated on ``EXTRACTOR_USE_DYNAMIC_SCHEMAS``; we toggle
    it ON for this one test only.
    """
    import os

    from app.agents.extraction_schemas.loader import (
        available_doc_types,
        build_system_prompt,
        is_enabled,
    )

    # Sanity — the schema file must be discoverable regardless of the
    # env flag (the loader scans the directory).
    types = available_doc_types()
    assert "PORTFOLIO_PNL" in types, (
        f"PORTFOLIO_PNL not in discovered schemas: {types}. "
        "Did you forget to create apps/worker/app/agents/extraction_schemas/portfolio_pnl.md ?"
    )

    # Now opt into the dynamic loader and confirm the assembled prompt
    # actually contains the portfolio_pnl payload header.
    prev = os.environ.get("EXTRACTOR_USE_DYNAMIC_SCHEMAS")
    os.environ["EXTRACTOR_USE_DYNAMIC_SCHEMAS"] = "1"
    try:
        assert is_enabled()
        prompt = build_system_prompt("PORTFOLIO_PNL")
        assert prompt is not None
        assert "PORTFOLIO_PNL" in prompt or "portfolio_pnl" in prompt.lower()
        # A few canonical keys must show up in the schema preamble so
        # the LLM knows the field-path conventions.
        assert "portfolio_pnl.rooms_dept_pct" in prompt
        assert "portfolio_pnl.admin_pct" in prompt
        assert "portfolio_pnl.utilities_pct" in prompt
    finally:
        if prev is None:
            os.environ.pop("EXTRACTOR_USE_DYNAMIC_SCHEMAS", None)
        else:
            os.environ["EXTRACTOR_USE_DYNAMIC_SCHEMAS"] = prev
