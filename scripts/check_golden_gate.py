#!/usr/bin/env python3
"""Kimpton golden gate — fail CI when the golden fixture moved without consent.

Why
---
``scripts/compare_golden_baseline.py`` re-runs the Kimpton Angler fixture
through every engine and diffs the headline fields against the committed
baseline. It is a REPORT: it exits 0 whether or not anything moved, so until
2026-10-30 somebody had to remember to run it and read it. A calculation
change that nobody meant to make could land on main unnoticed.

What it checks
--------------
Runs the compare script with ``--exit-nonzero-on-headline-diff`` and maps its
exit code:

* 0 — no headline differences: pass.
* 3 — headline fields or engine statuses differ. Pass ONLY when a commit in
  the range base..HEAD carries the trailer ``golden: expected`` (own line,
  case-insensitive) — the deliberate opt-in for a change that is supposed to
  move the model, which must ship a re-baselined
  ``evals/baselines/kimpton-golden-*.json`` in the same range. Otherwise FAIL.
* anything else — the comparison could not run (broken venv, failed
  migration, missing baseline): exit 2, never a pass.

Exit codes: 0 = pass, 1 = the fixture moved without the trailer, 2 = could not
compare (or could not determine a base commit when one was needed).

Base resolution (only needed when the fixture moved — mirrors
``check_pipeline_version.py``):
1. ``--base <ref>`` / ``$GOLDEN_GATE_BASE`` when given;
2. ``git merge-base HEAD origin/main`` (then ``main``);
3. when that merge-base IS HEAD (a push to main), the first parent ``HEAD^``.

Stdlib only. The compare script itself needs the worker venv, so run this as
``uv run --project apps/worker python scripts/check_golden_gate.py`` (CI) or
``apps/worker/.venv/bin/python scripts/check_golden_gate.py`` (laptop); the
interpreter running this file is reused for the compare unless
``--compare-cmd`` says otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPARE_SCRIPT = REPO / "scripts" / "compare_golden_baseline.py"
GATE_FLAG = "--exit-nonzero-on-headline-diff"
TRAILER = "golden: expected"
TRAILER_RE = re.compile(r"^\s*golden\s*:\s*expected\s*$", re.IGNORECASE | re.MULTILINE)

EXIT_PASS = 0
EXIT_MOVED = 1
EXIT_CANNOT_COMPARE = 2
COMPARE_EXIT_HEADLINE_DIFF = 3


class GitError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _rev_parse(repo: str, ref: str) -> str | None:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip() or None
    except GitError:
        return None


def resolve_base(repo: str, explicit: str | None = None) -> str:
    """See the module docstring, "Base resolution"."""
    if explicit:
        sha = _rev_parse(repo, explicit)
        if sha is None:
            raise GitError(f"base ref {explicit!r} does not resolve")
        return sha
    head = _rev_parse(repo, "HEAD")
    if head is None:
        raise GitError("HEAD does not resolve")
    for candidate in ("origin/main", "main"):
        if _rev_parse(repo, candidate) is None:
            continue
        try:
            base = _git(repo, "merge-base", "HEAD", candidate).strip()
        except GitError:
            continue
        if base and base != head:
            return base
        parent = _rev_parse(repo, "HEAD^")
        if parent is not None:
            return parent
        raise GitError("HEAD has no parent to compare against")
    raise GitError("neither origin/main nor main is available — fetch with depth 0")


def has_expected_trailer(repo: str, base: str) -> bool:
    """True when any commit message in ``base..HEAD`` carries ``golden: expected``."""
    body = _git(repo, "log", "--format=%B%x00", f"{base}..HEAD")
    return any(TRAILER_RE.search(msg) for msg in body.split("\x00") if msg.strip())


def default_compare_cmd() -> list[str]:
    return [sys.executable, str(COMPARE_SCRIPT), GATE_FLAG]


def run_compare(cmd: Sequence[str], repo: str) -> int:
    """Run the compare script, echoing its report, and return its exit code."""
    print(f"golden-gate: running {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
    proc = subprocess.run(list(cmd), cwd=repo)
    return proc.returncode


def gate(repo: str, compare_cmd: Sequence[str], explicit_base: str | None) -> tuple[int, str]:
    """Return ``(exit_code, verdict_line)``."""
    rc = run_compare(compare_cmd, repo)
    if rc == 0:
        return EXIT_PASS, "golden-gate: no headline differences — ok"
    if rc != COMPARE_EXIT_HEADLINE_DIFF:
        return EXIT_CANNOT_COMPARE, (
            f"golden-gate: the comparison could not run (compare exit {rc}) — "
            "this is NOT a pass; fix the environment and re-run"
        )
    try:
        base = resolve_base(repo, explicit_base)
    except GitError as exc:
        return EXIT_CANNOT_COMPARE, (
            f"golden-gate: the Kimpton fixture moved but the commit range could not be "
            f"determined ({exc}) — cannot look for the '{TRAILER}' trailer"
        )
    if has_expected_trailer(repo, base):
        return EXIT_PASS, (
            f"golden-gate: headline differences found and a commit in {base[:10]}..HEAD "
            f"carries '{TRAILER}' — ok (deliberate model change; make sure the "
            "re-baselined evals/baselines/kimpton-golden-*.json ships in this range)"
        )
    return EXIT_MOVED, "\n".join(
        [
            f"golden-gate: FAIL — the Kimpton fixture moved since {base[:10]} and no "
            f"commit in the range carries the '{TRAILER}' trailer.",
            "",
            "  Read the report above. If the change was not supposed to move the model,",
            "  fix the calculation. If it was, re-baseline:",
            "",
            "      apps/worker/.venv/bin/python scripts/compare_golden_baseline.py \\",
            "          --write evals/baselines/kimpton-golden-<date>.json",
            "",
            "  point DEFAULT_BASELINE at the new file, and put this trailer on its own",
            "  line at the end of the commit message:",
            "",
            f"      {TRAILER}",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        default=os.environ.get("GOLDEN_GATE_REPO", str(REPO)),
        help="repository root (default: the checkout this script lives in)",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("GOLDEN_GATE_BASE"),
        help="explicit base ref; default: merge-base with origin/main (HEAD^ when on main)",
    )
    parser.add_argument(
        "--compare-cmd",
        default=None,
        help=(
            "override the compare command (shell-split); default: this interpreter "
            f"running scripts/compare_golden_baseline.py {GATE_FLAG}"
        ),
    )
    args = parser.parse_args(argv)
    cmd = shlex.split(args.compare_cmd) if args.compare_cmd else default_compare_cmd()
    code, verdict = gate(args.repo, cmd, args.base)
    print(verdict, file=sys.stderr if code else sys.stdout, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
