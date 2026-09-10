"""Phase 1.3c — the registry-driven critic loader produces the SAME numbers.

``api/documents.py::_load_critic_inputs`` now reads the concept registry
(``app/ontology/concepts.yaml``) instead of its own ``_canonical_key`` alias
table, ``_ANNUAL_HINTS`` substring list and hand-written OM basis rules. Every
number it hands the Critic and the variance report is Sam-facing, so the swap
had to be output-neutral.

``tests/fixtures/ontology/critic_inputs_pre_registry.json`` pins the exact
``USALIFinancials`` dicts the loader produced BEFORE the swap, over six
scenarios:

* the two real extraction payloads in ``tests/fixtures/real_payloads/`` —
  alone and together (the T-12-outranks-a-P&L rule);
* the two Sam-facing FON-54a fixtures (``test_variance_inputs_fon54a.py`` and
  ``test_variance_str_admission_fon54a.py`` seed the same documents);
* the FON-41 live capture of deal ``e577f547`` (four financial documents;
  rows synthesised from each document's ``low_conf_unreviewed`` block).

A diff here is not a test failure to paper over: it means the registry moved a
number the analyst sees. Either the registry (or the loader's parity freeze,
``documents._PRE_REGISTRY_CRITIC_TAILS``) is wrong, or the change is deliberate
and belongs in ``app/ontology/DRIFT_NOTES.md`` § "Phase 1.3c parity exceptions"
with the pin regenerated in the same commit.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_TENANT = "13c0a5e5-0000-5000-8000-0000000013c0"
_FIXTURES = Path(__file__).parent / "fixtures"
_PIN = _FIXTURES / "ontology" / "critic_inputs_pre_registry.json"


def _pin() -> dict[str, Any]:
    return json.loads(_PIN.read_text())


def _fields(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The extraction rows for one seeded document."""
    if "fields_file" in doc:
        return json.loads((_FIXTURES / doc["fields_file"]).read_text())["fields"]
    return doc["fields"]


