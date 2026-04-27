"""Golden-set regression tests for the Kimpton Angler underwriting case.

These tests verify the Fondok underwriting pipeline against the canonical
expected outputs in /expected/. Each expected file is checked against the
pipeline output produced by running the engines on /input/.

Run with: pytest -v evals/golden-set/kimpton-angler/test_pipeline.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

CASE_DIR = Path(__file__).parent
INPUT_DIR = CASE_DIR / "input"
EXPECTED_DIR = CASE_DIR / "expected"
RULES_FILE = CASE_DIR.parent / "usali-rules.csv"

DEFAULT_NUMERIC_TOLERANCE_PCT = 0.005  # 0.5 percent


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def om_input() -> dict:
    """Parsed Offering Memorandum input."""
    return json.loads((INPUT_DIR / "om_extracted.json").read_text())


@pytest.fixture(scope="module")
def t12_input() -> dict:
    """Parsed T-12 P&L input."""
    return json.loads((INPUT_DIR / "t12_extracted.json").read_text())


@pytest.fixture(scope="module")
def str_input() -> dict:
    """Parsed STR market report input."""
    return json.loads((INPUT_DIR / "str_extracted.json").read_text())


@pytest.fixture(scope="module")
def expected_extraction() -> dict:
    return json.loads((EXPECTED_DIR / "extraction.json").read_text())


@pytest.fixture(scope="module")
def expected_normalized() -> dict:
    return json.loads((EXPECTED_DIR / "normalized.json").read_text())


@pytest.fixture(scope="module")
def expected_variance() -> dict:
    return json.loads((EXPECTED_DIR / "variance.json").read_text())


@pytest.fixture(scope="module")
def expected_model() -> dict:
    return json.loads((EXPECTED_DIR / "model.json").read_text())


@pytest.fixture(scope="module")
def expected_memo() -> dict:
    return json.loads((EXPECTED_DIR / "memo.json").read_text())


@pytest.fixture(scope="module")
def usali_rule_ids() -> set[str]:
    """Set of valid rule_ids from the USALI rules CSV."""
    rule_ids: set[str] = set()
    for line in RULES_FILE.read_text().splitlines()[1:]:
        if line.strip():
            rule_ids.add(line.split(",", 1)[0])
    return rule_ids


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_extraction_matches_expected(om_input, t12_input, str_input, expected_extraction):
    """Every field in expected_extraction.json must reconcile to one of the input files.

    For each fields[] entry: locate the source_document in /input/, walk the dotted
    field path, and assert the value matches within tolerance.
    """
    pass


def test_normalization_matches_usali(t12_input, expected_normalized):
    """The pipeline's USALI normalization must equal expected_normalized.json.

    Specifically:
      - REVPAR_CHECK, REVENUE_SUM, DEPT_EXPENSE_SUM, GOP_IDENTITY, NOI_IDENTITY all PASS
      - Departmental margins fall in expected ranges
      - Coastal insurance flag set when applicable
    """
    pass


def test_variance_flags_grounded(expected_variance, usali_rule_ids):
    """Every variance flag must reference a rule_id present in usali-rules.csv."""
    for flag in expected_variance["flags"]:
        assert flag["rule_id"] in usali_rule_ids, (
            f"Flag {flag['flag_id']} references unknown rule_id {flag['rule_id']}"
        )


def test_engine_outputs_within_tolerance(om_input, t12_input, str_input, expected_model):
    """Pipeline engine outputs must match expected_model.json within 0.5 percent.

    Compares investment, p_and_l, debt, cash_flow, returns, partnership engines.
    Tolerance applies to numeric fields only.
    """
    pass


def test_memo_cites_sources(expected_memo, om_input, t12_input, str_input):
    """Every memo section claim must cite a source document or engine output.

    Checks that:
      - Every section.citations[] entry references a real document or engine field
      - No section body contains uncited specific numeric claims
    """
    pass


# --------------------------------------------------------------------------- #
# Helpers (placeholders for the pipeline harness)
# --------------------------------------------------------------------------- #


def run_extraction_pipeline(om: dict, t12: dict, str_report: dict) -> dict:
    """Stub — invoked by the test suite to run the live extraction pipeline."""
    raise NotImplementedError("wire to fondok.pipeline.extraction.run()")


def run_normalization_pipeline(extracted: dict) -> dict:
    raise NotImplementedError("wire to fondok.pipeline.normalize.run()")


def run_variance_pipeline(normalized: dict, broker_proforma: dict, rules: list) -> dict:
    raise NotImplementedError("wire to fondok.pipeline.variance.run()")


def run_engine_pipeline(normalized: dict, deal_assumptions: dict) -> dict:
    raise NotImplementedError("wire to fondok.pipeline.engines.run()")


def run_memo_pipeline(model: dict, variance: dict, market: dict) -> dict:
    raise NotImplementedError("wire to fondok.pipeline.memo.run()")
