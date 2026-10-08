"""E-023 (FON-63) — a pydantic ``ValidationError`` on an engine input must land
on the engine row as a sentence an analyst can act on, not raw validator text.

Two external testers hit the Debt engine with a negative year-1 NOI
(−4,879,452.5 and −69,982). ``DebtEngineInputExt.noi_by_year`` is
``Field(ge=0)``, so pydantic raised, the runner stored
``"1 validation error for DebtEngineInputExt\\nnoi_by_year.0\\n  Input should be
greater than or equal to 0 ..."`` and the UI could not say which input, which
year, what range, what did not run, or that the saved assumptions survived.

Pinned here:

* the EXACT Debt sentence (1-based year, ``−$4,879,453`` half-away-from-zero
  with thousands separators, the ``$0`` floor from ``ctx``);
* the downstream list is derived from ``ENGINE_DEPS`` in registry order;
* the "saved assumptions were kept" sentence and the actionable hint;
* the raw pydantic text survives verbatim after ``Technical detail:``;
* a generic-but-specific fallback for any other engine / field;
* non-validation exceptions are stored exactly as before.
"""

# The U+2212 MINUS SIGN in the expected strings below is deliberate: it is the
# exact glyph the runner writes and the banner shows, so the assertions must
# contain it. Ruff's confusable-character rules are therefore off for this file.
# ruff: noqa: RUF001, RUF002

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.engines.debt import DebtEngineInputExt
from app.services.engine_runner import (
    ENGINE_DEPS,
    _downstream_engines,
    _engine_error_text,
    _format_usd,
    _humanize_engine_validation_error,
)

# The exact human text the two testers should have seen (E-023 spec).
EXPECTED_DEBT_FIRST_SENTENCE = (
    "Debt: NOI for year 1 is −$4,879,453, below the $0 minimum the Debt model accepts."
)
EXPECTED_DOWNSTREAM_SENTENCE = (
    "The model stops here and Returns, Sensitivity, Partnership and Cash Flow were not run."
)
EXPECTED_KEPT_SENTENCE = "Your saved assumptions were kept."
EXPECTED_HINT_SENTENCE = (
    "Check key count, revenue base, expense base, or a starting-occupancy override, "
    "then re-run."
)
EXPECTED_DEBT_MESSAGE = " ".join(
    [
        EXPECTED_DEBT_FIRST_SENTENCE,
        EXPECTED_DOWNSTREAM_SENTENCE,
        EXPECTED_KEPT_SENTENCE,
        EXPECTED_HINT_SENTENCE,
    ]
)


def _debt_kwargs(**overrides: object) -> dict[str, object]:
    """A valid Debt input (only the required base-loan fields) so the one thing
    that raises is whatever the test overrides."""
    base: dict[str, object] = {
        "deal_id": uuid4(),
        "loan_amount": 25_000_000.0,
        "ltv": 0.60,
        "interest_rate": 0.065,
        "term_years": 5,
    }
    base.update(overrides)
    return base


class _NoiFloorDebtInput(DebtEngineInputExt):
    """The Debt input as it was when E-023 was filed: ``noi_by_year ge=0``.

    FON-63 since let negative NOI flow through the real Debt engine (DSCR N/A
    + a shortfall instead of a stop — ``test_negative_noi_flow.py``), so the
    formatter is exercised here against the old floor. The sentence it builds
    still applies to any engine input that floors an NOI series.
    """

    noi_by_year: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)


def _raise_debt(**overrides: object) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        _NoiFloorDebtInput(**_debt_kwargs(**overrides))
    return info.value


def test_fixture_is_valid_without_the_bad_input() -> None:
    """Guard: the base kwargs construct, so the raise below is the NOI alone."""
    model = _NoiFloorDebtInput(**_debt_kwargs(noi_by_year=[4_879_452.5, 1.0]))
    assert model.noi_by_year == [4_879_452.5, 1.0]


def test_real_debt_input_now_accepts_negative_noi() -> None:
    """FON-63 — the live Debt input no longer stops on a negative year."""
    model = DebtEngineInputExt(**_debt_kwargs(noi_by_year=[-4_879_452.5, 1.0]))
    assert model.noi_by_year == [-4_879_452.5, 1.0]


def test_debt_negative_year1_noi_is_the_exact_e023_sentence() -> None:
    exc = _raise_debt(noi_by_year=[-4879452.5, 1.0])
    # Only the NOI entry fires — the sentence describes exactly that.
    assert [e["loc"] for e in exc.errors()] == [("noi_by_year", 0)]

    text = _humanize_engine_validation_error("debt", exc)
    human, sep, technical = text.partition("\n\nTechnical detail: ")

    assert sep, "raw pydantic text must follow a blank line and 'Technical detail:'"
    assert human == EXPECTED_DEBT_MESSAGE
    assert human.startswith(EXPECTED_DEBT_FIRST_SENTENCE)
    assert EXPECTED_DOWNSTREAM_SENTENCE in human
    assert EXPECTED_KEPT_SENTENCE in human
    assert human.endswith(EXPECTED_HINT_SENTENCE)

    # Nothing lost: the technical remainder IS the raw pydantic text.
    assert technical == str(exc)
    assert "noi_by_year.0" in technical
    assert "greater_than_equal" in technical
    assert "-4879452.5" in technical


