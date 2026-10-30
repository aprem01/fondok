#!/usr/bin/env python3
"""FON-120 process guard — an underwriting behaviour change ships with its methodology.

Why
---
The Methodology page is the contract a client reads: what a tab shows, which
source wins, when a figure is withheld. The standing rule (go-live plan
ITEM C / memory) is that methodology changes in the SAME change set as any
underwriting behaviour change. Until now nothing enforced it; this is the
``pipeline-version`` guard (``check_pipeline_version.py``) applied to the
methodology package.

What it checks
--------------
Relative to a base commit (the merge-base with ``origin/main``; ``HEAD^`` on
a push to main; see ``--base``), the range is in violation when ALL hold:

* at least one changed file is an engine / runner / schema path
  (``RELEVANT_PREFIXES`` / ``RELEVANT_FILES`` — the places a behaviour change
  lands), and
* no changed file lives under ``packages/methodology/`` (the one source the
  page, the admin detail, the engineering reference and Atlas read), and
* no commit message in the range carries the trailer ``methodology: unchanged``
  (case-insensitive, own line) — the deliberate opt-out for a refactor, a
  comment-only or test-only change, or a bug fix that restores what the
  methodology already says.

Exit codes: 0 = ok; 1 = violation; 2 = could not determine a base.
Stdlib only — runs before ``uv sync`` in CI.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence

RELEVANT_PREFIXES: tuple[str, ...] = (
    "apps/worker/app/engines/",
    "apps/worker/app/services/stabilization",
)
RELEVANT_FILES: tuple[str, ...] = (
    "apps/worker/app/services/engine_runner.py",
    "apps/worker/app/services/index_methodology.py",
    "apps/worker/app/services/renovation_budget.py",
    "apps/worker/app/services/benchmark_suggestions.py",
    "apps/worker/app/services/deal_type.py",
    "apps/worker/app/api/model.py",
    "packages/schemas-py/fondok_schemas/underwriting.py",
    "packages/schemas-py/fondok_schemas/reasons.py",
)
METHODOLOGY_PREFIX = "packages/methodology/"
TRAILER_RE = re.compile(r"^\s*methodology\s*:\s*unchanged\s*$", re.IGNORECASE | re.MULTILINE)


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
    """Same resolution as ``check_pipeline_version.py``: ``--base``, else the
    merge-base with origin/main (then main), else ``HEAD^`` when on main."""
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


def changed_files(repo: str, base: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", base, "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def relevant_files(files: Sequence[str]) -> list[str]:
    return [f for f in files if f.startswith(RELEVANT_PREFIXES) or f in RELEVANT_FILES]


def methodology_files(files: Sequence[str]) -> list[str]:
    return [f for f in files if f.startswith(METHODOLOGY_PREFIX)]


def has_unchanged_trailer(repo: str, base: str) -> bool:
    body = _git(repo, "log", "--format=%B%x00", f"{base}..HEAD")
    return any(TRAILER_RE.search(msg) for msg in body.split("\x00") if msg.strip())


def check(repo: str, base: str) -> tuple[int, str]:
    files = changed_files(repo, base)
    relevant = relevant_files(files)
    short = base[:10]
    if not relevant:
        return 0, f"methodology: no engine / runner / schema files changed since {short} — ok"
    touched = methodology_files(files)
    lines = [
        f"methodology: {len(relevant)} engine / runner / schema file(s) changed since {short}:",
        *(f"  - {f}" for f in relevant),
    ]
    if touched:
        lines.append(f"  packages/methodology touched ({len(touched)} file(s)) — ok")
        return 0, "\n".join(lines)
    if has_unchanged_trailer(repo, base):
        lines.append("  'methodology: unchanged' trailer present — ok (deliberate: no behaviour the page describes moved)")
        return 0, "\n".join(lines)
    lines += [
        "",
        "FAIL: engine / runner / schema code changed but packages/methodology/ did not.",
        "      The Methodology page, its admin detail and the text Atlas reads must",
        "      describe the new behaviour in the same change set.",
        "",
        "  Fix: edit the owning section in packages/methodology/sections/<nn>-<id>.md",
        "  (then `uv run --project apps/worker python scripts/gen_methodology.py`),",
        "  or — only when no behaviour the page describes moved (refactor, comments,",
        "  tests, a fix restoring what the page already says) — add this trailer to",
        "  the commit message:",
        "",
        "      methodology: unchanged",
    ]
    return 1, "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("METHODOLOGY_CHECK_REPO", "."), help="repository root (default: cwd)")
    parser.add_argument("--base", default=os.environ.get("METHODOLOGY_CHECK_BASE"), help="explicit base ref; default: merge-base with origin/main (HEAD^ when on main)")
    args = parser.parse_args(argv)
    try:
        base = resolve_base(args.repo, args.base)
    except GitError as exc:
        print(f"methodology: cannot determine base commit: {exc}", file=sys.stderr)
        return 2
    try:
        code, report = check(args.repo, base)
    except GitError as exc:
        print(f"methodology: git failure: {exc}", file=sys.stderr)
        return 2
    print(report, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
