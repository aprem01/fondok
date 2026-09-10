"""Phase 1.3d — the registry-driven variance adapter changes NOTHING on the wire.

``GET /analysis/{id}/variance`` is Sam's live contract. Phase 1.3d moves the
hand-maintained maps in ``agents/variance.py`` (``_BROKER_RULE_BY_FIELD``, the
field-key normaliser, the ``USALIFinancials`` reader, the broker-claim
admission prefixes) and ``api/analysis.py`` (``_VARIANCE_CONCEPTS``,
``variance_concept``) onto ``app.ontology.registry``. Nothing about the
response may move.

``fixtures/ontology/variance_pre_registry.json`` is the **pre-change** JSON of
the endpoint on every seeded fixture the FON-54a suites use, captured on the
un-adapted code (Phase 1.3c, ``08bf554``). This module re-runs those exact
fixtures and compares the full response — key order, list order, floats and
all — against the pin.

Regenerate ONLY when a product decision deliberately changes the contract::

    python -m tests.test_ontology_variance_parity   # from apps/worker
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

PIN_PATH = Path(__file__).parent / "fixtures" / "ontology" / "variance_pre_registry.json"

#: Fixed deal ids so the pinned JSON (which carries ``deal_id``) is stable.
_FIXTURE_DEALS: dict[str, str] = {
    "fon54a_inputs__sams_deal": "1d3d0001-0000-4000-8000-000000000001",
    "fon54a_str_admission__sams_deal": "1d3d0002-0000-4000-8000-000000000002",
    "consolidation__duplicate_broker_paths": "1d3d0003-0000-4000-8000-000000000003",
}


def _fixtures() -> list[tuple[str, Callable[[UUID], Awaitable[None]], str, UUID]]:
    """``(name, seed, tenant, deal_id)`` — imported lazily so this module can
    be executed as a script as well as collected by pytest."""
    from .test_variance_consolidation import _TENANT as CONSOLIDATION_TENANT
    from .test_variance_consolidation import _seed_deal_with_duplicate_broker_paths
    from .test_variance_inputs_fon54a import _TENANT as INPUTS_TENANT
    from .test_variance_inputs_fon54a import _seed_sams_deal
    from .test_variance_str_admission_fon54a import _TENANT as STR_TENANT
    from .test_variance_str_admission_fon54a import _seed as _seed_str_admission

    return [
        (
            "fon54a_inputs__sams_deal",
            _seed_sams_deal,
            INPUTS_TENANT,
            UUID(_FIXTURE_DEALS["fon54a_inputs__sams_deal"]),
        ),
        (
            "fon54a_str_admission__sams_deal",
            _seed_str_admission,
            STR_TENANT,
            UUID(_FIXTURE_DEALS["fon54a_str_admission__sams_deal"]),
        ),
        (
            "consolidation__duplicate_broker_paths",
            _seed_deal_with_duplicate_broker_paths,
            CONSOLIDATION_TENANT,
            UUID(_FIXTURE_DEALS["consolidation__duplicate_broker_paths"]),
        ),
    ]


async def _reset(deal_id: UUID) -> None:
    """Drop any rows a previous capture left behind (the deal ids are fixed)."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    async with get_session_factory()() as s:
        for stmt in (
            "DELETE FROM extraction_results WHERE deal_id = :deal",
            "DELETE FROM documents WHERE deal_id = :deal",
            "DELETE FROM deals WHERE id = :deal",
        ):
            await s.execute(text(stmt), {"deal": str(deal_id)})
        await s.commit()


async def capture() -> dict[str, Any]:
    """Seed every fixture and return ``{name: GET /variance response JSON}``."""
    from app.api.analysis import get_variance
    from app.database import get_session_factory

    out: dict[str, Any] = {}
    for name, seed, tenant, deal_id in _fixtures():
        await _reset(deal_id)
        await seed(deal_id)
        async with get_session_factory()() as s:
            resp = await get_variance(deal_id=deal_id, session=s, tenant_id=UUID(tenant))
        out[name] = resp.model_dump(mode="json")
    return out


#: Keys Phase 4.1 ADDED to the variance response — the machine-readable
#: reason channel. The pin below still holds the pre-4.1 bytes; stripping
#: exactly these keys and comparing proves the addition is an addition and
#: nothing else moved. Do NOT grow this list to make a failure go away: a new
#: entry here is a claim that a key is additive, and the assertions under
#: :func:`test_phase4_reason_channel_is_purely_additive` have to back it.
_PHASE4_ADDED_KEYS: frozenset[str] = frozenset({"reason", "reasons"})


