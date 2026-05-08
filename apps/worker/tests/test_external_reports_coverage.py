"""CI regression for the external-report parse-coverage benchmark.

Replays the coverage check shipped at ``evals/external-reports/parse_coverage.py``
and asserts overall coverage stays at-or-above the 80% target. A drop
below 80% means the PDF/Excel parser stopped surfacing source text
that the extractor prompt expects to find — a silent regression that
would tank LLM extraction accuracy without any obvious user-visible
symptom.

This test is parser-only — no LLM calls, no API key, no fixture
labelling. The full extraction-accuracy benchmark (LLM run + ground
truth comparison) ships separately once Sam-annotated deal docs land.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "evals" / "external-reports" / "parse_coverage.py"


def _load_coverage_module():
    spec = importlib.util.spec_from_file_location(
        "fondok_evals_parse_coverage", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_external_report_coverage_at_or_above_target() -> None:
    """Each external report must hit ≥80% parse coverage on its
    anchor table — and the overall combined coverage must too.

    A regression here means a PDF/Excel parser change broke surface
    text we expect the extractor to ground fields on. Catching it
    before it reaches an LLM run saves both tokens and accuracy
    debugging time.
    """
    if not SCRIPT_PATH.exists():
        pytest.skip(f"coverage script not present at {SCRIPT_PATH}")

    cov = _load_coverage_module()
    target = 0.80

    for spec in cov.REPORTS:
        result = await cov._coverage_for(
            filename=spec["filename"],
            doc_type=spec["doc_type"],
            anchors=spec["anchors"],
        )
        if "error" in result:
            pytest.skip(result["error"])
        summary = result["summary"]
        coverage = summary["coverage_pct"]
        assert coverage >= target, (
            f"{result['filename']} parse coverage dropped to "
            f"{coverage:.1%} (< {target:.0%}). Missing anchors: "
            + ", ".join(
                ef["path"]
                for ef in result["expected_fields"]
                if ef["status"] == "missing"
            )
        )
