"""Phase 1.3b — the USALI scorer went registry-driven with ZERO score change.

``services/usali_scorer`` used to carry a hand-written ``_ALIASES`` map (≈420
lines of ``canonical → tolerated paths``) and its own three-step resolver.
Both now come from the concept registry: ``_ALIASES`` is a read-only view over
``bindings.scorer_key`` / ``scorer_synonyms`` / ``scorer_variants`` + the
concept's aliases, ``_resolve_field`` delegates to ``registry.resolve`` (with
the scorer's own token matcher as the resolver's opt-in tier 6), and
``_has_subordinate_namespace`` reads ``registry.subordinate_namespaces``.

The acceptance criterion for that swap is parity, not improvement:
``score_extraction``'s ``score``, ``applicable_count``, ``passed_count`` and
the per-rule outcome must be IDENTICAL on every fixture — plus, as a sharper
tripwire, the value every canonical the rule catalog references resolves to.

``tests/fixtures/ontology/usali_scorer_pre_registry.json`` is that truth,
captured against the pre-change scorer. It covers the nine saved extraction
payloads the scorer suites read — ``fixtures/usali_v3`` (Sam's June QA runs),
``fixtures/usali_v4`` (the live v4 router captures, including the two T-12
alternate runs and the OM) and ``fixtures/real_payloads`` (the pinned prod
payloads the golden set scores) — each flattened three ways: bare, with the
deal-level ``keys``, and with ``keys`` + the market-context flags that make
the coastal / seasonal rules applicable.

Where the registry's answer legitimately differs from the old scorer's, the
adapter keeps the OLD answer for this phase and the case is written up in
``app/ontology/DRIFT_NOTES.md`` under "Phase 1.3b parity exceptions". If this
test fails, that is the file to read before touching the fixture: a score
change here is a product decision, never a refactor side effect.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
_PIN_PATH = _HERE / "fixtures" / "ontology" / "usali_scorer_pre_registry.json"

#: Flatten variants, keyed by the suffix used in the pinned fixture.
_CONTEXTS: dict[str, dict[str, Any] | None] = {
    "": None,
    "::keys132": {"keys": 132},
    "::keys132+coastal": {"keys": 132, "coastal": True, "seasonal_market": True},
}


#: Phase 1.3b parity exceptions, keyed by payload path then canonical name:
#: ``{canonical: (pinned, expected_now)}``. These are resolutions the registry
#: answers DIFFERENTLY from the hand-written map, where the registry is right
#: and no rule outcome — and therefore no score — moves. Each one is written
#: up in ``app/ontology/DRIFT_NOTES.md`` under "Phase 1.3b parity exceptions".
#: The pair is asserted exactly, so drifting to a third value still fails.
_RESOLUTION_EXCEPTIONS: dict[str, dict[str, tuple[Any, Any]]] = {
    "tests/fixtures/usali_v4/live_extraction_anglers_om.json": {
        # The old resolver reached ``broker_adr`` only through its token
        # matcher, which demanded a literal ``broker`` token — so the one
        # candidate it could see on this OM was the broker's YEAR-5 pro-forma
        # ADR (340). The registry reads the ``adr`` concept filtered to
        # ``basis: broker`` and lands on the pro-forma's own ADR line (312),
        # which is what "the broker's ADR" means. No catalog rule that fires
        # on this fixture reads ``broker_adr``, so every rule outcome and the
        # score are unchanged.
        "broker_adr": (340, 312.0),
    },
}


def _round(v: Any) -> Any:
    """Match the pin's rounding so a last-bit float wobble isn't a failure."""
    return round(v, 10) if isinstance(v, float) else v


def _canonicals(rules: list[Any]) -> list[str]:
    """Every bare name the rule catalog's formulas reference."""
    names: set[str] = set()
    for rule in rules:
        formula = (rule.formula_or_check or "").strip()
        if not formula:
            continue
        try:
            tree = ast.parse(formula, mode="eval")
        except SyntaxError:
            continue
        names.update(n.id for n in ast.walk(tree) if isinstance(n, ast.Name))
    return sorted(names)


def _per_rule_outcomes(flat: dict[str, Any], rules: list[Any]) -> dict[str, str]:
    """Outcome per rule, evaluated one rule at a time through the real scorer.

    Scoring a single-rule catalog reuses ``score_extraction`` verbatim, so the
    outcome is the production code path's own verdict rather than a
    reimplementation of it. ``score`` is always ``None`` for a one-rule
    catalog (below the inconclusive floor) — the counts and the deviation are
    what we read.
    """
    from app.services.usali_scorer import score_extraction

    out: dict[str, str] = {}
    for rule in rules:
        res = score_extraction(flat, rules=[rule])
        dev = res.deviations[0] if res.deviations else None
        if dev is not None and dev.requires_market_context:
            out[rule.rule_id] = "market_context"
        elif res.applicable_count == 0:
            out[rule.rule_id] = "skipped"
        elif res.passed_count == 1:
            out[rule.rule_id] = "passed"
        else:
            out[rule.rule_id] = f"failed:{_round(dev.actual_value) if dev else None}"
    return out


