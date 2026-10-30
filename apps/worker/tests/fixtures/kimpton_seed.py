"""The Kimpton Angler seed set — a TEST FIXTURE, injected explicitly.

Until 2026-10-30 these values lived in the production loader
(``engine_runner._kimpton_assumptions``) and were applied to EVERY deal
whose own inputs were missing, so a deal with no keys and no purchase price
still ran as a 132-key, $36.4M hotel. The identity values now live only
here; production has no default for them (see
``engine_runner.IDENTITY_INPUT_KEYS``) and refuses to run the dependent
engines when they are missing.

The fixture is the complete assumption set the Kimpton golden baseline
(``evals/baselines/kimpton-golden-2026-10-16.json``) was generated from. It
is injected through the one public seam for explicit assumptions::

    run_all_engines(session, deal_id=KIMPTON_FIXTURE_DEAL_ID,
                    tenant_id=..., run_id=..., assumptions=kimpton_seed())

Both ``tests/test_engine_runner.py::test_run_all_engines_kimpton`` and
``scripts/compare_golden_baseline.py`` go through that call, so the golden
numbers are byte-identical to the pre-fixture run.

Keep the Kimpton values in THIS module only. The non-identity part of the
dict mirrors the production ``_default_assumptions()`` values on purpose
(``test_kimpton_fixture_defaults_match_production_defaults`` pins that): a
production default change must be a conscious golden re-baseline, never a
silent drift.
"""

from __future__ import annotations

from typing import Any

#: The baseline's ``deal_id``. Historically ``uuid5(NAMESPACE_URL,
#: "fondok://deal/kimpton-angler-2026")`` — the id the old non-UUID demo slug
#: was coerced to — pinned here as the plain UUID string so no slug mapping
#: exists anywhere any more. Engine outputs carry ``deal_id``, so the golden
#: comparison depends on this exact value.
KIMPTON_FIXTURE_DEAL_ID = "d849e90a-9581-519c-a796-5e09277b7260"

#: The two identity inputs. Tests that insert a deal row and expect the
#: Kimpton numbers put these on the row (``keys`` / ``purchase_price``).
KIMPTON_KEYS = 132
KIMPTON_PURCHASE_PRICE = 36_400_000


def kimpton_seed() -> dict[str, Any]:
    """A fresh copy of the complete Kimpton assumption set."""
    return {
        "keys": KIMPTON_KEYS,
        "purchase_price": KIMPTON_PURCHASE_PRICE,
        "starting_occupancy": 0.762,
        "starting_adr": 385.0,
        "occupancy_growth": 0.008,
        "adr_growth": 0.04,
        "fb_revenue_per_occupied_room": 88.0,
        "other_revenue_pct_of_rooms": 0.065,
        "hold_years": 5,
        "hotel_type": "lifestyle",
        "fb_ratio": 0.29,
        "other_ratio": 0.06,
        "mgmt_fee_pct": 0.03,
        "ffe_reserve_pct": 0.04,
        "expense_growth": 0.035,
        "grow_opex_independently": True,
        "renovation_budget": 5_280_000,
        # FON-44 (D2) — no universal soft-cost / contingency allowance; the
        # capital engine omits zero uses. ``working_capital`` stays (Sam
        # accepted it in her reconciled Total Uses).
        "soft_costs": 0.0,
        "contingency": 0.0,
        "working_capital": 500_000,
        "closing_costs_pct": 0.02,
        "loan_costs_pct": 0.015,
        "ltv": 0.65,
        "interest_rate": 0.068,
        "amortization_years": 30,
        "term_years": 5,
        "interest_only_years": 0,
        "exit_cap_rate": 0.07,
        "revpar_growth": 0.045,
        "selling_costs_pct": 0.02,
        # FON-67 — transfer/recordation tax on exit (0 unless the analyst sets it).
        "transfer_tax_pct": 0.0,
        "gp_equity_pct": 0.10,
        "lp_equity_pct": 0.90,
        # FON-66 — Kimpton Angler benchmark preferred return (10%).
        "pref_rate": 0.10,
    }


__all__ = [
    "KIMPTON_FIXTURE_DEAL_ID",
    "KIMPTON_KEYS",
    "KIMPTON_PURCHASE_PRICE",
    "kimpton_seed",
]
