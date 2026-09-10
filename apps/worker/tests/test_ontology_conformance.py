"""Ontology conformance — the gate the registry adapters land against.

Two things live here:

1. ``test_field_catalog_parity`` — the six constants
   ``app/extraction/field_catalog.py`` exports are now DERIVED from the concept
   registry. This pins them against
   ``tests/fixtures/ontology/field_catalog_pre_registry.json``, a snapshot
   taken from the pre-adapter loader (which read ``field_catalog.yaml``
   directly). Phase 1.3a is a parity phase: the engines must match exactly the
   paths they matched before, in the same order. A diff here is a real
   underwriting-behaviour change, never a test to update.

2. ``test_registry_literals_are_confined_to_the_registry`` — a grep gate. Once
   a consumer is registry-driven it has no business naming an extractor path
   itself. ``ENFORCED`` files must be clean; ``PENDING`` files are the ones the
   other adapter builders still own, marked ``xfail(strict=True)`` so the day
   one of them is cleaned the test FAILS with an XPASS and tells its owner to
   move the entry into ``ENFORCED``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN (pattern: test_ontology_registry).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-conformance.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.extraction import field_catalog as fc  # noqa: E402
from app.ontology.registry import get_registry  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = Path(__file__).parent / "fixtures" / "ontology" / "field_catalog_pre_registry.json"

_ALIAS_DICTS = (
    "T12_EXPENSE_FIELD_ALIASES",
    "T12_REVENUE_FIELD_ALIASES",
    "OM_CAPITAL_FIELD_ALIASES",
    "OM_DEBT_FIELD_ALIASES",
)
_NAMESPACES = ("t12_expense", "t12_revenue", "om_capital", "om_debt")


def _pinned() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# ───────────────────────────── 1. field_catalog parity ─────────────────────


def test_field_catalog_parity() -> None:
    """The six registry-derived constants == the pre-registry snapshot."""
    pinned = _pinned()
    constants = pinned["constants"]

    # (a) the flattened alias→canonical maps, key for key.
    for name in _ALIAS_DICTS:
        got = dict(sorted(getattr(fc, name).items()))
        want = constants[name]
        assert got == want, (
            f"{name} drifted from the pre-registry snapshot. "
            f"only-now={sorted(set(got) - set(want))} "
            f"only-before={sorted(set(want) - set(got))} "
            f"remapped={{k: (want[k], got[k]) for k in set(got) & set(want) if want[k] != got[k]}}"
        )

    # (b) period ranks — value AND order (engine_runner ranks P&L rows by these).
    assert dict(sorted(fc.PERIOD_TYPE_RANK.items())) == constants["PERIOD_TYPE_RANK"]
    assert list(fc.PERIOD_TYPE_RANK) == pinned["insertion_order"]["PERIOD_TYPE_RANK"]
    assert dict(get_registry().period_types) == dict(fc.PERIOD_TYPE_RANK)

    # (c) percentage keys.
    assert sorted(fc.OM_PERCENTAGE_KEYS) == constants["OM_PERCENTAGE_KEYS"]

    # (d) the per-canonical alias LISTS, in order. This is the part that
    #     matters for precedence: the flat dicts above are order-insensitive
    #     (every alias is unique — ``_invert_aliases`` raises otherwise), the
    #     lists are not.
    for namespace in _NAMESPACES:
        got_ns = fc.ALIAS_LISTS_BY_NAMESPACE[namespace]
        want_ns = {
            key: [str(a).strip().lower() for a in aliases]
            for key, aliases in pinned["source_lists"][namespace].items()
        }
        assert sorted(got_ns) == sorted(want_ns), (
            f"{namespace}: canonical keys drifted. "
            f"only-now={sorted(set(got_ns) - set(want_ns))} "
            f"only-before={sorted(set(want_ns) - set(got_ns))}"
        )
        for key, want_aliases in want_ns.items():
            assert got_ns[key] == want_aliases, (
                f"{namespace}.{key}: alias order/content changed. "
                f"Fix concepts.yaml (the registry), not this test."
            )


def test_field_catalog_scope_gate_is_a_registry_subset() -> None:
    """Phase-1.3a scope gate: what the engines see ⊂ what the registry knows.

    Documents the Phase 1.3a parity exception recorded in ``DRIFT_NOTES.md``:
    the registry carries strictly MORE aliases per canonical key than
    ``field_catalog.yaml`` ever listed, and the loader deliberately narrows to
    the frozen set so no engine starts matching a new path in a parity phase.
    When the widening lands this test flips to an equality assertion (and the
    YAML + ``_load_scope`` delete).
    """
    gated = fc.ALIAS_LISTS_BY_NAMESPACE
    full = fc._REGISTRY_ALIAS_LISTS
    widened = 0
    for namespace in _NAMESPACES:
        assert set(gated[namespace]) <= set(full[namespace])
        for key, paths in gated[namespace].items():
            known = full[namespace][key]
            # Every gated path is a registry alias, in registry order.
            assert paths == [p for p in known if p in set(paths)], (namespace, key)
            widened += len(known) - len(paths)
    assert widened > 0, (
        "the registry no longer knows more aliases than the scope gate allows — "
        "delete field_catalog.yaml, _load_scope and _gate, and turn this test "
        "into an equality assertion."
    )


def test_field_catalog_bindings_cover_every_engine_key() -> None:
    """Every canonical key the engines consume is owned by exactly one concept."""
    reg = get_registry()
    owners: dict[tuple[str, str], list[str]] = {}
    for cid, concept in reg.concepts.items():
        binding = concept.bindings.field_catalog
        if binding is not None:
            owners.setdefault((binding.namespace, binding.key), []).append(cid)
    assert all(len(v) == 1 for v in owners.values()), {
        k: v for k, v in owners.items() if len(v) > 1
    }
    for namespace in _NAMESPACES:
        for key in fc.ALIAS_LISTS_BY_NAMESPACE[namespace]:
            assert (namespace, key) in owners, (namespace, key)


# ───────────────────────── 2. registry-literal conformance ─────────────────

#: Extractor path prefixes that belong in ``app/ontology/concepts.yaml`` and
#: nowhere else. A consumer that still spells one of these is not yet
#: registry-driven.
_LITERAL_RE = re.compile(
    r"\b(?:p_and_l_usali|ttm_summary_per_om|broker_proforma|ttm_performance)\."
)

#: Clean today — a regression here fails the build.
ENFORCED: tuple[str, ...] = ("apps/worker/app/extraction/field_catalog.py",)

#: Still owned by another adapter builder. ``xfail(strict=True)``: when the
#: owner lands their adapter the file goes clean, the test XPASSes, and pytest
#: fails with the reason below — which tells them to move the entry up.
PENDING: dict[str, str] = {
    "apps/worker/app/services/usali_scorer.py": (
        "usali_scorer adapter not landed yet (_ALIASES still spells extractor "
        "paths). When it does: move this path into ENFORCED."
    ),
    "apps/worker/app/agents/variance.py": (
        "variance adapter not landed yet (_BROKER_RULE_BY_FIELD / "
        "BROKER_CLAIM_PREFIXES still spell extractor paths). When it does: "
        "move this path into ENFORCED."
    ),
    "apps/worker/app/api/analysis.py": (
        "analysis adapter not landed yet (_VARIANCE_CONCEPTS / the "
        "growth-vs-market flags still spell extractor paths). When it does: "
        "move this path into ENFORCED."
    ),
    "apps/worker/app/api/documents.py": (
        "Phase 1.3c LANDED: _load_critic_inputs is registry-driven and its "
        "region carries no extractor literals (test_ontology_critic_parity "
        "asserts that over inspect.getsource). The remaining hits are OTHER "
        "regions -- extraction period / doc-type verification and the STR / "
        "comp-set block builders, lines 460, 985, 1045, 2731-2733, 2800-2805, "
        "5486, 5515 -- which are not ontology aliases and have no adapter "
        "planned. This entry stays xfail until those are registry-driven or "
        "explicitly allowlisted; it is NOT a signal that 1.3c is outstanding."
    ),
    "apps/web/src/components/project/pl/HistoricalsSection.tsx": (
        "web historicals adapter not landed yet (buildHistYear still spells "
        "extractor paths). When it does: move this path into ENFORCED."
    ),
    "apps/worker/app/extraction/field_catalog.yaml": (
        "Phase-1.3a scope gate — this file is the only remaining reason "
        "field_catalog.py is not fully registry-vocabulary. It goes away with "
        "_load_scope / _gate when the alias widening lands (see "
        "test_field_catalog_scope_gate_is_a_registry_subset). When it does: "
        "delete this entry."
    ),
}


def _offending_lines(rel: str) -> list[str]:
    path = _REPO_ROOT / rel
    if not path.exists():
        # Deleted files carry no literals. A PENDING entry then XPASSes, which
        # is exactly the signal its owner needs.
        return []
    hits: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _LITERAL_RE.search(line):
            hits.append(f"{rel}:{lineno}: {line.strip()[:110]}")
    return hits


@pytest.mark.parametrize(
    "rel",
    [
        *ENFORCED,
        *(
            pytest.param(rel, marks=pytest.mark.xfail(strict=True, reason=reason))
            for rel, reason in PENDING.items()
        ),
    ],
)
def test_registry_literals_are_confined_to_the_registry(rel: str) -> None:
    if rel in ENFORCED and not (_REPO_ROOT / rel).exists():
        pytest.fail(
            f"{rel} is in ENFORCED but does not exist — the conformance gate "
            f"must name a real file (repo root resolved to {_REPO_ROOT})."
        )
    hits = _offending_lines(rel)
    assert not hits, (
        f"{rel} spells extractor paths that belong to app/ontology/concepts.yaml "
        f"({len(hits)} line(s)). Resolve them through app.ontology.registry "
        f"instead.\n" + "\n".join(hits[:20])
    )
