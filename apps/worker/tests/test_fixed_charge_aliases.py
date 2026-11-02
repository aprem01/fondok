"""Fixed-charge lines on the real annual P&Ls resolve through the alias catalog.

The 2020-2023 Angler's summary P&Ls (and their copies on several prod deals)
carry insurance and property taxes under paths the Phase-1.3a scope gate in
``field_catalog.yaml`` never listed, so the T-12 expense loader read no
insurance or property tax off them at all (W5 extract, item 1). The paths
below are the exact ``field_name`` strings found on those prod extractions
(read-only audit, 2026-11-02).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-fixed-charge-aliases.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.extraction.field_catalog import T12_EXPENSE_FIELD_ALIASES  # noqa: E402
from app.services import engine_runner as er  # noqa: E402


def _lines(fields):
    return er._resolve_statement_lines(
        fields,
        T12_EXPENSE_FIELD_ALIASES,
        opposite_section=er._is_revenue_section,
        admit=lambda v: v > 0,
    )


def _f(name: str, value: float) -> dict:
    return {"field_name": name, "value": value, "unit": "USD", "confidence": 0.98}


@pytest.mark.parametrize(
    ("statement", "fields", "insurance", "taxes"),
    [
        (
            "Angler_s 2020 P&L.xlsx",
            [
                _f("p_and_l_usali.fixed_charges.rent_usd", 7421.55),
                _f("p_and_l_usali.fixed_charges.property_taxes_usd", 558748),
                _f("p_and_l_usali.fixed_charges.insurance_usd", 755029),
            ],
            755029,
            558748,
        ),
        (
            "Angler_s 2021 P&L.xlsx / 2022 / 2023",
            [
                _f("p_and_l_usali.non_operating.rent_usd", 14805.0),
                _f("p_and_l_usali.non_operating.insurance_usd", 831744.0),
                _f("p_and_l_usali.non_operating.property_and_other_taxes_usd", 497561.0),
                _f("p_and_l_usali.non_operating.total_non_operating_usd", 609751.0),
            ],
            831744.0,
            497561.0,
        ),
        (
            "Copy of Angler_s 2023 P&L.xlsx",
            [
                _f("p_and_l_usali.fixed_charges.insurance_usd", 1351730),
                _f("p_and_l_usali.fixed_charges.property_and_other_taxes_usd", 496949),
            ],
            1351730,
            496949,
        ),
        (
            "Angler_s 2019 P&L.xlsx",
            [
                _f("p_and_l_usali.fixed_charges.rent", 10719.1),
                _f("p_and_l_usali.fixed_charges.property_and_other_taxes", 548002),
                _f("p_and_l_usali.fixed_charges.insurance", 662426),
            ],
            662426,
            548002,
        ),
    ],
)
def test_real_fixed_charge_paths_resolve(statement, fields, insurance, taxes):
    lines = _lines(fields)
    assert lines["insurance"][0] == pytest.approx(insurance), statement
    assert lines["property_taxes"][0] == pytest.approx(taxes), statement


def test_new_aliases_are_exact_paths_not_bare_tails():
    """Only the audited statement-level paths were widened: a bare
    ``insurance_usd`` tail (which also ends prior-year / budget / per-key
    rows) is still not an alias."""
    for path in (
        "p_and_l_usali.fixed_charges.insurance_usd",
        "p_and_l_usali.non_operating.insurance_usd",
        "p_and_l_usali.fixed_charges.property_taxes_usd",
        "p_and_l_usali.fixed_charges.property_and_other_taxes",
        "p_and_l_usali.fixed_charges.property_and_other_taxes_usd",
        "p_and_l_usali.non_operating.property_and_other_taxes_usd",
    ):
        assert path in T12_EXPENSE_FIELD_ALIASES, path
    assert "insurance_usd" not in T12_EXPENSE_FIELD_ALIASES
    assert "property_and_other_taxes_usd" not in T12_EXPENSE_FIELD_ALIASES


def test_prior_year_and_budget_insurance_rows_never_claim_the_line():
    lines = _lines(
        [
            _f("p_and_l_usali.prior_year.insurance_usd", 999.0),
            _f("p_and_l_usali.budget.insurance_usd", 888.0),
        ]
    )
    assert "insurance" not in lines
