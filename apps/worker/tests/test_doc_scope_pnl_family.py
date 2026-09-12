"""``_doc_scope`` for the P&L family — the contract the web mirrors (FON-41 #4).

Financials → Historicals now states each column's period basis (FY / YTD /
T12) instead of rendering a generic year. It resolves that basis in
``apps/web/src/components/project/pl/HistoricalsSection.tsx``
(``derivePeriodBasis``) as a MIRROR of this resolver: the document's own
``period_type`` through the registry's ``period_types`` rank map, then the
doc-type default.

This suite pins the worker half so the mirror cannot drift silently:

    rank 0 → annual (FY)   rank 1 → ttm (T12)   rank 5 → ytd (YTD)
    rank 7 → quarterly     anything else → monthly

    default: T12 → ttm, PNL → annual, PNL_YTD → ytd, PNL_MONTHLY → monthly

No model value depends on this test; it is a vocabulary lock.

FON-44 §4 added a second consumer of the same resolver: the historical
baseline's YoY walk asks it what period each year's statement covers before
it divides one year by another (``engines/historical_baseline._period_basis``
→ ``HistoricalYear.period_basis`` / ``is_partial``). The last block below
pins that projection, so the walk, the worksheet column header and the web
mirror cannot come to three different answers about one document.
"""

from __future__ import annotations

import pytest

from app.engines.historical_baseline import _period_basis
from app.ontology.registry import _as_fields, _doc_scope, get_registry


def scope_of(doc_type: str | None, period_type: str | None = None) -> str:
    fields = _as_fields(
        {"p_and_l_usali.period_type": period_type} if period_type is not None else {}
    )
    return _doc_scope(fields, doc_type, get_registry())


def basis_of(
    doc_type: str | None, period_type: str | None = None
) -> tuple[str, bool]:
    """``(period_basis, is_partial)`` as the historical baseline reads it."""
    fields = (
        {"p_and_l_usali.period_type": period_type} if period_type is not None else {}
    )
    return _period_basis(fields, doc_type)


@pytest.mark.parametrize(
    ("doc_type", "expected"),
    [
        ("T12", "ttm"),
        ("PNL", "annual"),
        ("PNL_YTD", "ytd"),
        ("PNL_MONTHLY", "monthly"),
    ],
)
def test_doc_type_default_scope(doc_type: str, expected: str) -> None:
    """A statement with no ``period_type`` falls back to its classification."""
    assert scope_of(doc_type) == expected


@pytest.mark.parametrize(
    ("period_type", "expected"),
    [
        ("annual", "annual"),
        ("fiscal_year", "annual"),
        ("full_year", "annual"),
        ("ttm", "ttm"),
        ("t12", "ttm"),
        ("trailing_twelve", "ttm"),
        ("rolling_twelve", "ttm"),
        ("ytd", "ytd"),
        ("year_to_date", "ytd"),
        ("quarterly", "quarterly"),
        ("quarter", "quarterly"),
        ("monthly", "monthly"),
        ("month", "monthly"),
    ],
)
def test_stated_period_type_wins_over_the_doc_default(
    period_type: str, expected: str
) -> None:
    """A stated ``period_type`` beats the classification, for every doc type."""
    for doc_type in ("PNL", "T12", "PNL_YTD", "PNL_MONTHLY"):
        assert scope_of(doc_type, period_type) == expected


def test_ytd_is_never_reported_as_a_trailing_twelve() -> None:
    """The regression the web mirror exists to kill (FON-29).

    The worksheet used to map ``period_type`` in {ytd, quarter, month} to the
    "T-12" column, asserting a trailing twelve that does not exist.
    """
    assert scope_of("PNL_YTD") == "ytd"
    assert scope_of("PNL", "ytd") == "ytd"
    assert scope_of("T12", "ytd") == "ytd"
    assert scope_of("PNL", "ytd") != "ttm"


def test_unknown_doc_type_and_unreadable_period_type_resolve_to_unknown() -> None:
    """Nothing is guessed: an unresolvable basis is ``unknown``."""
    assert scope_of(None) == "unknown"
    assert scope_of("") == "unknown"
    assert scope_of("OM") == "unknown"
    # An unrecognized period_type breaks to the doc default (here: none).
    assert scope_of(None, "since inception") == "unknown"
    # …and with a doc default, that default stands.
    assert scope_of("PNL", "since inception") == "annual"


def test_the_rank_map_the_web_mirror_reads_is_the_registry_s_own() -> None:
    """The web reads these exact ranks out of ``concepts.generated.ts``."""
    ranks = get_registry().period_types
    assert ranks["annual"] == 0
    assert ranks["ttm"] == 1
    assert ranks["ytd"] == 5
    assert ranks["quarterly"] == 7
    assert ranks["monthly"] == 9


# ───────── the historical baseline's projection of that scope ─────────


@pytest.mark.parametrize(
    ("doc_type", "expected"),
    [
        ("PNL", ("FY", False)),
        ("T12", ("T12", False)),
        ("PNL_YTD", ("YTD", True)),
        ("PNL_MONTHLY", ("MONTHLY", True)),
    ],
)
def test_period_basis_projects_the_doc_type_default(
    doc_type: str, expected: tuple[str, bool]
) -> None:
    """Same four defaults, in the walk's vocabulary — ``FY`` reads as the
    web's ``FY`` column header, and everything under twelve months is
    partial.
    """
    assert basis_of(doc_type) == expected


@pytest.mark.parametrize(
    ("period_type", "expected"),
    [
        ("annual", ("FY", False)),
        ("fiscal_year", ("FY", False)),
        ("ttm", ("T12", False)),
        ("trailing_twelve", ("T12", False)),
        ("ytd", ("YTD", True)),
        ("year_to_date", ("YTD", True)),
        ("quarterly", ("QUARTERLY", True)),
        ("monthly", ("MONTHLY", True)),
    ],
)
def test_period_basis_follows_a_stated_period_type(
    period_type: str, expected: tuple[str, bool]
) -> None:
    """A statement that says what it covers is believed over its filing —
    the walk refuses a ``PNL`` that declares itself year-to-date.
    """
    assert basis_of("PNL", period_type) == expected


def test_period_basis_is_case_insensitive_on_the_doc_type() -> None:
    """The loader's SQL matches on ``UPPER(d.doc_type)``, so a lowercase
    tag must not fall through to the annual default and be compared as a
    full year.
    """
    assert basis_of("pnl_ytd") == ("YTD", True)
    assert basis_of("t12") == ("T12", False)


def test_period_basis_falls_back_to_annual_only_with_no_doc_type() -> None:
    """A row with nothing to resolve from (the pure entrypoint's callers)
    keeps the annual default, which is what those rows compared as before
    the gate existed. The loader never reaches this: its SQL admits only
    the four P&L-family doc types, each of which HAS a default scope.
    """
    assert scope_of(None) == "unknown"
    assert basis_of(None) == ("FY", False)
