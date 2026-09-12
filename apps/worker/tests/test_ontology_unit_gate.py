"""FON-54 §2 / §8 — the resolver refuses a unit it cannot reconcile.

``registry.resolve`` matches a path to a concept through six tiers; tier 4
(:data:`registry.TIER_STRIPPED`) removes the unit suffix so a ``_usd`` or bare
sibling finds the same alias. That tier is what let
``broker_proforma.rooms_revenue_pct`` — an OM's %-of-revenue proforma column —
match the DOLLAR alias ``broker_proforma.rooms_revenue_usd`` and be read as
``$1`` against a $9,332,100 T-12 line.

The gate added here does not change which tier matches; it checks, after a
match, that the unit family the PATH declares is the family the CONCEPT
carries. A mismatch is refused as ``unit_unknown`` — never reinterpreted, and
never reconstructed into dollars (the denominator would itself be an
unverified broker claim).

A path that declares NO unit stays permissive: that is the behaviour every
bare alias in ``concepts.yaml`` relies on and it must not regress.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


def _ef(name: str, value):
    from fondok_schemas import ExtractionField

    return ExtractionField(field_name=name, value=value, source_page=1, confidence=0.9)


# ═══════════════════ the primitive ═══════════════════


@pytest.mark.parametrize(
    ("path", "family"),
    [
        ("broker_proforma.rooms_revenue_pct", "pct"),
        ("broker_proforma.rooms_revenue_percent", "pct"),
        ("opex_ratio", "pct"),
        ("broker_proforma.rooms_revenue_usd", "usd"),
        ("senior_loan_amount", "usd"),
        ("p_and_l_usali.noi_per_key_usd", "usd_per_key"),
        ("comparable_sales.3.sale_price_per_key_usd", "usd_per_key"),
        # Declares nothing — the common case for a bare alias.
        ("broker_proforma.rooms_revenue", None),
        ("p_and_l_usali.operating_revenue.rooms_revenue", None),
        ("occupancy", None),
    ],
)
def test_path_unit_family_reads_only_what_the_path_declares(path: str, family) -> None:
    from app.ontology.registry import path_unit_family

    assert path_unit_family(path) == family


def test_units_compatible_is_permissive_unless_the_path_disagrees() -> None:
    from app.ontology.registry import units_compatible

    # Declared and matching.
    assert units_compatible("broker_proforma.rooms_revenue_usd", "usd")
    assert units_compatible("broker_proforma.occupancy_pct", "ratio")
    assert units_compatible("p_and_l_usali.noi_per_key_usd", "usd_per_key")
    # Declared and disagreeing.
    assert not units_compatible("broker_proforma.rooms_revenue_pct", "usd")
    assert not units_compatible("broker_proforma.mgmt_fee_ratio", "usd")
    assert not units_compatible("broker_proforma.occupancy_usd", "ratio")
    assert not units_compatible("p_and_l_usali.noi_per_key_usd", "usd")
    # Declares nothing — permissive, whatever the concept's unit.
    for unit in ("usd", "ratio", "count", "years", "date", "text", "usd_per_key"):
        assert units_compatible("broker_proforma.rooms_revenue", unit), unit
    # A unit the family map does not classify stays permissive rather than
    # refusing every path that names a suffix.
    assert units_compatible("broker_proforma.rooms_revenue_usd", "furlongs")


def test_every_alias_in_the_registry_is_compatible_with_its_own_concept() -> None:
    """The gate must refuse nothing ``concepts.yaml`` deliberately declares."""
    from app.ontology.registry import get_registry, units_compatible

    offenders = [
        (cid, concept.unit, alias.path)
        for cid, concept in get_registry().concepts.items()
        for aliases in concept.aliases.values()
        for alias in aliases
        if not units_compatible(alias.path, concept.unit)
    ]
    assert offenders == []


# ═══════════════════ enforced inside resolve() ═══════════════════


def test_resolve_refuses_a_unit_incompatible_candidate_with_unit_unknown() -> None:
    from app.ontology.registry import resolve

    r = resolve(
        [_ef("broker_proforma.rooms_revenue_pct", 1.0)], "rooms_revenue", doc_type="OM"
    )
    assert r.value is None
    assert r.reason == "unit_unknown"
    # The candidate is still disclosed — it matched, it was refused.
    assert [(c.field_name, c.excluded) for c in r.candidates] == [
        ("broker_proforma.rooms_revenue_pct", "unit_unknown")
    ]


def test_resolve_still_takes_the_dollar_sibling_on_the_same_concept() -> None:
    """Guards against over-refusal — only the percent may be rejected."""
    from app.ontology.registry import resolve

    r = resolve(
        [
            _ef("broker_proforma.rooms_revenue_pct", 1.0),
            _ef("broker_proforma.rooms_revenue_usd", 9_708_984),
        ],
        "rooms_revenue",
        doc_type="OM",
    )
    assert r.value == 9_708_984
    assert r.field_name == "broker_proforma.rooms_revenue_usd"
    assert r.reason is None


def test_a_path_declaring_no_unit_still_resolves() -> None:
    from app.ontology.registry import resolve

    r = resolve(
        [_ef("p_and_l_usali.operating_revenue.rooms_revenue", 9_332_100)],
        "rooms_revenue",
        doc_type="T12",
        want="ttm",
    )
    assert r.value == 9_332_100
    assert r.reason is None


def test_a_per_key_dollar_alias_is_not_mistaken_for_a_dollar_total() -> None:
    """``noi_per_key_usd`` belongs to ``noi_per_key``, never to ``noi``."""
    from app.ontology.registry import resolve

    per_key = resolve(
        [_ef("p_and_l_usali.noi_per_key_usd", 31_674)], "noi_per_key", doc_type="T12"
    )
    assert per_key.value == 31_674 and per_key.reason is None


def test_the_ratio_concept_accepts_its_pct_suffix() -> None:
    from app.ontology.registry import resolve

    r = resolve([_ef("broker_proforma.occupancy_pct", 0.83)], "occupancy", doc_type="OM")
    assert r.value == pytest.approx(0.83)
    assert r.reason is None
