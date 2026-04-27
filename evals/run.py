"""Fondok golden-set evaluation runner.

Discovers all golden-set cases under ``evals/golden-set/<case>/``, runs
the deterministic engine pipeline against the input fixtures, and
asserts that every numeric output matches ``expected/model.json``
within the per-field tolerance configured in
``expected/tolerance.json`` (default 0.5 percent).

Outputs:
    * stdout       — human-readable drift table.
    * JUnit XML    — ``evals/results.xml`` for the GitHub PR test
                     reporter and CI artifact storage.

Exit codes:
    * 0  — every assertion passed.
    * 1  — at least one numeric drift outside tolerance.
    * 2  — fixtures missing or corrupt (e.g. expected/model.json gone,
           tolerance.json malformed).

Usage::

    python evals/run.py
    python evals/run.py --case kimpton-angler
    python evals/run.py --report json
    python evals/run.py --junit-output /tmp/results.xml
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

EVALS_ROOT = Path(__file__).parent
GOLDEN_SET_DIR = EVALS_ROOT / "golden-set"
DEFAULT_JUNIT_PATH = EVALS_ROOT / "results.xml"

REPO_ROOT = EVALS_ROOT.parent
WORKER_PATH = REPO_ROOT / "apps" / "worker"
SCHEMAS_PATH = REPO_ROOT / "packages" / "schemas-py"
for p in (WORKER_PATH, SCHEMAS_PATH):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ─────────────────────── exit codes ───────────────────────


EXIT_PASS = 0
EXIT_DRIFT = 1
EXIT_FIXTURES_BROKEN = 2


# ─────────────────────── data classes ───────────────────────


@dataclass
class DriftRow:
    """One numeric assertion result, ready for stdout / JUnit projection."""

    metric: str
    expected: float
    actual: float
    delta_pct: float
    tolerance_pct: float
    passed: bool
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "expected": self.expected,
            "actual": self.actual,
            "delta_pct": self.delta_pct,
            "tolerance_pct": self.tolerance_pct,
            "passed": self.passed,
            "error": self.error,
        }


@dataclass
class CaseResult:
    """Per-case rollup of drift rows + any setup error."""

    case_name: str
    duration_seconds: float = 0.0
    drift_rows: list[DriftRow] = field(default_factory=list)
    setup_error: Optional[str] = None  # e.g. fixtures missing, exception in engines

    @property
    def passed(self) -> int:
        return sum(1 for r in self.drift_rows if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.drift_rows if not r.passed)

    @property
    def is_broken(self) -> bool:
        return self.setup_error is not None


@dataclass
class RunSummary:
    started_at: str
    finished_at: str
    total_duration_seconds: float
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def total_passed(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def total_failed(self) -> int:
        return sum(c.failed for c in self.cases)

    @property
    def has_broken_cases(self) -> bool:
        return any(c.is_broken for c in self.cases)


# ─────────────────────── fixture discovery ───────────────────────


def discover_cases() -> list[Path]:
    """Walk golden-set/ and return every case directory containing input/ + expected/."""
    cases: list[Path] = []
    if not GOLDEN_SET_DIR.exists():
        return cases
    for case_dir in sorted(p for p in GOLDEN_SET_DIR.iterdir() if p.is_dir()):
        if (case_dir / "input").exists() and (case_dir / "expected").exists():
            cases.append(case_dir)
    return cases


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_tolerance(case_dir: Path) -> tuple[float, dict[str, float]]:
    """Read ``expected/tolerance.json`` if present; return (default, per_field)."""
    tol_path = case_dir / "expected" / "tolerance.json"
    if not tol_path.exists():
        return 0.005, {}
    raw = _load_json(tol_path)
    default = float(raw.get("default_tolerance_pct", 0.005))
    fields = {str(k): float(v) for k, v in (raw.get("fields") or {}).items()}
    return default, fields


def _resolve_tolerance(metric: str, default: float, per_field: dict[str, float]) -> float:
    """Look up the tolerance for ``metric``. Falls back to default."""
    return per_field.get(metric, default)


# ─────────────────────── numeric comparison ───────────────────────


def _delta_pct(actual: float, expected: float) -> float:
    """Relative delta. When expected is 0, treat absolute |actual| as the gap."""
    if expected == 0:
        return abs(actual)
    return abs(actual - expected) / abs(expected)


def _check(
    metric: str,
    actual: float,
    expected: float,
    *,
    default_tol: float,
    per_field_tol: dict[str, float],
) -> DriftRow:
    tol = _resolve_tolerance(metric, default_tol, per_field_tol)
    delta = _delta_pct(actual, expected)
    return DriftRow(
        metric=metric,
        expected=expected,
        actual=actual,
        delta_pct=delta,
        tolerance_pct=tol,
        passed=delta <= tol,
    )


# ─────────────────────── engine pipeline (wrapped) ───────────────────────


def _run_engine_pipeline(case_dir: Path) -> dict[str, Any]:
    """Invoke the deterministic engine helper from test_pipeline.py.

    The case ships its own ``test_pipeline.py:_run_engines`` (carries
    case-specific assumption overrides). We import it dynamically rather
    than re-implementing the pipeline here so the eval runner stays in
    lock-step with the golden-set tests.
    """
    import importlib.util

    test_file = case_dir / "test_pipeline.py"
    spec = importlib.util.spec_from_file_location(
        f"goldenset.{case_dir.name}.test_pipeline", test_file
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {test_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    om = _load_json(case_dir / "input" / "om_extracted.json")
    t12 = _load_json(case_dir / "input" / "t12_extracted.json")
    str_report = _load_json(case_dir / "input" / "str_extracted.json")
    return module._run_engines(om, t12, str_report)


# ─────────────────────── drift assembly per-case ───────────────────────


def _proforma_index(model: dict) -> dict[str, dict[str, Any]]:
    """``label -> row`` for the p_and_l_engine_proforma table."""
    return {row["label"]: row for row in model["p_and_l_engine_proforma"]["lines"]}


def _check_case(case_dir: Path) -> CaseResult:
    """Run one case end-to-end and return the per-row drift report."""
    started = time.monotonic()
    case_name = case_dir.name

    expected_path = case_dir / "expected" / "model.json"
    if not expected_path.exists():
        return CaseResult(
            case_name=case_name,
            duration_seconds=time.monotonic() - started,
            setup_error=f"missing fixture: {expected_path}",
        )

    try:
        expected_model = _load_json(expected_path)
    except json.JSONDecodeError as exc:
        return CaseResult(
            case_name=case_name,
            duration_seconds=time.monotonic() - started,
            setup_error=f"corrupt fixture {expected_path.name}: {exc}",
        )

    try:
        default_tol, per_field_tol = _load_tolerance(case_dir)
    except (json.JSONDecodeError, ValueError) as exc:
        return CaseResult(
            case_name=case_name,
            duration_seconds=time.monotonic() - started,
            setup_error=f"corrupt tolerance.json: {exc}",
        )

    try:
        engines = _run_engine_pipeline(case_dir)
    except (ImportError, ModuleNotFoundError) as exc:
        return CaseResult(
            case_name=case_name,
            duration_seconds=time.monotonic() - started,
            setup_error=f"engine deps unavailable: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            case_name=case_name,
            duration_seconds=time.monotonic() - started,
            setup_error=f"engine pipeline raised: {exc}\n{traceback.format_exc()}",
        )

    cap = engines["capital"]
    debt = engines["debt"]
    ret = engines["returns"]
    fb = engines["fb"]
    exp = engines["expense"]
    proforma = _proforma_index(expected_model)

    inv_exp = expected_model["investment_engine"]
    debt_exp = expected_model["debt_engine"]
    ret_exp = expected_model["returns_engine"]

    rows: list[DriftRow] = []

    def _add(metric: str, actual: float, expected: float) -> None:
        rows.append(
            _check(
                metric,
                actual,
                expected,
                default_tol=default_tol,
                per_field_tol=per_field_tol,
            )
        )

    # Investment engine
    _add("investment_engine.price_per_key_usd", cap.price_per_key, inv_exp["price_per_key_usd"])
    _add("investment_engine.total_capital_usd", cap.total_capital, inv_exp["total_capital_usd"])

    # Debt engine
    _add("debt_engine.loan_amount_usd", cap.debt_amount, debt_exp["loan_amount_usd"])
    _add(
        "debt_engine.annual_debt_service_usd",
        debt.annual_debt_service,
        debt_exp["annual_debt_service_usd"],
    )
    if debt.year_one_debt_yield is not None:
        _add(
            "debt_engine.year1_debt_yield",
            debt.year_one_debt_yield,
            debt_exp["year1_debt_yield"],
        )

    # Returns engine
    _add(
        "returns_engine.gross_sale_price_usd",
        ret.gross_sale_price,
        ret_exp["gross_sale_price_usd"],
    )
    _add(
        "returns_engine.selling_costs_usd",
        ret.selling_costs,
        ret_exp["selling_costs_usd"],
    )

    # Proforma table — values in $ thousands.
    _add(
        "p_and_l_engine_proforma.lines.Room Revenue.y1",
        fb.years[0].rooms_revenue / 1_000.0,
        proforma["Room Revenue"]["y1"],
    )
    _add(
        "p_and_l_engine_proforma.lines.F&B Revenue.y1",
        fb.years[0].fb_revenue / 1_000.0,
        proforma["F&B Revenue"]["y1"],
    )
    _add(
        "p_and_l_engine_proforma.lines.Total Revenue.y1",
        fb.years[0].total_revenue / 1_000.0,
        proforma["Total Revenue"]["y1"],
    )
    _add(
        "p_and_l_engine_proforma.lines.Net Operating Income.y1",
        exp.years[0].noi / 1_000.0,
        proforma["Net Operating Income"]["y1"],
    )
    _add(
        "p_and_l_engine_proforma.lines.Net Operating Income.y5",
        exp.years[4].noi / 1_000.0,
        proforma["Net Operating Income"]["y5"],
    )

    return CaseResult(
        case_name=case_name,
        duration_seconds=time.monotonic() - started,
        drift_rows=rows,
    )


# ─────────────────────── reporters ───────────────────────


def _fmt_value(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v:.4f}"


def emit_text_report(summary: RunSummary) -> str:
    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("Fondok golden-set drift report")
    lines.append("=" * 90)
    lines.append(f"Started:  {summary.started_at}")
    lines.append(f"Finished: {summary.finished_at}")
    lines.append(f"Duration: {summary.total_duration_seconds:.2f}s")
    lines.append("")

    for case in summary.cases:
        lines.append(f"[{case.case_name}] passed={case.passed} failed={case.failed} "
                     f"({case.duration_seconds:.2f}s)")
        if case.is_broken:
            lines.append(f"  SETUP ERROR: {case.setup_error}")
            lines.append("")
            continue

        # Drift table
        lines.append(
            f"  {'metric':<60} {'expected':>15} {'actual':>15} {'delta%':>10} {'pass':>5}"
        )
        lines.append("  " + "-" * 110)
        for r in case.drift_rows:
            mark = "OK" if r.passed else "FAIL"
            lines.append(
                f"  {r.metric:<60} "
                f"{_fmt_value(r.expected):>15} "
                f"{_fmt_value(r.actual):>15} "
                f"{r.delta_pct * 100:>9.3f}% "
                f"{mark:>5}"
            )
            if not r.passed:
                lines.append(
                    f"      → drift {r.delta_pct * 100:.3f}% > tolerance "
                    f"{r.tolerance_pct * 100:.3f}%"
                )
        lines.append("")

    lines.append(
        f"TOTAL: passed={summary.total_passed} failed={summary.total_failed} "
        f"broken_cases={sum(1 for c in summary.cases if c.is_broken)}"
    )
    lines.append("=" * 90)
    return "\n".join(lines) + "\n"


def emit_json_report(summary: RunSummary) -> str:
    return json.dumps(
        {
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "total_duration_seconds": summary.total_duration_seconds,
            "total_passed": summary.total_passed,
            "total_failed": summary.total_failed,
            "cases": [
                {
                    "case_name": c.case_name,
                    "duration_seconds": c.duration_seconds,
                    "passed": c.passed,
                    "failed": c.failed,
                    "setup_error": c.setup_error,
                    "drift_rows": [r.to_dict() for r in c.drift_rows],
                }
                for c in summary.cases
            ],
        },
        indent=2,
    )


def emit_junit_xml(summary: RunSummary) -> str:
    """Render the run as a JUnit XML document.

    GitHub's test reporter actions (e.g. dorny/test-reporter) consume
    this format directly — failures show up inline on the PR diff.
    """
    total_tests = sum(max(1, len(c.drift_rows)) for c in summary.cases)
    total_failures = sum(c.failed for c in summary.cases)
    total_errors = sum(1 for c in summary.cases if c.is_broken)

    out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(
        f'<testsuites name="fondok-golden-set" tests="{total_tests}" '
        f'failures="{total_failures}" errors="{total_errors}" '
        f'time="{summary.total_duration_seconds:.3f}">'
    )

    for case in summary.cases:
        case_tests = max(1, len(case.drift_rows))
        case_failures = case.failed
        case_errors = 1 if case.is_broken else 0
        out.append(
            f'  <testsuite name="{xml_escape(case.case_name)}" '
            f'tests="{case_tests}" failures="{case_failures}" '
            f'errors="{case_errors}" time="{case.duration_seconds:.3f}">'
        )

        if case.is_broken:
            err = xml_escape(case.setup_error or "unknown setup error")
            out.append(
                f'    <testcase classname="{xml_escape(case.case_name)}" '
                f'name="setup" time="{case.duration_seconds:.3f}">'
            )
            out.append(f'      <error message="setup failed">{err}</error>')
            out.append("    </testcase>")
        else:
            for r in case.drift_rows:
                tc_name = xml_escape(r.metric)
                out.append(
                    f'    <testcase classname="{xml_escape(case.case_name)}" '
                    f'name="{tc_name}" time="0.001">'
                )
                if not r.passed:
                    msg = (
                        f"drift {r.delta_pct * 100:.3f}% > tolerance "
                        f"{r.tolerance_pct * 100:.3f}% "
                        f"(expected {r.expected}, actual {r.actual})"
                    )
                    out.append(
                        f'      <failure message="{xml_escape(msg)}">{xml_escape(msg)}</failure>'
                    )
                out.append("    </testcase>")

        out.append("  </testsuite>")
    out.append("</testsuites>")
    return "\n".join(out) + "\n"


# ─────────────────────── orchestration ───────────────────────


def run_all(case_filter: Optional[str] = None) -> RunSummary:
    started = datetime.now(timezone.utc)
    cases = discover_cases()
    if case_filter:
        cases = [c for c in cases if c.name == case_filter]

    results: list[CaseResult] = []
    total_start = time.monotonic()
    for case_dir in cases:
        results.append(_check_case(case_dir))
    total_duration = time.monotonic() - total_start
    finished = datetime.now(timezone.utc)

    return RunSummary(
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        total_duration_seconds=total_duration,
        cases=results,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run a single case by directory name")
    parser.add_argument(
        "--report",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--junit-output",
        default=str(DEFAULT_JUNIT_PATH),
        help=(
            f"Where to write JUnit XML (default: {DEFAULT_JUNIT_PATH}). "
            "Pass '' to skip writing."
        ),
    )
    args = parser.parse_args()

    summary = run_all(case_filter=args.case)

    if args.report == "json":
        sys.stdout.write(emit_json_report(summary))
    else:
        sys.stdout.write(emit_text_report(summary))

    # Always write JUnit XML so GitHub's test-reporter can consume it
    # even when the run passes (gives a 'green' annotation per metric).
    if args.junit_output:
        try:
            Path(args.junit_output).write_text(emit_junit_xml(summary), encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"warning: could not write JUnit XML: {exc}\n")

    if summary.has_broken_cases:
        return EXIT_FIXTURES_BROKEN
    if summary.total_failed > 0:
        return EXIT_DRIFT
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
