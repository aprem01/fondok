"""Fondok golden-set evaluation runner.

Discovers all golden-set cases under /evals/golden-set/<case>/ and runs
their pytest test_pipeline.py files. Aggregates results and emits a
machine-readable report.

Usage:
    python evals/run.py                    # run all cases
    python evals/run.py --case kimpton-angler  # run a single case
    python evals/run.py --report json      # emit JSON report instead of text
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

EVALS_ROOT = Path(__file__).parent
GOLDEN_SET_DIR = EVALS_ROOT / "golden-set"


@dataclass
class CaseResult:
    case_name: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    failures: list[dict] = field(default_factory=list)


@dataclass
class RunSummary:
    started_at: str
    finished_at: str
    total_duration_seconds: float
    total_passed: int
    total_failed: int
    total_skipped: int
    cases: list[CaseResult] = field(default_factory=list)
    rules_loaded: int = 0
    brand_count: int = 0


def discover_cases() -> list[Path]:
    """Walk golden-set/ and return every case directory containing test_pipeline.py."""
    cases: list[Path] = []
    if not GOLDEN_SET_DIR.exists():
        return cases
    for case_dir in sorted(p for p in GOLDEN_SET_DIR.iterdir() if p.is_dir()):
        if (case_dir / "test_pipeline.py").exists():
            cases.append(case_dir)
    return cases


def load_usali_rules() -> list[dict]:
    """Stub — load USALI rules from usali-rules.csv."""
    raise NotImplementedError("wire to fondok.rules.load()")


def load_brand_catalog() -> dict:
    """Stub — load brand-catalog.json."""
    raise NotImplementedError("wire to fondok.brands.load()")


def run_case(case_dir: Path) -> CaseResult:
    """Run a single case's pytest suite and return aggregated results."""
    raise NotImplementedError("wire to pytest --json-report or pytest.main()")


def run_all(case_filter: Optional[str] = None) -> RunSummary:
    """Run every discovered case (optionally filtered by name)."""
    raise NotImplementedError("wire to discover_cases() and run_case() per case")


def emit_text_report(summary: RunSummary) -> str:
    """Render a human-readable summary for stdout."""
    raise NotImplementedError("format RunSummary as text")


def emit_json_report(summary: RunSummary) -> str:
    """Render summary as JSON (for CI ingestion)."""
    return json.dumps(asdict(summary), indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run a single case by directory name")
    parser.add_argument(
        "--report",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    summary = run_all(case_filter=args.case)

    if args.report == "json":
        sys.stdout.write(emit_json_report(summary))
    else:
        sys.stdout.write(emit_text_report(summary))

    return 0 if summary.total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