def test_second_tester_value_and_1_based_year_index() -> None:
    """−69,982 in year 2 → 'year 2' (loc[1] = 1 is 0-based) and '−$69,982'."""
    exc = _raise_debt(noi_by_year=[1.0, -69982.0, 1.0])
    text = _humanize_engine_validation_error("debt", exc)
    assert text.startswith(
        "Debt: NOI for year 2 is −$69,982, below the $0 minimum the Debt model accepts. "
    )


def test_multiple_bad_years_each_get_a_sentence() -> None:
    exc = _raise_debt(noi_by_year=[-4879452.5, 1.0, -69982.0])
    human = _humanize_engine_validation_error("debt", exc).split("\n\nTechnical detail: ")[0]
    assert human.startswith(
        "Debt: NOI for year 1 is −$4,879,453, below the $0 minimum the Debt model accepts. "
        "NOI for year 3 is −$69,982, below the $0 minimum the Debt model accepts. "
        "The model stops here and "
    )
    # The hint is shared by both entries and must not be repeated.
    assert human.count(EXPECTED_HINT_SENTENCE) == 1


def test_downstream_list_is_derived_from_engine_deps_in_registry_order() -> None:
    assert _downstream_engines("debt") == ["returns", "sensitivity", "partnership", "cash_flow"]
    assert _downstream_engines("returns") == ["sensitivity", "partnership", "cash_flow"]
    assert _downstream_engines("cash_flow") == []
    # ``capital`` has no upstream, so a revenue failure never blocks it.
    revenue_blocked = _downstream_engines("revenue")
    assert "capital" not in revenue_blocked
    assert revenue_blocked[:3] == ["fb", "expense", "debt"]
    # Every listed dependant really has a path back to the failed engine.
    blocked_by_debt = {"debt", *_downstream_engines("debt")}
    for name in _downstream_engines("debt"):
        assert any(d in blocked_by_debt for d in ENGINE_DEPS[name])


def test_leaf_engine_failure_says_nothing_else_depends_on_it() -> None:
    exc = _raise_debt(noi_by_year=[-1.0])
    text = _humanize_engine_validation_error("cash_flow", exc)
    assert "The model stops here; no other model depends on it." in text
    assert "were not run" not in text
    assert EXPECTED_KEPT_SENTENCE in text


def test_generic_fallback_names_field_value_and_range() -> None:
    """Any other engine / field: '<field path> = <value> is outside the allowed
    range (<constraint>)' + the same stops / not-run / kept text."""
    exc = _raise_debt(purchase_price_usd=-1_000_000.0)
    text = _humanize_engine_validation_error("debt", exc)
    human, _, technical = text.partition("\n\nTechnical detail: ")
    assert human == (
        "Debt: purchase_price_usd = -1,000,000 is outside the allowed range (≥ 0). "
        "The model stops here and Returns, Sensitivity, Partnership and Cash Flow were not run. "
        "Your saved assumptions were kept. "
        "Check the assumptions feeding the Debt model, then re-run."
    )
    assert technical == str(exc)


def test_generic_fallback_for_another_engine_and_a_non_range_error() -> None:
    """A missing required field on a different engine: the path + pydantic's
    message, the capital engine's own dependants, and the kept sentence."""

    class _CapitalLike(BaseModel):
        purchase_price: Annotated[float, Field(gt=0)]
        equity_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.4

    with pytest.raises(ValidationError) as info:
        _CapitalLike(equity_pct=1.5)  # type: ignore[call-arg]
    text = _humanize_engine_validation_error("capital", info.value)
    human = text.split("\n\nTechnical detail: ")[0]
    assert human == (
        "Capital: purchase_price: Field required. "
        "equity_pct = 1.5 is outside the allowed range (≤ 1). "
        "The model stops here and Debt, Returns, Sensitivity, Partnership and Cash Flow "
        "were not run. "
        "Your saved assumptions were kept. "
        "Check the assumptions feeding the Capital model, then re-run."
    )


def test_engine_error_text_dispatches_only_validation_errors() -> None:
    exc = _raise_debt(noi_by_year=[-4879452.5, 1.0])
    assert _engine_error_text("debt", exc) == _humanize_engine_validation_error("debt", exc)
    # Anything else is stored exactly as before — byte-for-byte str(exc).
    plain = ZeroDivisionError("float division by zero")
    assert _engine_error_text("debt", plain) == "float division by zero"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-4879452.5, "−$4,879,453"),  # half away from zero, not banker's
        (-69982.0, "−$69,982"),
        (0.0, "$0"),
        (1234567.49, "$1,234,567"),
        (-0.5, "−$1"),
    ],
)
def test_format_usd(value: float, expected: str) -> None:
    assert _format_usd(value) == expected
