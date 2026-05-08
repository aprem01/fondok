#!/usr/bin/env python3
"""End-to-end smoke runner — exercises every shipped surface.

Hits a live Fondok worker (local or production), creates a fresh
deal, uploads all five reference fixtures, waits for extraction,
runs the engine pipeline, and probes every read endpoint we ship
(variance, due-diligence, market-data, transaction-comps, dossier,
ask). Prints PASS/FAIL per check so you can eyeball coverage in
~2-3 minutes.

Usage::

    # Local (default — assumes `make dev` is running):
    python scripts/e2e_smoke.py

    # Production (Railway worker):
    WORKER_URL=https://fondok-worker-production.up.railway.app \\
        python scripts/e2e_smoke.py

    # Custom tenant:
    WORKER_URL=... TENANT_ID=<uuid> python scripts/e2e_smoke.py

Exit codes:
    0  — all checks passed (or the only failures are documented thin
         spots like the mock OM not carrying a comp-sales table)
    1  — a real surface failed; investigate before deploying

What's NOT tested here:
    * The web UI (use the click recipe in docs/DEMO_SCRIPT.md)
    * The Excel / PDF / PPTX export endpoints (separate suite)
    * SSE memo streaming (separate suite)

Fixtures used (in apps/worker/tests/fixtures + a legacy mock OM/T-12):
    * sample_OM.pdf            — synthetic 13KB OM (THIN — no comp
                                  sales table; transaction-comps will
                                  surface the empty state)
    * sample_T12.pdf           — synthetic 6KB T-12 (THIN — extraction
                                  will land few fields)
    * sample_str_trend.xls     — REAL Rosewood Miami Beach trend
    * sample_cbre_horizons.pdf — REAL Seattle Q3 2024 Hotel Horizons
    * sample_pnl_benchmark.pdf — REAL CBRE Benchmarker P&L
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_URL = os.environ.get("WORKER_URL", "http://localhost:8001").rstrip("/")
TENANT_ID = os.environ.get("TENANT_ID")  # optional X-Tenant-Id header

# Reference fixtures. Worker fixtures ship the heavyweights; the OM /
# T-12 mocks live in apps/web/public/test-documents because they're
# small enough to ride alongside the static UI assets.
WORKER_FIXTURES = REPO_ROOT / "apps" / "worker" / "tests" / "fixtures"
WEB_FIXTURES = REPO_ROOT / "apps" / "web" / "public" / "test-documents"
FIXTURE_FILES: list[tuple[str, Path, str]] = [
    ("sample_OM.pdf", WEB_FIXTURES / "sample_OM.pdf", "application/pdf"),
    ("sample_T12.pdf", WEB_FIXTURES / "sample_T12.pdf", "application/pdf"),
    (
        "sample_cbre_horizons.pdf",
        WORKER_FIXTURES / "sample_cbre_horizons.pdf",
        "application/pdf",
    ),
    (
        "sample_pnl_benchmark.pdf",
        WORKER_FIXTURES / "sample_pnl_benchmark.pdf",
        "application/pdf",
    ),
    (
        "sample_str_trend.xls",
        WORKER_FIXTURES / "sample_str_trend.xls",
        "application/vnd.ms-excel",
    ),
]

# ANSI escapes for the result table.
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


class Result:
    """Per-check status accumulator."""

    def __init__(self) -> None:
        self.passes: list[tuple[str, str]] = []
        self.fails: list[tuple[str, str]] = []
        self.warns: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passes.append((name, detail))
        print(f"  {GREEN}✓{RESET} {name}{(': ' + DIM + detail + RESET) if detail else ''}")

    def fail(self, name: str, detail: str) -> None:
        self.fails.append((name, detail))
        print(f"  {RED}✗{RESET} {name}: {RED}{detail}{RESET}")

    def warn(self, name: str, detail: str) -> None:
        self.warns.append((name, detail))
        print(f"  {YELLOW}!{RESET} {name}: {YELLOW}{detail}{RESET}")

    def summary(self) -> int:
        total = len(self.passes) + len(self.fails) + len(self.warns)
        print()
        print(
            f"{BOLD}Summary{RESET}: "
            f"{GREEN}{len(self.passes)} passed{RESET}, "
            f"{RED}{len(self.fails)} failed{RESET}, "
            f"{YELLOW}{len(self.warns)} warned{RESET} (total {total})"
        )
        if self.fails:
            print(f"\n{RED}Failures:{RESET}")
            for name, detail in self.fails:
                print(f"  - {name}: {detail}")
            return 1
        return 0


async def main() -> int:  # noqa: PLR0912, PLR0915
    print(f"{BOLD}Fondok E2E Smoke{RESET} — worker = {WORKER_URL}")
    print(f"  fixtures: {WORKER_FIXTURES.relative_to(REPO_ROOT)} + {WEB_FIXTURES.relative_to(REPO_ROOT)}")
    print()

    r = Result()
    headers: dict[str, str] = {}
    if TENANT_ID:
        headers["X-Tenant-Id"] = TENANT_ID

    async with httpx.AsyncClient(
        base_url=WORKER_URL, headers=headers, timeout=120.0
    ) as client:
        # ───── 1. health ──────────────────────────────────────────────
        print(f"{BOLD}1. Health{RESET}")
        try:
            res = await client.get("/health")
            if res.status_code == 200:
                body = res.json()
                r.ok(
                    "health endpoint",
                    f"version={body.get('version', '?')} db={body.get('db', '?')}",
                )
            else:
                r.fail("health endpoint", f"HTTP {res.status_code}")
                return r.summary()
        except Exception as exc:  # noqa: BLE001
            r.fail(
                "health endpoint",
                f"{exc} — is the worker running at {WORKER_URL}?",
            )
            return r.summary()

        # ───── 2. create deal ─────────────────────────────────────────
        print(f"\n{BOLD}2. Create deal{RESET}")
        try:
            res = await client.post(
                "/deals",
                json={"name": "E2E Smoke Test", "city": "Seattle", "keys": 200},
            )
            res.raise_for_status()
            deal = res.json()
            deal_id = deal["id"]
            r.ok("POST /deals", f"deal_id={deal_id}")
        except Exception as exc:  # noqa: BLE001
            r.fail("POST /deals", str(exc))
            return r.summary()

        # ───── 3. upload fixtures ─────────────────────────────────────
        print(f"\n{BOLD}3. Upload fixtures (5 files){RESET}")
        files_payload: list[tuple[str, tuple[str, bytes, str]]] = []
        skipped: list[str] = []
        for name, path, mime in FIXTURE_FILES:
            if not path.exists():
                skipped.append(name)
                continue
            files_payload.append(("files", (name, path.read_bytes(), mime)))
        if skipped:
            r.warn("fixtures", f"missing on disk: {', '.join(skipped)}")
        try:
            res = await client.post(
                f"/deals/{deal_id}/documents/upload", files=files_payload
            )
            res.raise_for_status()
            uploaded = res.json()
            r.ok("upload", f"{len(uploaded)} files queued for parse + extract")
        except Exception as exc:  # noqa: BLE001
            r.fail("upload", str(exc))
            return r.summary()

        # ───── 4. wait for extraction ─────────────────────────────────
        print(f"\n{BOLD}4. Wait for extraction (background pipeline){RESET}")
        target_count = len(uploaded)
        deadline = time.time() + 240.0
        last_status = ""
        extracted = 0
        while time.time() < deadline:
            res = await client.get(f"/deals/{deal_id}/documents")
            docs = res.json() if res.status_code == 200 else []
            extracted = sum(
                1 for d in docs if (d.get("status") or "").upper() == "EXTRACTED"
            )
            failed = sum(
                1 for d in docs if (d.get("status") or "").upper() == "FAILED"
            )
            statuses = ",".join(d.get("status", "?") for d in docs)
            if statuses != last_status:
                print(f"    {DIM}{statuses}{RESET}")
                last_status = statuses
            if extracted + failed >= target_count:
                break
            await asyncio.sleep(3.0)
        if extracted == target_count:
            r.ok("extraction", f"{extracted}/{target_count} EXTRACTED")
        elif extracted > 0:
            r.warn("extraction", f"only {extracted}/{target_count} EXTRACTED")
        else:
            r.fail("extraction", f"0/{target_count} reached EXTRACTED in 240s")

        # Spot-check parser labels — XLS must hit xlrd, not the PDF path.
        res = await client.get(f"/deals/{deal_id}/documents")
        docs = res.json() if res.status_code == 200 else []
        xls_doc = next(
            (d for d in docs if d.get("filename", "").endswith(".xls")), None
        )
        if xls_doc:
            if xls_doc.get("parser") == "xlrd":
                r.ok("XLS parser", "sample_str_trend.xls → xlrd ✓")
            else:
                r.fail(
                    "XLS parser",
                    f"expected xlrd, got {xls_doc.get('parser')}",
                )

        # ───── 5. run all engines ────────────────────────────────────
        print(f"\n{BOLD}5. Engine pipeline (8 engines, dependency-ordered){RESET}")
        try:
            res = await client.post(f"/deals/{deal_id}/engines/run")
            res.raise_for_status()
            kickoff = res.json()
            run_id = kickoff["run_id"]
            r.ok("POST /engines/run", f"run_id={run_id[:8]}…")
        except Exception as exc:  # noqa: BLE001
            r.fail("POST /engines/run", str(exc))
            return r.summary()

        deadline = time.time() + 300.0
        last_complete = -1
        while time.time() < deadline:
            res = await client.get(f"/deals/{deal_id}/engines/run/{run_id}")
            run = res.json() if res.status_code == 200 else {}
            engines = run.get("engines", [])
            done = sum(1 for e in engines if e.get("status") == "complete")
            failed = sum(1 for e in engines if e.get("status") == "failed")
            if done != last_complete:
                print(f"    {DIM}{done}/{len(engines)} complete, {failed} failed{RESET}")
                last_complete = done
            if done + failed >= len(engines) and len(engines) > 0:
                break
            await asyncio.sleep(2.0)
        if done == len(engines) and len(engines) > 0:
            r.ok("engine completion", f"{done}/{len(engines)} complete")
        else:
            r.fail("engine completion", f"{done}/{len(engines)} complete, {failed} failed")

        # ───── 6. read endpoints ─────────────────────────────────────
        print(f"\n{BOLD}6. Read endpoints{RESET}")

        async def probe(label: str, path: str, validate=lambda j: True) -> Any:
            try:
                res = await client.get(path)
                if res.status_code != 200:
                    r.fail(label, f"HTTP {res.status_code}")
                    return None
                body = res.json()
                ok, detail = validate(body)
                if ok:
                    r.ok(label, detail)
                else:
                    r.warn(label, detail)
                return body
            except Exception as exc:  # noqa: BLE001
                r.fail(label, str(exc))
                return None

        await probe(
            "GET /engines",
            f"/deals/{deal_id}/engines",
            lambda b: (
                len(b.get("engines", {})) >= 8,
                f"{len(b.get('engines', {}))} engines",
            ),
        )
        await probe(
            "GET /analysis/variance",
            f"/analysis/{deal_id}/variance",
            lambda b: (
                "flags" in b,
                f"{len(b.get('flags', []))} flags "
                f"({b.get('critical_count', 0)}c/{b.get('warn_count', 0)}w/{b.get('info_count', 0)}i)",
            ),
        )
        await probe(
            "GET /due-diligence",
            f"/deals/{deal_id}/due-diligence",
            lambda b: (
                "questions" in b,
                f"{b.get('total', 0)} questions "
                f"({b.get('high_priority', 0)} high)",
            ),
        )
        await probe(
            "GET /market/transaction-comps",
            f"/market/{deal_id}/transaction-comps",
            lambda b: (
                "comps" in b,
                (
                    f"{len(b.get('comps', []))} comps · "
                    f"median $/key {b.get('median_price_per_key')} · "
                    f"median cap {b.get('median_cap_rate_pct')}"
                )
                if b.get("comps")
                else "0 comps (expected — mock OM has no Comparable Sales table)",
            ),
        )
        await probe(
            "GET /market-data",
            f"/deals/{deal_id}/market-data",
            lambda b: ("sources" in b, f"sources={list(b.get('sources', {}).keys())}"),
        )
        await probe(
            "GET /dossier",
            f"/deals/{deal_id}/dossier",
            lambda b: (True, "dossier returned"),
        )

        # ───── 7. AI NOI Summary (LLM-grounded /ask) ─────────────────
        print(f"\n{BOLD}7. Grounded Q&A (powers AI NOI Summary){RESET}")
        try:
            res = await client.post(
                f"/deals/{deal_id}/ask",
                json={
                    "question": "Summarize the deal NOI trajectory in 2 sentences."
                },
                timeout=120.0,
            )
            if res.status_code == 200:
                body = res.json()
                ans = (body.get("answer") or "").strip()
                cites = body.get("citations") or []
                if ans:
                    preview = ans.replace("\n", " ")[:80]
                    r.ok("/ask", f"answer={len(ans)}c, {len(cites)} citations: {preview}…")
                else:
                    r.warn("/ask", "empty answer body")
            else:
                r.fail("/ask", f"HTTP {res.status_code}")
        except Exception as exc:  # noqa: BLE001
            r.fail("/ask", str(exc))

        # ───── 8. patch deal (room-count override) ───────────────────
        print(f"\n{BOLD}8. Room-count override (PATCH /deals){RESET}")
        try:
            res = await client.patch(f"/deals/{deal_id}", json={"keys": 250})
            res.raise_for_status()
            patched = res.json()
            if patched.get("keys") == 250:
                r.ok("PATCH /deals keys=250", "deal row updated")
            else:
                r.fail("PATCH /deals", f"server returned keys={patched.get('keys')}")
        except Exception as exc:  # noqa: BLE001
            r.fail("PATCH /deals", str(exc))

    return r.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
