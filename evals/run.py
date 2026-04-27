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
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
    """Load USALI rules from usali-rules.csv. Returns [] if the file is missing."""
    csv_path = GOLDEN_SET_DIR / "usali-rules.csv"
    if not csv_path.exists():
        return []
    rules: list[dict] = []
    lines = csv_path.read_text().splitlines()
    if not lines:
        return rules
    header = [h.strip() for h in lines[0].split(",")]
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(",")]
        rules.append(dict(zip(header, cells)))
    return rules


def load_brand_catalog() -> dict:
    """Load brand-catalog.json. Returns {} if missing."""
    path = GOLDEN_SET_DIR / "brand-catalog.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def run_case(case_dir: Path) -> CaseResult:
    """Run a single case's pytest suite via subprocess and parse the result line."""
    start = time.monotonic()
    test_file = case_dir / "test_pipeline.py"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "--tb=short",
            "-rs",
            str(test_file),
        ],
        capture_output=True,
        text=True,
    )
    duration = time.monotonic() - start

    out = proc.stdout + proc.stderr
    passed = failed = skipped = 0
    failures: list[dict] = []

    # Pytest summary lines like "==== 4 passed, 1 skipped in 0.42s ===="
    # Walk the lines and collect stats.
    for line in out.splitlines():
        line_l = line.strip()
        if " passed" in line_l or " failed" in line_l or " skipped" in line_l:
            for token in line_l.split():
                if token.isdigit():
                    n = int(token)
                else:
                    continue
                if " passed" in line_l and "passed" in line_l.split(token)[1][:10]:
                    if passed == 0:
                        passed = n
                if " failed" in line_l and "failed" in line_l.split(token)[1][:10]:
                    if failed == 0:
                        failed = n
                if " skipped" in line_l and "skipped" in line_l.split(token)[1][:10]:
                    if skipped == 0:
                        skipped = n

        if line.startswith("FAILED "):
            failures.append({"test": line.replace("FAILED ", "").strip(), "raw": ""})

    # Fallback: re-parse the final summary line robustly.
    summary_line = ""
    for line in reversed(out.splitlines()):
        if line.startswith("=") and ("passed" in line or "failed" in line or "no tests" in line):
            summary_line = line
            break
    if summary_line:
        parts = summary_line.replace("=", "").strip().split()
        for i, tok in enumerate(parts):
            if tok in {"passed", "passed,"}:
                try: passed = int(parts[i - 1])
                except (ValueError, IndexError): pass
            elif tok in {"failed", "failed,"}:
                try: failed = int(parts[i - 1])
                except (ValueError, IndexError): pass
            elif tok in {"skipped", "skipped,"}:
                try: skipped = int(parts[i - 1])
                except (ValueError, IndexError): pass

    return CaseResult(
        case_name=case_dir.name,
        passed=passed,
        failed=failed,
        skipped=skipped,
        duration_seconds=duration,
        failures=failures,
    )


def run_all(case_filter: Optional[str] = None) -> RunSummary:
    """Run every discovered case (optionally filtered by name)."""
    started = datetime.now(timezone.utc)
    cases = discover_cases()
    if case_filter:
        cases = [c for c in cases if c.name == case_filter]

    rules = load_usali_rules()
    brands = load_brand_catalog()

    results: list[CaseResult] = []
    total_start = time.monotonic()
    for case_dir in cases:
        results.append(run_case(case_dir))
    total_duration = time.monotonic() - total_start
    finished = datetime.now(timezone.utc)

    return RunSummary(
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        total_duration_seconds=total_duration,
        total_passed=sum(r.passed for r in results),
        total_failed=sum(r.failed for r in results),
        total_skipped=sum(r.skipped for r in results),
        cases=results,
        rules_loaded=len(rules),
        brand_count=len(brands),
    )


def emit_text_report(summary: RunSummary) -> str:
    """Render a human-readable summary for stdout."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Fondok golden-set evaluation")
    lines.append("=" * 72)
    lines.append(f"Started:  {summary.started_at}")
    lines.append(f"Finished: {summary.finished_at}")
    lines.append(f"Duration: {summary.total_duration_seconds:.2f}s")
    lines.append(f"Rules loaded: {summary.rules_loaded}, brands: {summary.brand_count}")
    lines.append("")
    for case in summary.cases:
        lines.append(
            f"  [{case.case_name}] passed={case.passed} "
            f"failed={case.failed} skipped={case.skipped} "
            f"({case.duration_seconds:.2f}s)"
        )
        for f in case.failures:
            lines.append(f"      FAIL: {f['test']}")
    lines.append("")
    lines.append(
        f"TOTAL: passed={summary.total_passed} failed={summary.total_failed} "
        f"skipped={summary.total_skipped}"
    )
    lines.append("=" * 72)
    return "\n".join(lines) + "\n"


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