async def _seed(scenario: dict[str, Any]) -> str:
    """Insert the scenario's deal + documents + extractions; return the deal id."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    deal_id = uuid4()
    t0 = datetime.now(UTC) - timedelta(days=3)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) VALUES (:id,:t,:n,'Draft',0.0,:ts,:ts)"
            ),
            {"id": str(deal_id), "t": _TENANT, "n": f"parity {scenario['name']}", "ts": t0},
        )
        for doc in scenario["docs"]:
            doc_id = uuid4()
            ts = t0 + timedelta(hours=doc["hours"])
            await s.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
                    "status, uploaded_at) VALUES (:id,:deal,:t,:f,:dt,'EXTRACTED',:ts)"
                ),
                {"id": str(doc_id), "deal": str(deal_id), "t": _TENANT,
                 "f": doc["filename"], "dt": doc["doc_type"], "ts": ts},
            )
            await s.execute(
                text(
                    "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, "
                    "fields, confidence_report, agent_version, created_at) "
                    "VALUES (:id,:doc,:deal,:t,:f,'{}','v1',:ts)"
                ),
                {"id": str(uuid4()), "doc": str(doc_id), "deal": str(deal_id), "t": _TENANT,
                 "f": json.dumps(_fields(doc)), "ts": ts},
            )
        await s.commit()
    return str(deal_id)


async def _critic_inputs(scenario: dict[str, Any]) -> dict[str, Any]:
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id = await _seed(scenario)
    async with get_session_factory()() as s:
        broker, actuals, _market, _keys = await _load_critic_inputs(
            s, deal_id=deal_id, tenant_id=_TENANT
        )
    return {
        "broker": broker.model_dump(mode="json") if broker is not None else None,
        "actuals": actuals.model_dump(mode="json") if actuals is not None else None,
    }


def _scenario_ids() -> list[str]:
    return [s["name"] for s in _pin()["scenarios"]]


@pytest.mark.parametrize("scenario_name", _scenario_ids())
@pytest.mark.asyncio
async def test_critic_inputs_match_the_pre_registry_pin(scenario_name: str) -> None:
    scenario = next(s for s in _pin()["scenarios"] if s["name"] == scenario_name)
    got = await _critic_inputs(scenario)
    assert got == scenario["expected"], (
        f"{scenario_name}: the registry-driven loader moved a Sam-facing number. "
        f"{scenario['why']}"
    )


def test_pin_covers_every_required_corpus() -> None:
    """The pin is the contract — it must keep covering all three corpora."""
    names = set(_scenario_ids())
    assert {"real_payload_t12_only", "real_payload_annual_pnl_only"} <= names
    assert {"fon54a_inputs", "fon54a_str_admission"} <= names
    assert "fon41_live_capture" in names
    for scenario in _pin()["scenarios"]:
        assert scenario["docs"], scenario["name"]
        assert scenario["expected"]["actuals"] is not None, scenario["name"]


def test_fon54a_scenarios_pin_the_numbers_the_sam_facing_tests_assert() -> None:
    """The FON-54a scenarios really are the documents those two tests seed.

    If someone re-seeds ``test_variance_inputs_fon54a.py`` the pin must move
    with it — this catches a silently diverged copy.
    """
    scenarios = {s["name"]: s for s in _pin()["scenarios"]}
    actuals = scenarios["fon54a_inputs"]["expected"]["actuals"]
    broker = scenarios["fon54a_inputs"]["expected"]["broker"]
    # …the exact assertions of test_critic_inputs_use_annual_t12_lines_…
    assert actuals["rooms_revenue"] == 9_332_100
    assert actuals["gop"] == 4_970_460
    assert actuals["noi"] == 1_794_100
    assert actuals["total_revenue"] == 13_796_340
    assert actuals["occupancy"] == pytest.approx(0.716)
    assert actuals["adr"] == pytest.approx(372.4)
    assert broker["rooms_revenue"] == 9_541_537
    assert broker["gop"] == 5_088_268
    assert broker["noi"] == 3_356_709
    assert broker["occupancy"] == pytest.approx(0.83)
    assert broker["adr"] == pytest.approx(385.0)

    # …and the STR-admission fixture's actual side (never an STR reading).
    str_actuals = scenarios["fon54a_str_admission"]["expected"]["actuals"]
    assert str_actuals["occupancy"] == pytest.approx(0.83)
    assert str_actuals["adr"] == pytest.approx(232.77)
    assert str_actuals["occupancy"] not in (0.7118, 0.716, 0.829, 0.824)


def test_parity_freeze_is_a_subset_of_the_registry() -> None:
    """Every tail the parity freeze keeps must still name a live critic key.

    The freeze (``_PRE_REGISTRY_CRITIC_TAILS``) is temporary; this stops it
    outliving a ``bindings.critic_key`` rename in ``concepts.yaml``.
    """
    from app.api.documents import (
        _PRE_REGISTRY_CRITIC_PARENTS,
        _PRE_REGISTRY_CRITIC_TAILS,
    )
    from app.ontology import registry as ontology

    live = {
        c.bindings.critic_key
        for c in ontology.get_registry().concepts.values()
        if c.bindings.critic_key
    }
    assert set(_PRE_REGISTRY_CRITIC_TAILS) <= live
    assert set(_PRE_REGISTRY_CRITIC_PARENTS) <= set(_PRE_REGISTRY_CRITIC_TAILS)


def test_loader_region_carries_no_extraction_path_literals() -> None:
    """Phase 1.3c conformance for this region: the aliases live in the registry.

    Scoped to ``_load_critic_inputs`` and its Phase 1.3c helpers — other
    regions of ``documents.py`` (extraction, doc-type verification, the STR
    block builders) are other builders' and still carry their own literals.
    """
    import inspect

    from app.api import documents

    src = "\n".join(
        inspect.getsource(obj)
        for obj in (documents._load_critic_inputs, documents._pre_registry_critic_reach)
    )
    for literal in ("p_and_l_usali.", "ttm_summary_per_om.", "broker_proforma.", "ttm_performance."):
        assert literal not in src, f"{literal} still hard-coded in the critic loader"
