"""Chunk-size benchmark harness — Task U (cost-opt-tier2-u, 2026-07).

WHAT
----
Measures the tokens × wall-time × extraction quality tradeoff at a
handful of candidate chunk sizes so ``EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE``
can be tuned empirically instead of guessed.

For each candidate size ``k`` in ``--sizes`` we:

1. Parse the fixture through ``parse_document`` (offline, always
   deterministic — the parser cache is the same regardless of chunk
   size).
2. Build ``_build_extractor_chunks(..., chunk_pages=k)`` — the same
   entrypoint production uses, with the size forced.
3. Run ``run_extractor`` ``--reps`` times (default 2) against Anthropic
   to sample noise. Each rep records input / output tokens (including
   cache_creation / cache_read), wall-clock, USALI score, field count.
4. Aggregates mean cost, mean latency, mean score, mean fields across
   reps and prints a table.

GATING
------
This is a spend-money harness — it hits Anthropic. It skips cleanly
(exit 0) when ``ANTHROPIC_API_KEY`` isn't set so CI-adjacent runs
don't fail. Pass ``--dry-run`` to exercise the chunk-builder /
tokenizer plumbing without any LLM call (useful as a smoke-test).

USAGE
-----
    cd apps/worker
    ANTHROPIC_API_KEY=sk-... uv run --active python \
        scripts/bench_chunk_size.py --case anglers_t12 --sizes 3,5,8 --reps 2

Run against every P&L-ish case in the golden manifest::

    uv run --active python scripts/bench_chunk_size.py --all-pnl

Output is a plain-text table plus a JSON dump at
``/tmp/bench_chunk_size_<case>.json`` so a follow-up sweep can diff.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "evals" / "golden-set" / "documents"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"


def _isolate_env() -> None:
    """Force SQLite + stub secrets so the worker boots without prod state.

    The bench doesn't touch the DB, but importing the app modules will
    validate ``Settings`` at import time — a stray ``DATABASE_URL``
    pointing at prod would leak.
    """
    tmp_db = Path(tempfile.gettempdir()) / "fondok-bench-chunk-size.db"
    if tmp_db.exists():
        tmp_db.unlink()
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_db}"
    os.environ["DOCUMENT_STORAGE_ROOT"] = str(
        Path(tempfile.gettempdir()) / "fondok-bench-storage"
    )


def _load_case(case_id: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / f"{case_id}.expected.json").read_text())


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def _p_and_l_cases() -> list[str]:
    manifest = _load_manifest()
    out: list[str] = []
    for cid in manifest["cases"]:
        case = _load_case(cid)
        if case.get("usali") and case.get("extraction_payload_path"):
            out.append(cid)
    return out


async def _bench_one_rep(
    *,
    case: dict[str, Any],
    chunk_pages: int,
    parsed_pages: list[dict[str, Any]],
    filename: str,
    doc_type: str,
) -> dict[str, Any]:
    """Run one rep of extraction at the given chunk size.

    Returns a dict of measurements — the caller aggregates across reps.
    """
    from app.agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
    )
    from app.api.documents import _build_extractor_chunks
    from app.budget import estimate_spent_usd
    from app.services.usali_scorer import (
        flatten_extraction_fields,
        score_extraction,
    )

    chunks = _build_extractor_chunks(
        pages=parsed_pages,
        doc_id=f"bench-{case['case_id']}",
        filename=filename,
        doc_type=doc_type,
        make_doc=ExtractorDocument,
        chunk_pages=chunk_pages,
    )

    payload = ExtractorInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=f"bench-{case['case_id']}-{chunk_pages}",
        documents=chunks,
    )

    t0 = time.monotonic()
    out = await run_extractor(payload)
    wall = time.monotonic() - t0

    # Merge fields the same way production does.
    by_name: dict[str, dict[str, Any]] = {}
    for doc in out.extracted_documents or []:
        for f in doc.fields or []:
            fd = f.model_dump() if hasattr(f, "model_dump") else dict(f)
            name = fd.get("field_name")
            if not name:
                continue
            existing = by_name.get(name)
            if existing is None or (
                float(fd.get("confidence", 0) or 0)
                > float(existing.get("confidence", 0) or 0)
            ):
                by_name[name] = fd
    merged_fields = list(by_name.values())

    # USALI score (only if the fixture is a P&L-ish doc).
    usali_score: float | None = None
    applicable = 0
    if case.get("usali"):
        flat = flatten_extraction_fields(merged_fields)
        score = score_extraction(flat)
        usali_score = score.score
        applicable = score.applicable_rules

    # Token accounting from model_calls.
    in_tok = sum(int(getattr(c, "input_tokens", 0) or 0) for c in out.model_calls)
    out_tok = sum(int(getattr(c, "output_tokens", 0) or 0) for c in out.model_calls)
    cache_read = sum(
        int(getattr(c, "cache_read_input_tokens", 0) or 0) for c in out.model_calls
    )
    cache_create = sum(
        int(getattr(c, "cache_creation_input_tokens", 0) or 0)
        for c in out.model_calls
    )
    cost = estimate_spent_usd(list(out.model_calls))

    return {
        "chunk_pages": chunk_pages,
        "chunks": len(chunks),
        "wall_seconds": wall,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_create,
        "cost_usd": cost,
        "field_count": len(merged_fields),
        "usali_score": usali_score,
        "usali_applicable_rules": applicable,
        "success": bool(out.success),
        "error": out.error,
    }


async def _bench_case(
    *, case_id: str, sizes: list[int], reps: int, dry_run: bool
) -> dict[str, Any]:
    from app.extraction import parse_document

    case = _load_case(case_id)
    fx = REPO_ROOT / case["fixture_path"]
    if not fx.exists():
        print(f"[skip] fixture missing: {fx}", file=sys.stderr)
        return {"case_id": case_id, "skipped": True, "reason": "missing_fixture"}

    print(f"\n=== Bench case: {case_id} ({fx.name}) ===")
    print(f"  Parsing fixture ({fx.stat().st_size:,} bytes) ...")
    t0 = time.monotonic()
    parsed = await parse_document(fx.read_bytes(), fx.name)
    print(
        f"  Parsed: {parsed.total_pages} pages, "
        f"{sum(len(p.text or '') for p in parsed.pages):,} chars, "
        f"{time.monotonic() - t0:.2f}s"
    )

    # Coerce parser output → the dict shape ``_build_extractor_chunks``
    # expects. Production reads this shape off the documents.extraction_data
    # JSONB; the parse-time model uses attribute access.
    pages_dicts = [
        {"page_num": p.page_num, "text": p.text or ""} for p in parsed.pages
    ]

    doc_type = str(case.get("expected_doc_type") or "OM")

    if dry_run:
        # No LLM call — just show what chunks would look like.
        rows: list[dict[str, Any]] = []
        from app.agents.extractor import ExtractorDocument
        from app.api.documents import _build_extractor_chunks

        for k in sizes:
            chunks = _build_extractor_chunks(
                pages=pages_dicts,
                doc_id=f"bench-{case_id}",
                filename=fx.name,
                doc_type=doc_type,
                make_doc=ExtractorDocument,
                chunk_pages=k,
            )
            total_chars = sum(len(c.content) for c in chunks)
            rows.append(
                {
                    "chunk_pages": k,
                    "chunks": len(chunks),
                    "total_chars": total_chars,
                    "mean_chunk_chars": total_chars / max(len(chunks), 1),
                }
            )
        _print_dry_table(case_id, rows)
        return {"case_id": case_id, "dry_run": True, "rows": rows}

    results: list[dict[str, Any]] = []
    for k in sizes:
        rep_rows: list[dict[str, Any]] = []
        for rep in range(reps):
            print(f"  chunk_pages={k}  rep {rep + 1}/{reps} ...", flush=True)
            row = await _bench_one_rep(
                case=case,
                chunk_pages=k,
                parsed_pages=pages_dicts,
                filename=fx.name,
                doc_type=doc_type,
            )
            rep_rows.append(row)
        agg = _aggregate(rep_rows)
        agg["chunk_pages"] = k
        agg["reps"] = reps
        results.append(agg)

    _print_bench_table(case_id, results)

    out_path = Path(tempfile.gettempdir()) / f"bench_chunk_size_{case_id}.json"
    out_path.write_text(json.dumps({"case_id": case_id, "results": results}, indent=2))
    print(f"  wrote {out_path}")
    return {"case_id": case_id, "results": results, "json_path": str(out_path)}


def _mean(vals: list[float]) -> float:
    vals = [v for v in vals if v is not None]
    return statistics.fmean(vals) if vals else 0.0


def _aggregate(reps: list[dict[str, Any]]) -> dict[str, Any]:
    def m(k: str) -> float:
        return _mean([float(r.get(k) or 0) for r in reps])

    # USALI score handled specially — None is a valid outcome.
    usali_vals = [r.get("usali_score") for r in reps if r.get("usali_score") is not None]
    return {
        "chunks": int(_mean([r["chunks"] for r in reps])),
        "wall_seconds_mean": m("wall_seconds"),
        "input_tokens_mean": m("input_tokens"),
        "output_tokens_mean": m("output_tokens"),
        "cache_read_tokens_mean": m("cache_read_tokens"),
        "cache_creation_tokens_mean": m("cache_creation_tokens"),
        "cost_usd_mean": m("cost_usd"),
        "field_count_mean": m("field_count"),
        "usali_score_mean": statistics.fmean(usali_vals) if usali_vals else None,
        "usali_applicable_mean": m("usali_applicable_rules"),
        "success_rate": sum(1 for r in reps if r["success"]) / max(len(reps), 1),
    }


def _print_dry_table(case_id: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n--- Dry-run chunking table for {case_id} ---")
    print(
        f"{'k':>4} | {'chunks':>7} | {'total_chars':>12} | {'mean_chunk_chars':>16}"
    )
    print("-" * 52)
    for r in rows:
        print(
            f"{r['chunk_pages']:>4} | {r['chunks']:>7} | "
            f"{r['total_chars']:>12,} | {r['mean_chunk_chars']:>16,.0f}"
        )


def _print_bench_table(case_id: str, results: list[dict[str, Any]]) -> None:
    print(f"\n--- Bench results for {case_id} ---")
    header = (
        f"{'k':>3} | {'chunks':>6} | {'wall_s':>7} | {'in_tok':>9} | "
        f"{'out_tok':>7} | {'cost_$':>8} | {'fields':>6} | {'usali':>6} | {'success':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        usali = f"{r['usali_score_mean']:.1f}" if r["usali_score_mean"] is not None else "  n/a"
        print(
            f"{r['chunk_pages']:>3} | {r['chunks']:>6} | "
            f"{r['wall_seconds_mean']:>7.2f} | {r['input_tokens_mean']:>9,.0f} | "
            f"{r['output_tokens_mean']:>7,.0f} | {r['cost_usd_mean']:>8.4f} | "
            f"{r['field_count_mean']:>6,.0f} | {usali:>6} | "
            f"{r['success_rate']:>7.0%}"
        )


def _parse_sizes(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        default=None,
        help="Single case_id from the golden manifest. Ignored when --all-pnl is set.",
    )
    parser.add_argument(
        "--all-pnl",
        action="store_true",
        help="Bench every P&L-ish case with a pinned USALI expectation.",
    )
    parser.add_argument(
        "--sizes",
        default="3,5,8",
        help="Comma-sep chunk sizes to try (default: 3,5,8).",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=2,
        help="Reps per size for noise averaging (default: 2). 3 gives tighter stats.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the LLM call — only show chunk shapes / char counts.",
    )
    args = parser.parse_args()

    _isolate_env()

    sizes = _parse_sizes(args.sizes)
    if not sizes:
        parser.error("--sizes cannot be empty")

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "[skip] ANTHROPIC_API_KEY not set — pass --dry-run to smoke-test "
            "the harness without hitting Anthropic.",
            file=sys.stderr,
        )
        return 0

    if args.all_pnl:
        cases = _p_and_l_cases()
    elif args.case:
        cases = [args.case]
    else:
        parser.error("pass --case <id> or --all-pnl")

    async def _run() -> None:
        for cid in cases:
            await _bench_case(
                case_id=cid, sizes=sizes, reps=args.reps, dry_run=args.dry_run
            )

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