def _strip_phase4(node: Any) -> Any:
    """Recursively drop the Phase 4.1 reason keys from a response dump."""
    if isinstance(node, dict):
        return {
            k: _strip_phase4(v)
            for k, v in node.items()
            if k not in _PHASE4_ADDED_KEYS
        }
    if isinstance(node, list):
        return [_strip_phase4(v) for v in node]
    return node


@pytest.mark.asyncio
async def test_variance_endpoint_is_byte_identical_to_the_pre_registry_pin() -> None:
    assert PIN_PATH.exists(), f"missing pin {PIN_PATH} — regenerate on un-adapted code"
    pinned = json.loads(PIN_PATH.read_text())
    live = await capture()

    assert list(live) == list(pinned)
    for name in pinned:
        # Compare the serialised form so key ORDER and list ORDER are pinned
        # too, not just structural equality. Phase 4.1's additive reason keys
        # are stripped first — the pin is the pre-4.1 wire and stays that way.
        want = json.dumps(pinned[name], ensure_ascii=False)
        got = json.dumps(_strip_phase4(live[name]), ensure_ascii=False)
        assert got == want, f"{name}: variance response drifted from the pin"


@pytest.mark.asyncio
async def test_phase4_reason_channel_is_purely_additive() -> None:
    """The only keys the live response gained over the pin are the 4.1 ones.

    Guards the strip above: if the endpoint ever grows another key, this
    fails rather than the pin quietly ignoring it.
    """
    pinned = json.loads(PIN_PATH.read_text())
    live = await capture()

    def _keys(node: Any, trail: str = "") -> set[str]:
        out: set[str] = set()
        if isinstance(node, dict):
            for k, v in node.items():
                out.add(f"{trail}.{k}")
                out |= _keys(v, f"{trail}.{k}")
        elif isinstance(node, list):
            for v in node:
                out |= _keys(v, f"{trail}[]")
        return out

    for name in pinned:
        added = _keys(live[name]) - _keys(pinned[name])
        assert added, f"{name}: expected the Phase 4.1 reason keys to be present"
        # Every new path must sit at, or under, one of the added keys.
        offenders = [
            path
            for path in added
            if not any(
                seg.removesuffix("[]") in _PHASE4_ADDED_KEYS
                for seg in path.split(".")
            )
        ]
        assert not offenders, f"{name}: non-additive keys appeared: {sorted(offenders)}"


@pytest.mark.asyncio
async def test_capture_is_deterministic() -> None:
    """The pin is only meaningful if two runs of the same fixture agree."""
    first = await capture()
    second = await capture()
    assert json.dumps(first) == json.dumps(second)


@pytest.mark.asyncio
async def test_no_registry_fallbacks_on_the_pinned_fixtures() -> None:
    """Every path these fixtures carry is classified by the registry.

    A non-empty fallback map means ``concepts.yaml`` is missing an alias and
    the adapter is running on the pre-registry last-segment strip.
    """
    from app.agents import variance

    variance.REGISTRY_FALLBACKS.clear()
    await capture()
    assert variance.REGISTRY_FALLBACKS == {}


# ── the pre-registry maps, verbatim from 08bf554 (Phase 1.3c) ──
#
# The endpoint pin above proves the WIRE is unchanged; these prove the derived
# vocabulary is the same vocabulary, key for key, so a later ``concepts.yaml``
# edit that would silently widen the admission gate or re-label a flag fails
# here rather than on Sam's screen.

_LEGACY_BROKER_RULE_BY_FIELD: dict[str, str] = {
    "noi": "BROKER_VS_T12_NOI_VARIANCE",
    "noi_usd": "BROKER_VS_T12_NOI_VARIANCE",
    "occupancy": "BROKER_VS_T12_OCC_VARIANCE",
    "occupancy_pct": "BROKER_VS_T12_OCC_VARIANCE",
    "adr": "BROKER_VS_T12_ADR_VARIANCE",
    "adr_usd": "BROKER_VS_T12_ADR_VARIANCE",
    "revpar": "REVPAR_GROWTH_RANGE",
    "revpar_usd": "REVPAR_GROWTH_RANGE",
    "rooms_revenue": "BROKER_VS_T12_NOI_VARIANCE",
    "rooms_revenue_usd": "BROKER_VS_T12_NOI_VARIANCE",
    "fb_revenue": "FB_DEPT_MARGIN_FULL",
    "fb_revenue_usd": "FB_DEPT_MARGIN_FULL",
    "total_revenue": "BROKER_VS_T12_NOI_VARIANCE",
    "total_revenue_usd": "BROKER_VS_T12_NOI_VARIANCE",
    "departmental_expenses": "DEPT_EXPENSE_SUM",
    "departmental_expenses_usd": "DEPT_EXPENSE_SUM",
    "undistributed_expenses": "A_AND_G_PCT_REVENUE",
    "undistributed_expenses_usd": "A_AND_G_PCT_REVENUE",
    "gop": "GOP_MARGIN_RANGE",
    "gop_usd": "GOP_MARGIN_RANGE",
    "mgmt_fee": "MGMT_FEE_RANGE",
    "mgmt_fee_usd": "MGMT_FEE_RANGE",
    "ffe_reserve": "FFE_RESERVE_RANGE",
    "ffe_reserve_usd": "FFE_RESERVE_RANGE",
    "fixed_charges": "INSURANCE_PER_KEY",
    "fixed_charges_usd": "INSURANCE_PER_KEY",
    "insurance": "INSURANCE_PER_KEY",
    "insurance_usd": "INSURANCE_PER_KEY",
}

