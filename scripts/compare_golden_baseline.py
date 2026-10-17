#!/usr/bin/env python3
"""Re-run the Kimpton golden fixture and diff it against a committed baseline.

Sam (2026-10-16): "preserve the current Kimpton model outputs so we can
distinguish expected changes from unintended calculation discrepancies during
regression testing."

The fixture is the Kimpton Angler seed — the non-UUID demo deal id
``kimpton-angler-2026``, which loads ``_kimpton_assumptions()`` defaults only
(no documents, no overrides) — run through ``run_all_engines`` exactly as
``apps/worker/tests/test_engine_runner.py::test_run_all_engines_kimpton``
does, against a throwaway SQLite database.

Usage (from the repo root)::

    apps/worker/.venv/bin/python scripts/compare_golden_baseline.py
    apps/worker/.venv/bin/python scripts/compare_golden_baseline.py --all
    apps/worker/.venv/bin/python scripts/compare_golden_baseline.py --full
    apps/worker/.venv/bin/python scripts/compare_golden_baseline.py \\
        --baseline evals/baselines/kimpton-golden-2026-10-16.json
    apps/worker/.venv/bin/python scripts/compare_golden_baseline.py \\
        --write evals/baselines/kimpton-golden-<date>.json   # new baseline

The headline section uses the same field list and differ as the live
``GET /deals/{id}/engines/compare`` endpoint
(``apps/worker/app/services/run_compare.py``). ``--full`` additionally lists
every other numeric leaf of the raw engine outputs that moved.

This is a REPORT: it always exits 0. Whether a listed change is expected is a
human call — compare it to the change you shipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "apps" / "worker"
DEFAULT_BASELINE = REPO / "evals" / "baselines" / "kimpton-golden-2026-10-16.json"
FIXTURE_DEAL_ID = "kimpton-angler-2026"


def _bootstrap() -> None:
    db = Path(tempfile.mkdtemp(prefix="fondok-golden-")) / "golden.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")
    for p in (WORKER, REPO / "packages" / "schemas-py"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


async def run_fixture() -> dict[str, dict[str, Any]]:
    """``{engine: {status, outputs}}`` for one fresh Kimpton chain run."""
    from uuid import uuid4

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import run_all_engines

    await run_startup_migrations()
    async with get_session_factory()() as session:
        results = await run_all_engines(
            session,
            deal_id=FIXTURE_DEAL_ID,
            tenant_id=str(uuid4()),
            run_id=str(uuid4()),
        )
    # Round-trip through JSON so the comparison sees exactly what a baseline
    # file holds (and what engine_outputs persists).
    raw = {
        name: {"status": r.get("status"), "outputs": r.get("outputs")}
        for name, r in sorted(results.items())
    }
    return json.loads(json.dumps(raw, default=str))


def _git_head() -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _leaves(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "provenance":
                continue
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_leaves(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def _fmt(v: Any, unit: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if unit == "pct":
        return f"{v * 100:.4f}%"
    if unit == "x":
        return f"{v:.4f}x"
    if unit == "usd":
        return f"${v:,.2f}"
    return f"{v:,.6g}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--all", action="store_true", help="also list unchanged headline fields")
    ap.add_argument("--full", action="store_true", help="also diff every raw numeric leaf")
    ap.add_argument("--write", type=Path, help="write a NEW baseline file instead of comparing")
    args = ap.parse_args()

    _bootstrap()
    head = asyncio.run(run_fixture())

    if args.write:
        from datetime import date

        doc = {
            "schema": "fondok.golden-baseline/1",
            "fixture": FIXTURE_DEAL_ID,
            "fixture_description": (
                "Kimpton Angler seed (non-UUID demo deal id -> "
                "_kimpton_assumptions() defaults only, no documents, no "
                "overrides), run through run_all_engines exactly as "
                "tests/test_engine_runner.py::test_run_all_engines_kimpton does."
            ),
            "generated_from_commit": _git_head(),
            "generated_on": date.today().isoformat(),
            "regenerate": "python scripts/compare_golden_baseline.py --write evals/baselines/<name>.json",
            "engines": head,
        }
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"wrote {args.write}")
        return 0

    from app.services.run_compare import diff_fields, extract_headline_fields

    baseline = json.loads(args.baseline.read_text())
    base_eng = baseline["engines"]

    print(f"Baseline : {args.baseline.relative_to(REPO) if args.baseline.is_relative_to(REPO) else args.baseline}")
    print(f"           generated from {baseline.get('generated_from_commit')} on {baseline.get('generated_on')}")
    print(f"Head     : working tree at {_git_head()}")
    print(f"Fixture  : {FIXTURE_DEAL_ID}")

    status_moves = [
        (n, base_eng.get(n, {}).get("status"), head.get(n, {}).get("status"))
        for n in sorted(set(base_eng) | set(head))
        if base_eng.get(n, {}).get("status") != head.get(n, {}).get("status")
    ]
    for n, b, h in status_moves:
        print(f"ENGINE STATUS CHANGED: {n}: {b} -> {h}")

    rows = diff_fields(
        extract_headline_fields({k: v.get("outputs") for k, v in base_eng.items()}),
        extract_headline_fields({k: v.get("outputs") for k, v in head.items()}),
        include_unchanged=args.all,
    )
    changed = [r for r in rows if r["status"] != "unchanged"]
    print()
    print(f"Headline fields changed: {len(changed)}")
    group = None
    for r in rows:
        if r["group"] != group:
            group = r["group"]
            print(f"\n[{group}]")
        pct = (
            f"{r['delta_pct'] * 100:+.4f}%" if r["delta_pct"] is not None
            else f"— ({r['delta_reason']})" if r["delta_reason"] else "—"
        )
        dabs = _fmt(r["delta_abs"], r["unit"]) if r["delta_abs"] is not None else "—"
        print(
            f"  {r['status']:<9} {r['label']:<52} "
            f"{_fmt(r['base_value'], r['unit']):>20} -> {_fmt(r['head_value'], r['unit']):<20} "
            f"Δ {dabs} ({pct})"
        )

    if args.full:
        print("\nRaw numeric leaves changed (provenance excluded):")
        n = 0
        for eng in sorted(set(base_eng) | set(head)):
            bl = _leaves(base_eng.get(eng, {}).get("outputs") or {})
            hl = _leaves(head.get(eng, {}).get("outputs") or {})
            for key in sorted(set(bl) | set(hl)):
                b, h = bl.get(key), hl.get(key)
                same = (
                    math.isclose(b, h, rel_tol=1e-9, abs_tol=1e-6)
                    if isinstance(b, (int, float)) and isinstance(h, (int, float))
                    and not isinstance(b, bool) and not isinstance(h, bool)
                    else b == h
                )
                if not same:
                    n += 1
                    print(f"  {eng}.{key}: {b!r} -> {h!r}")
        print(f"Raw leaves changed: {n}")

    if not changed and not status_moves:
        print("\nNo headline differences: the Kimpton fixture matches the baseline.")
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — a report never fails the caller
        print(f"compare_golden_baseline: could not complete the comparison: {exc!r}")
    sys.exit(0)
