#!/usr/bin/env python3
"""FON-88 process guard — extractor changes must bump EXTRACTION_PIPELINE_VERSION.

Why
---
``EXTRACTION_PIPELINE_VERSION`` (``apps/worker/app/api/documents.py``) is
baked into every persisted ``extraction_results.agent_version`` and into
every learned sibling-template mapping. The content-hash cache and the
sibling lookup treat "same version" as "same extractor": a change to an
extraction schema, an extractor prompt or a template extractor that ships
WITHOUT a bump keeps serving the OLD result for every re-upload
(2026-10-08: a stale v1 sibling mapping reproduced a partial field set
under v3 and the result was then cached).

What it checks
--------------
Relative to a base commit (the merge-base with ``origin/main``; see
``--base``), the commit range is in violation when ALL of these hold:

* at least one changed file lives under ``apps/worker/app/extraction/`` or
  ``apps/worker/app/agents/`` (see ``RELEVANT_PREFIXES``), and
* the value of ``EXTRACTION_PIPELINE_VERSION`` in
  ``apps/worker/app/api/documents.py`` is the same at the base and at HEAD,
  and
* no commit message in the range carries the trailer
  ``pipeline-version: unchanged`` (case-insensitive, own line) — the
  deliberate opt-out for refactors / comment-only / test-only changes
  inside those directories that do not alter extraction output.

Exit codes: 0 = nothing relevant changed, the version was bumped, or the
trailer is present; 1 = violation; 2 = could not determine a base.

Base resolution
---------------
1. ``--base <ref>`` / ``$PIPELINE_VERSION_BASE`` when given;
2. ``git merge-base HEAD origin/main`` (then ``main``);
3. when that merge-base IS HEAD (a push to main — the CI checkout is the
   tip of main itself), fall back to the first parent ``HEAD^`` so a merge
   commit is checked against the state before the merge.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence

RELEVANT_PREFIXES: tuple[str, ...] = (
    "apps/worker/app/extraction/",
    "apps/worker/app/agents/",
)
VERSION_FILE = "apps/worker/app/api/documents.py"
VERSION_RE = re.compile(
    r'^\s*EXTRACTION_PIPELINE_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE
)
TRAILER_RE = re.compile(r"^\s*pipeline-version\s*:\s*unchanged\s*$", re.IGNORECASE | re.MULTILINE)


class GitError(RuntimeError):
    pass


def _git(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
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
        # On main itself: compare against the state before this commit.
        parent = _rev_parse(repo, "HEAD^")
        if parent is not None:
            return parent
        raise GitError("HEAD has no parent to compare against")
    raise GitError("neither origin/main nor main is available — fetch with depth 0")


def changed_files(repo: str, base: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", base, "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def relevant_files(files: Sequence[str]) -> list[str]:
    return [f for f in files if f.startswith(RELEVANT_PREFIXES)]


def version_at(repo: str, ref: str) -> str | None:
    try:
        src = _git(repo, "show", f"{ref}:{VERSION_FILE}")
    except GitError:
        return None
    m = VERSION_RE.search(src)
    return m.group(1) if m else None


def has_unchanged_trailer(repo: str, base: str) -> bool:
    body = _git(repo, "log", "--format=%B%x00", f"{base}..HEAD")
    return any(TRAILER_RE.search(msg) for msg in body.split("\x00") if msg.strip())


def check(repo: str, base: str) -> tuple[int, str]:
    """Return ``(exit_code, report)``."""
    files = changed_files(repo, base)
    relevant = relevant_files(files)
    short = base[:10]
    if not relevant:
        return 0, f"pipeline-version: no extractor files changed since {short} — ok"
    before = version_at(repo, base)
    after = version_at(repo, "HEAD")
    lines = [
        f"pipeline-version: {len(relevant)} extractor file(s) changed since {short}:",
        *(f"  - {f}" for f in relevant),
        f"  EXTRACTION_PIPELINE_VERSION: {before!r} -> {after!r}",
    ]
    if before != after and after is not None:
        lines.append("  version bumped — ok")
        return 0, "\n".join(lines)
    if has_unchanged_trailer(repo, base):
        lines.append("  'pipeline-version: unchanged' trailer present — ok (deliberate no-bump)")
        return 0, "\n".join(lines)
    lines += [
        "",
        "FAIL: extraction schemas / extractor prompts / template extractors changed",
        f"      but EXTRACTION_PIPELINE_VERSION in {VERSION_FILE} did not.",
        "      Cached extraction_results and learned sibling mappings keyed on the old",
        "      version would keep serving the OLD extractor's output for every re-upload.",
        "",
        "  Fix: bump EXTRACTION_PIPELINE_VERSION (e.g. v8 -> v9), or — only when the",
        "  change provably does not alter extraction output (refactor, comments,",
        "  tests) — add this trailer to the commit message:",
        "",
        "      pipeline-version: unchanged",
    ]
    return 1, "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        default=os.environ.get("PIPELINE_VERSION_REPO", "."),
        help="repository root (default: cwd)",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PIPELINE_VERSION_BASE"),
        help="explicit base ref; default: merge-base with origin/main (HEAD^ when on main)",
    )
    args = parser.parse_args(argv)
    try:
        base = resolve_base(args.repo, args.base)
    except GitError as exc:
        print(f"pipeline-version: cannot determine base commit: {exc}", file=sys.stderr)
        return 2
    try:
        code, report = check(args.repo, base)
    except GitError as exc:
        print(f"pipeline-version: git failure: {exc}", file=sys.stderr)
        return 2
    print(report, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