_LEGACY_VARIANCE_CONCEPTS: dict[str, tuple[str, str]] = {
    "noi": ("NOI", "noi"),
    "gop": ("GOP", "noi"),
    "rooms_revenue": ("Rooms revenue", "revenue"),
    "fb_revenue": ("F&B revenue", "revenue"),
    "other_revenue": ("Other revenue", "revenue"),
    "resort_fees": ("Resort fees", "revenue"),
    "total_revenue": ("Total revenue", "revenue"),
    "occupancy": ("Occupancy", "revenue"),
    "adr": ("ADR", "revenue"),
    "revpar": ("RevPAR", "revenue"),
    "departmental_expenses": ("Departmental expenses", "expense"),
    "undistributed_expenses": ("Undistributed expenses", "expense"),
    "mgmt_fee": ("Management fee", "expense"),
    "ffe_reserve": ("FF&E reserve", "expense"),
    "fixed_charges": ("Fixed charges", "expense"),
    "insurance": ("Insurance", "expense"),
    "property_taxes": ("Property taxes", "expense"),
    "broker_adr_growth_vs_market": ("ADR growth vs. market forecast", "other"),
    "broker_revpar_growth_vs_market": ("RevPAR growth vs. market forecast", "other"),
}

#: What ``BROKER_CLAIM_PREFIXES`` held before it was derived. The registry
#: supplies the three namespace prefixes; the fourth ("explicitly the
#: broker's, wherever it sits") is its basis rule, so the pin is on the
#: PREDICATE, not on the tuple.
_LEGACY_CLAIM_PATHS: list[str] = [
    "broker_proforma.rooms_revenue_usd",
    "broker.rooms_revenue",
    "ttm_summary_per_om.occupancy_pct",
    "ttm_performance.subject.adr_usd",
]
_LEGACY_NON_CLAIM_PATHS: list[str] = [
    "p_and_l_usali.operating_revenue.rooms_revenue",
    "p_and_l_usali.gross_operating_profit",
    "ttm_performance.segment.luxury_upper_upscale.occupancy_pct",
    "rooms_revenue_usd",
    "brokerage.rooms_revenue",
]


def test_derived_broker_rule_map_matches_the_hand_written_one() -> None:
    from app.agents.variance import _BROKER_RULE_BY_FIELD

    assert _BROKER_RULE_BY_FIELD == _LEGACY_BROKER_RULE_BY_FIELD


def test_derived_variance_concept_catalog_matches_the_hand_written_one() -> None:
    from app.api.analysis import _VARIANCE_CONCEPTS

    assert _VARIANCE_CONCEPTS == _LEGACY_VARIANCE_CONCEPTS


def test_derived_claim_predicate_matches_the_hand_written_prefixes() -> None:
    from app.agents.variance import is_broker_claim_path, is_explicit_broker_path

    for path in _LEGACY_CLAIM_PATHS:
        assert is_broker_claim_path(path), path
    for path in _LEGACY_NON_CLAIM_PATHS:
        assert not is_broker_claim_path(path), path
    # "Explicitly the broker's" stays the narrower of the two.
    assert is_explicit_broker_path("broker_proforma.noi_usd")
    assert is_explicit_broker_path("broker.rooms_revenue")
    assert not is_explicit_broker_path("ttm_summary_per_om.noi_usd")
    assert not is_explicit_broker_path("broker_noi")


if __name__ == "__main__":  # pragma: no cover - regeneration entry point
    import asyncio

    PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = asyncio.run(capture())
    PIN_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {PIN_PATH} ({len(data)} fixtures)")
