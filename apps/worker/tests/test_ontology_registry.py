"""Phase 1.1 — concept registry: validation, resolution, absorption, boot invariant.

The registry (``app/ontology/concepts.yaml``) absorbs nine hand-maintained
alias maps. These tests lock:

    * every validation failure raises at load (a mutated YAML in tmp);
    * ``resolve`` picks the ANNUAL / TTM line over a monthly slice on the
      real Angler's T-12 fixture and on the FON-41 live capture;
    * ``concept_for_path`` classifies a monthly slice as (gop, actual, monthly);
    * every identifier in every ``usali-rules.csv`` formula is some concept's
      scorer identifier (the foundation for registry-driven scoring);
    * a broker-basis resolution on an OM never returns the OM's history
      block or a market-segment row;
    * the legacy maps are absorbed: field_catalog, usali_scorer._ALIASES,
      variance._BROKER_RULE_BY_FIELD and analysis._VARIANCE_CONCEPTS all
      round-trip through the registry;
    * /health reports ``ontology_version`` and GET /ontology/concepts serves
      the registry.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN (pattern: test_fon73_run_scoped_readers).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-ontology.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.ontology import registry as reg_mod  # noqa: E402
from app.ontology.identities import evaluate_identities  # noqa: E402
from app.ontology.registry import (  # noqa: E402
    REASON_CODES,
    RegistryError,
    concept_for_path,
    get_registry,
    identities,
    load_registry,
    registry_version,
    resolve,
    resolve_many,
)

_WORKER_ROOT = Path(__file__).resolve().parents[1]
_YAML = _WORKER_ROOT / "app" / "ontology" / "concepts.yaml"
_FIXTURE = _WORKER_ROOT / "tests" / "fixtures" / "real_payloads" / "anglers_t12_real.json"
_LIVE = Path("/Users/prem/fon41_live.json")
_RULES_CSV = _WORKER_ROOT.parents[1] / "evals" / "golden-set" / "usali-rules.csv"


def _good_yaml() -> dict[str, Any]:
    return yaml.safe_load(_YAML.read_text(encoding="utf-8"))


def _load_mutated(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Any:
    data = _good_yaml()
    mutate(data)
    p = tmp_path / "concepts.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return load_registry(p)


def _anglers_fields() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["fields"]


# ─────────────────────────────── load / version ───────────────────────────


def test_registry_loads_and_version_is_int() -> None:
    reg = get_registry()
    assert isinstance(registry_version(), int)
    assert registry_version() == reg.version == 1
    assert len(reg.concepts) >= 60
    assert set(reg.reasons) == set(REASON_CODES)


def test_reloading_the_shipped_yaml_is_valid(tmp_path: Path) -> None:
    reg = _load_mutated(tmp_path, lambda d: None)
    assert set(reg.concepts) == set(get_registry().concepts)


# ─────────────────────────────── validation ───────────────────────────────


def _dup_alias(d: dict[str, Any]) -> None:
    d["concepts"]["noi"]["aliases"]["*"].append("gop")


def _unknown_rule(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["usali_rules"].append("NOT_A_RULE")


def _rule_set_drift(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["usali_rules"].remove("GOP_IDENTITY")


def _unknown_engine(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["engines"].append("warp_drive")


def _identity_unknown_concept(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["identity"] = "total_revenue - bogus_concept"


def _identity_bad_operator(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["identity"] = "total_revenue ** 2"


def _unknown_alias_doc_type(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["aliases"]["BOGUS_DOC"] = ["gop_line"]


def _unknown_doc_type(d: dict[str, Any]) -> None:
    d["doc_types"].append("BOGUS_DOC")


def _unknown_unit(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["unit"] = "furlongs"


def _unknown_sign(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["sign"] = "sideways"


def _unknown_period(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["period"] = "eon"


def _missing_reason(d: dict[str, Any]) -> None:
    del d["reasons"]["stale_run"]


def _bad_as_of(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["as_of"] = "not_a_concept"


def _dup_scorer_identifier(d: dict[str, Any]) -> None:
    d["concepts"]["ebitda"]["bindings"]["scorer_key"] = "gop"


def _unknown_key(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["bogus_field"] = 1


def _no_aliases(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["aliases"] = {}


def _bad_source_reason(d: dict[str, Any]) -> None:
    d["sources"]["str_forecast_unavailable"]["reason"] = "nope"


def _null_scope_on_flow(d: dict[str, Any]) -> None:
    d["concepts"]["gop"]["default_scope"] = None
    d["concepts"]["gop"]["note"] = "not a point fact"


def _null_scope_without_note(d: dict[str, Any]) -> None:
    d["concepts"]["keys"]["note"] = None


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (_dup_alias, "already maps to"),
        (_unknown_rule, "unknown usali_rules id"),
        (_rule_set_drift, "must equal the rules naming"),
        (_unknown_engine, "unknown engine"),
        (_identity_unknown_concept, "unknown concept"),
        (_identity_bad_operator, "not allowed"),
        (_unknown_alias_doc_type, "unknown alias doc type"),
        (_unknown_doc_type, "not a router DocType"),
        (_unknown_unit, "unit"),
        (_unknown_sign, "sign"),
        (_unknown_period, "period"),
        (_missing_reason, "reasons"),
        (_bad_as_of, "as_of"),
        (_dup_scorer_identifier, "gop"),
        (_unknown_key, "bogus_field"),
        (_no_aliases, "at least one alias"),
        (_bad_source_reason, "reason"),
        (_null_scope_on_flow, "default_scope null"),
        (_null_scope_without_note, "default_scope null"),
    ],
    ids=lambda x: getattr(x, "__name__", str(x)),
)
def test_validation_failures_raise_at_load(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], needle: str
) -> None:
    with pytest.raises(RegistryError) as exc:
        _load_mutated(tmp_path, mutate)
    assert needle in str(exc.value)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "nope.yaml")


# ─────────────────────────────── resolution ───────────────────────────────


def test_anglers_fixture_annual_gop_over_monthly() -> None:
    fields = _anglers_fields()
    res = resolve(fields, "gop", doc_type="T12")
    assert res.field_name == "p_and_l_usali.gross_operating_profit_usd"
    assert res.value == 5081540.0
    assert res.scope == "ttm" and res.basis == "actual" and res.reason is None
    assert res.candidates[0].tier == reg_mod.TIER_EXACT_DOC
    assert res.source_page is not None and res.confidence == 0.5


def test_anglers_fixture_kpis_skip_monthly_slices() -> None:
    fields = _anglers_fields()
    monthly = [f["field_name"] for f in fields if ".monthly." in f["field_name"] and f["field_name"].endswith("adr_usd")]
    assert monthly, "fixture must carry monthly ADR slices for this test to mean anything"
    res = resolve(fields, "adr", doc_type="T12")
    assert res.field_name == "ttm_summary_per_om.adr_usd"
    assert res.value == pytest.approx(233.446)
    excluded = {c.field_name: c.excluded for c in res.candidates if c.excluded}
    assert any(name in excluded and excluded[name] == "period_mismatch" for name in monthly)
    # Asking for the monthly scope flips the pick to a monthly slice.
    res_m = resolve(fields, "adr", doc_type="T12", want="monthly")
    assert res_m.field_name in monthly and res_m.scope == "monthly"


def test_anglers_fixture_whole_pnl_resolves_on_real_paths() -> None:
    fields = _anglers_fields()
    got = resolve_many(
        fields,
        ["total_revenue", "rooms_revenue", "fb_revenue", "dept_expenses", "undistributed_expenses",
         "mgmt_fee", "ffe_reserve", "insurance", "property_taxes", "noi", "keys", "period_ending"],
        doc_type="T12",
    )
    assert got["total_revenue"].value == 14009800.0
    assert got["rooms_revenue"].field_name == "p_and_l_usali.rooms.revenue_usd"
    assert got["fb_revenue"].field_name == "p_and_l_usali.food_and_beverage.revenue_usd"
    assert got["dept_expenses"].field_name == "p_and_l_usali.total_departmental_expense_usd"
    assert got["undistributed_expenses"].value == 3540620.0
    assert got["mgmt_fee"].value == 650353.0
    assert got["ffe_reserve"].value == 560393.0
    assert got["insurance"].field_name == "p_and_l_usali.non_operating.insurance_usd"
    assert got["property_taxes"].field_name == "p_and_l_usali.non_operating.property_and_other_taxes_usd"
    # NOI proxy — the same line the scorer and the historicals tab use today.
    assert got["noi"].field_name == "p_and_l_usali.ebitda_less_replacement_reserve_usd"
    assert got["keys"].value == 132.0
    assert got["period_ending"].value == "2025-05-31"


@pytest.mark.skipif(not _LIVE.exists(), reason="FON-41 live capture not present on this machine")
def test_fon41_live_capture_annual_gop_over_monthly() -> None:
    live = json.loads(json.loads(_LIVE.read_text(encoding="utf-8")))
    t12 = next(d for d in live["financial_docs"] if d["doc_type"] == "T12")
    fields = list(t12["low_conf_unreviewed"])
    present = {f["field_name"] for f in fields}
    # The capture ships names (not values) for the sampled fields; give the
    # monthly slices a synthetic value so a wrong pick would be visible.
    for name in t12["sample_field_names"]:
        if name not in present:
            fields.append({"field_name": name, "value": 515000.0, "confidence": 0.99, "source_page": 1})
    assert "p_and_l_usali.monthly.apr_2024.gop" in {f["field_name"] for f in fields}
    res = resolve(fields, "gop", doc_type="T12")
    assert res.field_name == "p_and_l_usali.gross_operating_profit"
    assert res.value == 4970460.0
    slices = {c.field_name: c.excluded for c in res.candidates}
    assert slices.get("p_and_l_usali.monthly.apr_2024.gop") == "period_mismatch"
    assert resolve(fields, "gop", doc_type="T12", want="monthly").scope == "monthly"


def test_concept_for_path_classifies_slices_and_bases() -> None:
    assert concept_for_path("p_and_l_usali.monthly.apr_2024.gop", doc_type="T12") == ("gop", "actual", "monthly")
    assert concept_for_path("p_and_l_usali.gross_operating_profit", doc_type="T12") == ("gop", "actual", "unknown")
    assert concept_for_path("ttm_summary_per_om.gop_usd", doc_type="T12") == ("gop", "actual", "unknown")
    assert concept_for_path("ttm_summary_per_om.gop_usd", doc_type="OM") == ("gop", "broker", "annual")
    assert concept_for_path("p_and_l_usali.2021.gop_usd", doc_type="OM") == ("gop", "om_history", "annual")
    assert concept_for_path("p_and_l_usali.2021.gop_usd", doc_type="T12") == ("gop", "actual", "annual")
    assert concept_for_path("broker_proforma.noi_usd", doc_type="OM") == ("noi", "broker", "unknown")
    assert concept_for_path("ttm_performance.segment.occupancy_pct", doc_type="OM") == ("occupancy", "market", "ttm")
    # A P&L-family exact alias is recognised on an OM too (cross-doc tier),
    # so an expense line never reads as F&B revenue.
    assert concept_for_path("p_and_l_usali.departmental_expenses.food_beverage", doc_type="OM")[0] == "fb_dept_expense"
    assert concept_for_path("transaction_comps.3.cap_rate_pct", doc_type="OM")[0] == "comp_cap_rate"
    assert concept_for_path("no.such.path.anywhere") is None


def test_tail_match_refuses_paths_owned_by_another_concept() -> None:
    fields = [{"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 100.0}]
    res = resolve(fields, "fb_revenue", doc_type="T12")
    assert res.value is None and res.reason == "no_source"
    assert resolve(fields, "fb_dept_expense", doc_type="T12").value == 100.0


def test_every_rule_identifier_is_a_scorer_key() -> None:
    import ast
    import csv

    idents: set[str] = set()
    for c in get_registry().concepts.values():
        idents |= c.bindings.scorer_identifiers()
    missing: dict[str, set[str]] = {}
    with _RULES_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tree = ast.parse(row["formula_or_check"], mode="eval")
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - {"abs", "sum", "min", "max"}
            if names - idents:
                missing[row["rule_id"]] = names - idents
    assert not missing, missing


_OM_PAYLOAD = [
    {"field_name": "p_and_l_usali.2021.noi_usd", "value": 4_000_000},
    {"field_name": "historical_performance.2022.noi_usd", "value": 4_100_000},
    {"field_name": "broker_proforma.noi_usd", "value": 5_000_000},
    {"field_name": "ttm_performance.segment.occupancy_pct", "value": 0.70},
    {"field_name": "comp_set.occupancy", "value": 0.72},
    {"field_name": "ttm_summary_per_om.occupancy_pct", "value": 0.80},
    {"field_name": "p_and_l_usali.2022.gop_usd", "value": 6_000_000},
    {"field_name": "market_overview_per_om.compset_revpar_usd", "value": 150.0},
]


def test_om_broker_basis_never_returns_history_or_segment_rows() -> None:
    for cid in ("noi", "occupancy", "gop", "revpar", "compset_revpar"):
        res = resolve(_OM_PAYLOAD, cid, doc_type="OM", basis="broker")
        assert res.basis == "broker"
        if res.value is not None:
            assert res.field_name in {"broker_proforma.noi_usd", "ttm_summary_per_om.occupancy_pct"}
        for cand in res.candidates:
            if cand.basis in ("om_history", "market"):
                assert cand.excluded == "basis_excluded"
    gop = resolve(_OM_PAYLOAD, "gop", doc_type="OM", basis="broker")
    assert gop.value is None and gop.reason == "basis_excluded"
    # Without a basis filter registry order governs: the broker's own line
    # is listed before the {year} history pattern.
    assert resolve(_OM_PAYLOAD, "noi", doc_type="OM").field_name == "broker_proforma.noi_usd"


def test_ytd_document_never_serves_an_annual_line() -> None:
    fields = [
        {"field_name": "p_and_l_usali.period_type", "value": "ytd"},
        {"field_name": "p_and_l_usali.gross_operating_profit", "value": 1_000_000},
    ]
    res = resolve(fields, "gop", doc_type="PNL")
    assert res.value is None and res.reason == "period_mismatch"
    assert resolve(fields, "gop", doc_type="PNL", want="ytd").value == 1_000_000.0
    # A PNL_YTD doc type carries the same default even without the line.
    res2 = resolve(fields[1:], "gop", doc_type="PNL_YTD")
    assert res2.value is None and res2.reason == "period_mismatch"


def test_unit_unknown_and_no_source_reasons() -> None:
    assert resolve([], "gop", doc_type="T12").reason == "no_source"
    res = resolve([{"field_name": "p_and_l_usali.gross_operating_profit", "value": "n/a"}], "gop", doc_type="T12")
    assert res.value is None and res.reason == "unit_unknown"
    assert resolve([{"field_name": "gop_usd", "value": "$1,250"}], "gop").value == 1250.0


def test_flat_dict_tenant_aliases_and_token_match() -> None:
    assert resolve({"p_and_l_usali.gop_usd": 42}, "gop", doc_type="PNL").value == 42.0
    custom = [{"field_name": "custom.gop_line", "value": 5}]
    assert resolve(custom, "gop", doc_type="T12").value is None
    hit = resolve(custom, "gop", doc_type="T12", tenant_aliases={"gop": ["custom.gop_line"]})
    assert hit.value == 5.0 and hit.candidates[0].tier == reg_mod.TIER_EXACT_DOC
    odd = [{"field_name": "hotel.gop_total_usd", "value": 7}]
    assert resolve(odd, "gop", doc_type="T12").value is None
    tok = resolve(odd, "gop", doc_type="T12", allow_token_match=True)
    assert tok.value == 7.0 and tok.candidates[0].tier == reg_mod.TIER_TOKEN


def test_resolution_is_deterministic_across_field_order() -> None:
    fields = _anglers_fields()
    a = resolve(fields, "gop", doc_type="T12")
    b = resolve(list(reversed(fields)), "gop", doc_type="T12")
    assert (a.field_name, a.value) == (b.field_name, b.value)


# ─────────────────────────────── identities ───────────────────────────────


def test_identities_evaluate_on_the_real_t12() -> None:
    idents = {i.concept: i for i in identities()}
    assert idents["gop"].expression == "total_revenue - dept_expenses - undistributed_expenses"
    results = {r.concept: r for r in evaluate_identities(_anglers_fields(), doc_type="T12")}
    assert results["gop"].ok is True and results["gop"].drift < 0.005
    assert results["dept_expenses"].ok is True
    assert results["total_revenue"].ok is True  # rooms + fb + other + misc (+ resort 0)
    # fixed_charges resolves to the USALI "Total Non-Operating Income and
    # Expenses" row, so the NOI chain closes: gop - mgmt - ffe - fixed.
    assert results["fixed_charges"].stated == 1922240.0
    assert results["noi"].ok is True and results["noi"].drift < 0.005
    assert results["income_before_nonop"].ok is True
    assert results["ebitda"].ok is True
    assert results["dept_profit"].ok is True


def test_corpus_enum_additions_weekly_scope_and_plan_bases() -> None:
    # STAR weekly report: served only when the weekly scope is asked for.
    weekly = [{"field_name": "ttm_performance.subject.weekly.2025_w18.occupancy_pct", "value": 0.9}]
    assert resolve(weekly, "occupancy", doc_type="STR_TREND").reason == "period_mismatch"
    wk = resolve(weekly, "occupancy", doc_type="STR_TREND", want="weekly")
    assert wk.value == 0.9 and wk.scope == "weekly" and wk.basis == "market"
    # Owner budget / plan / adjusted blocks carry a basis, not a period slice.
    fields = [
        {"field_name": "p_and_l_usali.budget.insurance_usd", "value": 1_000_000},
        {"field_name": "p_and_l_usali.adjusted.insurance_usd", "value": 1_161_390},
        {"field_name": "p_and_l_usali.forecast.insurance_usd", "value": 1_500_000},
        {"field_name": "p_and_l_usali.non_operating.insurance_usd", "value": 1_392_610},
    ]
    assert resolve(fields, "insurance", doc_type="T12", basis="actual").value == 1_392_610.0
    assert resolve(fields, "insurance", doc_type="T12", basis="budget").value == 1_000_000.0
    assert resolve(fields, "insurance", doc_type="T12", basis="adjusted").value == 1_161_390.0
    assert resolve(fields, "insurance", doc_type="T12", basis="plan").value == 1_500_000.0
    assert concept_for_path("p_and_l_usali.budget.insurance_usd", doc_type="T12") == ("insurance", "budget", "unknown")
    # Point facts carry no scope; the note says why.
    keys = get_registry().concepts["keys"]
    assert keys.default_scope is None and keys.period == "point" and keys.note


def test_om_ttm_block_is_the_brokers_annual_column_not_a_ttm() -> None:
    om = [{"field_name": "ttm_summary_per_om.occupancy_pct", "value": 0.831}]
    res = resolve(om, "occupancy", doc_type="OM")
    assert (res.value, res.basis, res.scope) == (0.831, "broker", "annual")
    assert concept_for_path("ttm_summary_per_om.occupancy_pct", doc_type="OM") == ("occupancy", "broker", "annual")
    # On a T-12 the same path is an actual and takes the document's period.
    assert concept_for_path("ttm_summary_per_om.occupancy_pct", doc_type="T12") == ("occupancy", "actual", "unknown")
    t12 = resolve(om, "occupancy", doc_type="T12")
    assert (t12.basis, t12.scope) == ("actual", "ttm")
    # A month label never becomes a period: it only marks a monthly slice.
    assert concept_for_path("p_and_l_usali.monthly.jan_2024.rooms_revenue_usd", doc_type="T12") == ("rooms_revenue", "actual", "monthly")


# ─────────────────────────────── absorption ───────────────────────────────


def test_period_types_match_field_catalog_and_engines_are_known() -> None:
    from app.extraction.field_catalog import PERIOD_TYPE_RANK
    from app.services.engine_runner import ENGINE_NAMES

    assert get_registry().period_types == PERIOD_TYPE_RANK
    assert set(ENGINE_NAMES) <= reg_mod._known_engines()


def test_field_catalog_aliases_round_trip() -> None:
    from app.extraction import field_catalog as fc

    reg = get_registry()
    for namespace, aliases, dt in (
        ("t12_expense", fc.T12_EXPENSE_FIELD_ALIASES, "T12"),
        ("t12_revenue", fc.T12_REVENUE_FIELD_ALIASES, "T12"),
        ("om_capital", fc.OM_CAPITAL_FIELD_ALIASES, "OM"),
        ("om_debt", fc.OM_DEBT_FIELD_ALIASES, "OM"),
    ):
        for alias, canonical in aliases.items():
            hit = concept_for_path(alias, doc_type=dt)
            assert hit is not None, (namespace, alias)
            binding = reg.concepts[hit[0]].bindings.field_catalog
            assert binding is not None and (binding.namespace, binding.key) == (namespace, canonical), (alias, hit)
    for key in fc.OM_PERCENTAGE_KEYS:
        owner = next(c for c in reg.concepts.values() if c.bindings.field_catalog and c.bindings.field_catalog.key == key)
        assert owner.bindings.field_catalog.percentage_key is True


def test_usali_scorer_aliases_round_trip() -> None:
    from app.services.usali_scorer import _ALIASES

    reg = get_registry()
    for canonical, aliases in _ALIASES.items():
        for alias in (canonical, *aliases):
            hit = concept_for_path(alias)
            assert hit is not None, (canonical, alias)
            assert canonical in reg.concepts[hit[0]].bindings.scorer_identifiers(), (canonical, alias, hit)


def test_variance_and_analysis_maps_round_trip() -> None:
    from app.agents.variance import _BROKER_RULE_BY_FIELD
    from app.api.analysis import _VARIANCE_CONCEPTS

    reg = get_registry()
    for key, rule in _BROKER_RULE_BY_FIELD.items():
        hit = concept_for_path(key, doc_type="OM")
        assert hit is not None, key
        assert reg.concepts[hit[0]].bindings.variance_rule == rule, (key, hit)
    for key, (label, basis) in _VARIANCE_CONCEPTS.items():
        owner = [c for c in reg.concepts.values() if c.bindings.variance_concept and c.bindings.variance_concept.key == key]
        assert len(owner) == 1, key
        assert (owner[0].bindings.variance_concept.label, owner[0].bindings.variance_concept.impact_basis) == (label, basis)


def test_basis_predicates_mirror_variance_agent_and_subordinate_filter_mirrors_scorer() -> None:
    from app.agents import variance
    from app.services.usali_scorer import _has_subordinate_namespace

    paths = [
        "p_and_l_usali.2021.gop_usd", "historical_performance.2022.noi", "historical.x", "p_and_l_usali.gop",
        "ttm_performance.segment.adr", "segment.adr", "market.revpar", "comp_set.adr", "compset.adr_usd",
        "broker_proforma.noi_usd", "p_and_l_usali.monthly.jan.gop", "p_and_l_usali.page5.insurance_usd",
        "p_and_l_usali.q1.gop", "p_and_l_usali.quarterly.q2.gop", "p_and_l_usali.per_month.jan.gop",
        "p_and_l_usali.2024_05.gop", "ttm_summary_per_om.gop_usd",
    ]
    sub = get_registry()._subordinate
    for p in paths:
        assert reg_mod.is_om_historical_year(p) == variance.is_om_historical_year(p), p
        assert reg_mod.is_market_segment(p) == variance.is_market_segment(p), p
        if _has_subordinate_namespace(p):
            assert reg_mod._subordinate_scope(p.lower(), sub)[0] is True, p
        if variance.is_period_slice(p):
            assert reg_mod._subordinate_scope(p.lower(), sub)[0] is True, p


# ─────────────────────────────── boot + endpoint ──────────────────────────


@pytest.mark.asyncio
async def test_health_reports_ontology_version() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/health")
        body = r.json()
        assert body["ontology_version"] == registry_version()
        assert "ontology_invalid" not in body["degraded_reasons"]


@pytest.mark.asyncio
async def test_health_flags_invalid_registry() -> None:
    from httpx import ASGITransport, AsyncClient

    from app import main as main_mod

    saved = copy.deepcopy(main_mod._STARTUP_STATE)
    try:
        main_mod._STARTUP_STATE["ontology_version"] = -1
        async with AsyncClient(transport=ASGITransport(app=main_mod.app), base_url="http://test") as client:
            body = (await client.get("/health")).json()
            assert body["ontology_version"] == -1
            assert "ontology_invalid" in body["degraded_reasons"]
            assert body["status"] == "degraded"
    finally:
        main_mod._STARTUP_STATE.clear()
        main_mod._STARTUP_STATE.update(saved)


@pytest.mark.asyncio
async def test_get_ontology_concepts_is_public_and_serves_the_registry() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/ontology/concepts")  # no tenant header, no auth
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == registry_version()
        assert body["concepts"]["gop"]["id"] == "gop"
        assert body["concepts"]["gop"]["bindings"]["scorer_key"] == "gop"
        assert body["concepts"]["gop"]["label"] == "Gross Operating Profit"
        assert set(body["reasons"]) == set(REASON_CODES)
        assert "t12_actual" in body["sources"]
        assert body == get_registry().dump()


# ─────────────────────────────── codegen ──────────────────────────────────


def test_codegen_is_up_to_date_and_deterministic() -> None:
    proc = subprocess.run(
        [sys.executable, str(_WORKER_ROOT / "scripts" / "gen_ontology.py"), "--check"],
        cwd=_WORKER_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_ontology", _WORKER_ROOT / "scripts" / "gen_ontology.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    first, second = mod.render_all(), mod.render_all()
    assert first == second
    reasons_ts = first[mod.WEB_ONTOLOGY_DIR / "reasons.generated.ts"]
    assert "export const REASONS" in reasons_ts
    for code in REASON_CODES:
        assert f'"{code}"' in reasons_ts


def test_reasons_block_matches_schema_reason_meta():
    """The registry's `reasons` block must mirror fondok_schemas.reasons.REASON_META
    byte for byte — Phase 0 owns that vocabulary; the YAML only redistributes it."""
    import yaml
    from pathlib import Path
    from fondok_schemas.reasons import REASON_META, ReasonCode

    data = yaml.safe_load(Path("app/ontology/concepts.yaml").read_text())
    got = data["reasons"]
    assert set(got) == {c.value for c in ReasonCode}
    for code in ReasonCode:
        for key in ("label", "ui", "explanation"):
            assert got[code.value][key] == REASON_META[code][key], (code, key)