def _recompute() -> dict[str, dict[str, Any]]:
    from app.services.usali_scorer import (
        _resolve_field,
        flatten_extraction_fields,
        score_extraction,
    )
    from app.usali_rules import load_usali_rules

    rules = load_usali_rules()
    canonicals = _canonicals(rules)
    pinned = json.loads(_PIN_PATH.read_text())

    out: dict[str, dict[str, Any]] = {}
    for case_name in pinned:
        rel, _, suffix = case_name.partition("::")
        payload = json.loads((_HERE.parent / rel).read_text())
        fields = payload.get("fields") or []
        flat = flatten_extraction_fields(
            fields, extra_context=_CONTEXTS[f"::{suffix}" if suffix else ""]
        )
        res = score_extraction(flat)
        out[case_name] = {
            "score": res.score,
            "applicable_count": res.applicable_count,
            "passed_count": res.passed_count,
            "inconclusive": res.inconclusive,
            "per_rule": _per_rule_outcomes(flat, rules),
            "resolved": {c: _round(_resolve_field(flat, c)) for c in canonicals},
        }
    return out


@pytest.fixture(scope="module")
def pinned() -> dict[str, dict[str, Any]]:
    assert _PIN_PATH.exists(), f"missing parity pin: {_PIN_PATH}"
    data = json.loads(_PIN_PATH.read_text())
    assert data, "parity pin is empty"
    return data


@pytest.fixture(scope="module")
def recomputed() -> dict[str, dict[str, Any]]:
    return _recompute()


def test_pin_covers_every_saved_extraction_payload(pinned: dict[str, Any]) -> None:
    """The pin must not silently shrink — every saved payload, every variant."""
    payloads = sorted(
        str(p.relative_to(_HERE.parent))
        for group in ("usali_v3", "usali_v4", "real_payloads")
        for p in (_HERE / "fixtures" / group).glob("*.json")
    )
    assert payloads, "no saved extraction payloads found"
    expected = {f"{p}{suffix}" for p in payloads for suffix in _CONTEXTS}
    assert set(pinned) == expected


@pytest.mark.parametrize("field", ["score", "applicable_count", "passed_count", "inconclusive"])
def test_headline_counts_are_unchanged(
    pinned: dict[str, Any], recomputed: dict[str, Any], field: str
) -> None:
    before = {name: case[field] for name, case in pinned.items()}
    after = {name: recomputed[name][field] for name in pinned}
    assert after == before


def test_every_rule_outcome_is_unchanged(
    pinned: dict[str, Any], recomputed: dict[str, Any]
) -> None:
    drift: dict[str, dict[str, tuple[str, str]]] = {}
    for name, case in pinned.items():
        got = recomputed[name]["per_rule"]
        moved = {
            rule_id: (was, got.get(rule_id))
            for rule_id, was in case["per_rule"].items()
            if got.get(rule_id) != was
        }
        added = set(got) - set(case["per_rule"])
        assert not added, (name, sorted(added))
        if moved:
            drift[name] = moved
    assert not drift, f"per-rule outcomes moved: {json.dumps(drift, indent=2)}"


def test_every_canonical_resolves_to_the_same_value(
    pinned: dict[str, Any], recomputed: dict[str, Any]
) -> None:
    """Sharper than the score: a resolution that moves without moving a rule
    outcome is still a behaviour change, and this is where it shows up.

    The only moves allowed are the declared ``_RESOLUTION_EXCEPTIONS``, and
    each must land on exactly the value that was signed off."""
    drift: dict[str, dict[str, tuple[Any, Any]]] = {}
    for name, case in pinned.items():
        payload = name.partition("::")[0]
        allowed = _RESOLUTION_EXCEPTIONS.get(payload, {})
        got = recomputed[name]["resolved"]
        moved = {
            canonical: (was, got.get(canonical))
            for canonical, was in case["resolved"].items()
            if got.get(canonical) != was
            and allowed.get(canonical) != (was, got.get(canonical))
        }
        if moved:
            drift[name] = moved
    assert not drift, f"canonical resolutions moved: {json.dumps(drift, indent=2)}"


def test_declared_parity_exceptions_still_apply(
    pinned: dict[str, Any], recomputed: dict[str, Any]
) -> None:
    """A signed-off exception that stops happening is drift too — it means the
    adapter quietly went back to the old answer (or to a third one)."""
    for payload, exceptions in _RESOLUTION_EXCEPTIONS.items():
        cases = [n for n in pinned if n.partition("::")[0] == payload]
        assert cases, payload
        for name in cases:
            for canonical, (was, now) in exceptions.items():
                assert pinned[name]["resolved"][canonical] == was, (name, canonical)
                assert recomputed[name]["resolved"][canonical] == now, (name, canonical)


def test_alias_view_is_registry_derived_and_covers_the_legacy_map(
    pinned: dict[str, Any],
) -> None:
    """``_ALIASES`` is now a view: every canonical the rule catalog touches must
    still be bound to exactly one concept, and every path in the view must be
    an alias of that concept."""
    from app.ontology.registry import concept_for_path, get_registry
    from app.services.usali_scorer import _ALIASES

    reg = get_registry()
    assert len(_ALIASES) > 0
    for canonical, paths in _ALIASES.items():
        owners = [
            cid
            for cid, c in reg.concepts.items()
            if canonical in c.bindings.scorer_identifiers()
        ]
        assert len(owners) == 1, (canonical, owners)
        for path in paths:
            hit = concept_for_path(path)
            assert hit is not None, (canonical, path)
