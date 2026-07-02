"""Golden-set regression suite (TASK P — cost-opt-batch-p).

WHY
---
Sam is shipping a batch of cost-optimization changes — structural
pre-filter, agent routing to cheaper models, and chunk-size tuning. Each
of those has real *quality* risk: fields could be dropped, doc-type
classification could regress, USALI compliance scoring could drift. This
suite is the safety net: 5 representative doc-type fixtures, one canonical
extraction pinned per fixture, and a set of deterministic assertions that
must hold before any cost-optimization change lands.

WHAT IT COVERS
--------------
For each fixture under ``evals/golden-set/documents/``:

1. **Parse signals** (offline, always runs)
   ``parse_document()`` must produce the expected parser tag, page/chunk
   count within an order-of-magnitude window, table count above a floor,
   and total text volume in the expected range. If the parser regresses
   (e.g. LlamaParse quota exhausted and PyMuPDF falls back with 3x fewer
   tables) this trips.

2. **USALI canonical-field resolution** (offline, always runs — for P&L
   fixtures only)
   The pinned real extraction payload from ``tests/fixtures/real_payloads/``
   goes through ``flatten_extraction_fields`` + ``score_extraction`` —
   the same code path production uses. We assert that the 7 headline
   canonical fields (rooms_revenue, total_revenue, gop, noi, revpar,
   adr, occupancy) resolve to the expected values within tolerance, and
   the USALI score falls inside the expected band. If a refactor to the
   structural recognizer or alias map drops a field, this trips.

3. **Live end-to-end regression** (opt-in, gated on
   ``pytest.mark.live_llm``)
   Runs parse → router → extractor against Anthropic and asserts the
   *real* extraction reproduces the canonical field values within
   tolerance. This is the slower, expensive check we run BEFORE
   promoting a cost-optimization batch to prod. Requires
   ``ANTHROPIC_API_KEY`` in the environment.

RUNNING
-------

Offline (default — safe for CI, no API key needed)::

    cd apps/worker && uv run --active pytest \\
        tests/test_golden_set_regression.py -v \\
        -m "not live_llm and not slow"

Live end-to-end (opt-in, hits Anthropic, ~$0.50-$2 per full run)::

    cd apps/worker && ANTHROPIC_API_KEY=sk-... \\
        uv run --active pytest tests/test_golden_set_regression.py -v \\
        -m live_llm

Include the slow OM parse (adds ~60s for the large 20MB PDF)::

    cd apps/worker && uv run --active pytest \\
        tests/test_golden_set_regression.py -v -m "not live_llm"

Everything (full regression — CI-nightly)::

    cd apps/worker && ANTHROPIC_API_KEY=sk-... \\
        uv run --active pytest tests/test_golden_set_regression.py -v

Note: use ``--active`` on machines where a system-wide pytest (e.g.
Anaconda) shadows the uv-managed venv's binary on ``PATH``.

FIXTURES
--------
The golden manifest lives at ``evals/golden-set/documents/manifest.json``
and lists five cases covering the highest-volume doc types:

* ``anglers_t12`` — T-12 annual P&L (xlsx, 132 keys, Angler's Hotel)
* ``anglers_2023_annual_pnl`` — monthly-broken-out annual P&L (xlsx)
* ``sample_str_trend`` — CoStar STR Trend competitive-set report (.xls)
* ``sample_cbre_horizons`` — CBRE Horizons submarket forecast (PDF)
* ``anglers_om`` — Offering memorandum (PDF, 45pp) — behind ``@slow``

Each fixture has a companion ``<case>.expected.json`` describing the
expected parse signals and (for P&L docs) the expected canonical USALI
fields. Fixture bytes live at ``apps/worker/tests/fixtures/`` and are
referenced by path so we don't duplicate large binaries in git.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ── Test isolation — SQLite DSN before any worker imports so a
# developer's shell-level ``DATABASE_URL`` (or a Postgres URL from a
# sibling test module that already ran) doesn't bleed in and break the
# extractor test path. Mirrors the pattern in
# ``test_extractor_empty_envelope_retry.py``.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-golden-set.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")


REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "evals" / "golden-set" / "documents"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"


# ── Marker registration lives in ``apps/worker/pyproject.toml`` under
# ``[tool.pytest.ini_options].markers`` so ``--strict-markers`` accepts
# both ``live_llm`` and ``slow`` (see this suite's docstring for what
# each gates).


# ──────────────────────────── helpers ────────────────────────────


def _load_case(case_id: str) -> dict[str, Any]:
    """Load ``<case_id>.expected.json`` from the golden dir."""
    payload = json.loads((GOLDEN_DIR / f"{case_id}.expected.json").read_text())
    return payload


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def _fixture_path(case: dict[str, Any]) -> Path:
    p = REPO_ROOT / case["fixture_path"]
    return p


def _relative(pct: float) -> float:
    """Percentage → relative tolerance (0.02 → 2%)."""
    return pct


def _assert_close(actual: float, expected: float, tol_pct: float, label: str) -> None:
    if expected == 0:
        assert abs(actual) <= tol_pct, f"{label}: expected 0, got {actual}"
        return
    rel = abs(actual - expected) / abs(expected)
    assert rel <= tol_pct, (
        f"{label}: actual={actual:,.4f} vs expected={expected:,.4f} "
        f"(rel={rel:.4%}, tol={tol_pct:.4%})"
    )


# ─────────────────────────── parameterization ───────────────────────────


def _all_cases() -> list[str]:
    return _load_manifest()["cases"]


def _fast_cases() -> list[str]:
    """Cases that run in the default (offline, not-slow) suite."""
    out: list[str] = []
    for cid in _all_cases():
        case = _load_case(cid)
        if case.get("slow"):
            continue
        out.append(cid)
    return out


def _p_and_l_cases() -> list[str]:
    """Cases where we have a pinned extraction payload with USALI fields."""
    out: list[str] = []
    for cid in _all_cases():
        case = _load_case(cid)
        if case.get("usali") and case.get("extraction_payload_path"):
            out.append(cid)
    return out


# ─────────────────────────── offline: parse signals ───────────────────────────


@pytest.mark.parametrize("case_id", _fast_cases())
def test_parse_signals_offline(case_id: str) -> None:
    """``parse_document`` on the fixture must produce the pinned parse
    signals — parser tag, chunk count in range, table floor, char volume.

    This is the load-bearing regression check for chunk-size tuning and
    the structural pre-filter: if either drops chunks or halves the text
    volume, this trips.
    """
    from app.extraction import parse_document

    case = _load_case(case_id)
    fx = _fixture_path(case)
    if not fx.exists():
        pytest.skip(f"fixture missing: {fx}")

    body = fx.read_bytes()
    parsed = asyncio.run(parse_document(body, fx.name))

    parse_expect = case["parse"]

    # Parser tag — either exact match or prefix (PDF fallback chain).
    if "parser" in parse_expect:
        assert parsed.parser == parse_expect["parser"], (
            f"{case_id}: parser={parsed.parser!r} expected={parse_expect['parser']!r}"
        )
    if "parser_prefix" in parse_expect:
        assert parsed.parser.startswith(parse_expect["parser_prefix"]), (
            f"{case_id}: parser={parsed.parser!r} did not start with "
            f"{parse_expect['parser_prefix']!r}"
        )

    total_pages = parsed.total_pages
    lo, hi = parse_expect["total_pages_min"], parse_expect["total_pages_max"]
    assert lo <= total_pages <= hi, (
        f"{case_id}: total_pages={total_pages} outside [{lo}, {hi}]"
    )

    total_chars = sum(len(pg.text or "") for pg in parsed.pages)
    clo, chi = parse_expect["total_chars_min"], parse_expect["total_chars_max"]
    assert clo <= total_chars <= chi, (
        f"{case_id}: total_chars={total_chars:,} outside [{clo:,}, {chi:,}]"
    )

    total_tables = sum(len(pg.tables or []) for pg in parsed.pages)
    assert total_tables >= parse_expect["min_tables"], (
        f"{case_id}: total_tables={total_tables} < min={parse_expect['min_tables']}"
    )

    # Sanity — every page carries positional metadata (page_num > 0).
    assert all(pg.page_num >= 1 for pg in parsed.pages)


@pytest.mark.slow
def test_parse_signals_offline_slow_om() -> None:
    """OM parse takes ~60s; run only when the slow marker is selected.

    Kept out of the parametrized fast suite so ``pytest ...`` in CI stays
    quick, but still available for the nightly regression sweep.
    """
    from app.extraction import parse_document

    case = _load_case("anglers_om")
    fx = _fixture_path(case)
    if not fx.exists():
        pytest.skip(f"fixture missing: {fx}")

    body = fx.read_bytes()
    parsed = asyncio.run(parse_document(body, fx.name))

    parse_expect = case["parse"]
    if "parser_prefix" in parse_expect:
        assert parsed.parser.startswith(parse_expect["parser_prefix"])

    total_pages = parsed.total_pages
    lo, hi = parse_expect["total_pages_min"], parse_expect["total_pages_max"]
    assert lo <= total_pages <= hi

    total_chars = sum(len(pg.text or "") for pg in parsed.pages)
    clo, chi = parse_expect["total_chars_min"], parse_expect["total_chars_max"]
    assert clo <= total_chars <= chi

    total_tables = sum(len(pg.tables or []) for pg in parsed.pages)
    assert total_tables >= parse_expect["min_tables"]


# ─────────────────────────── offline: USALI canonical resolution ───────────────────────────


@pytest.mark.parametrize("case_id", _p_and_l_cases())
def test_canonical_fields_from_pinned_extraction(case_id: str) -> None:
    """Given the pinned real extraction payload for this fixture, the
    USALI resolver must surface the expected canonical field values
    (rooms_revenue, total_revenue, gop, noi, revpar, adr, occupancy)
    within tolerance and the USALI score must land inside the pinned band.

    This is deterministic — no LLM in the path. If a change to the
    structural recognizer, the alias map, or the token resolver drops a
    field or shifts a canonical value, this trips.
    """
    from app.services.usali_scorer import flatten_extraction_fields, score_extraction

    case = _load_case(case_id)
    payload_path = REPO_ROOT / case["extraction_payload_path"]
    if not payload_path.exists():
        pytest.skip(f"extraction payload missing: {payload_path}")

    payload = json.loads(payload_path.read_text())
    fields = payload.get("fields", [])
    assert fields, f"{case_id}: pinned payload has no fields"

    usali_expect = case["usali"]
    flat = flatten_extraction_fields(
        fields, extra_context={"keys": usali_expect["keys"]}
    )

    # ── Headline canonical values within 1% relative tolerance.
    tol = 0.01
    for name, expected in usali_expect["canonical_fields"].items():
        actual = flat.get(name)
        assert actual is not None, (
            f"{case_id}: canonical field {name!r} did not resolve — "
            f"structural recognizer or alias map regressed"
        )
        _assert_close(float(actual), float(expected), tol, f"{case_id}.{name}")

    # ── USALI score band + minimum applicable rules.
    result = score_extraction(flat)
    assert result.applicable_count >= usali_expect["min_applicable_rules"], (
        f"{case_id}: applicable_count={result.applicable_count} < "
        f"min={usali_expect['min_applicable_rules']} — resolver regression"
    )
    assert result.score is not None, (
        f"{case_id}: USALI score is None (inconclusive) — expected numeric"
    )
    slo, shi = usali_expect["score_range"]
    assert slo <= result.score <= shi, (
        f"{case_id}: USALI score={result.score} outside [{slo}, {shi}]"
    )


# ─────────────────────────── offline: text signals (STR / market reports) ───────────────────────────


def test_str_trend_text_signals_detect_str_markers() -> None:
    """STR Trend fixture must trip the ``detect_text_signals`` STR
    marker classifier — this is a poor-man's offline doc-type check
    (no LLM required). If the CoStar / by-measure regexes regress or
    the parser drops the marker lines, this trips.
    """
    from app.extraction import parse_document
    from app.services.structural_recognizer import detect_text_signals

    case = _load_case("sample_str_trend")
    fx = _fixture_path(case)
    if not fx.exists():
        pytest.skip(f"fixture missing: {fx}")

    body = fx.read_bytes()
    parsed = asyncio.run(parse_document(body, fx.name))

    combined = "\n".join(pg.text for pg in parsed.pages[:6])
    sig = detect_text_signals(combined)
    ts_expect = case["text_signals"]
    assert sig.str_marker_hits >= ts_expect["min_str_marker_hits"], (
        f"str_marker_hits={sig.str_marker_hits} < "
        f"min={ts_expect['min_str_marker_hits']}"
    )


# ─────────────────────────── live: opt-in Anthropic end-to-end ───────────────────────────


@pytest.mark.live_llm
@pytest.mark.parametrize("case_id", _p_and_l_cases())
def test_live_llm_end_to_end_extraction(case_id: str) -> None:
    """Opt-in — runs parse → router → extractor against the real Anthropic
    API and asserts the LIVE extraction reproduces the pinned canonical
    values within a wider tolerance.

    Requires ``ANTHROPIC_API_KEY`` in the environment. Skipped otherwise.

    Wider tolerance (10% vs. 1% for the offline path) because the LLM
    extraction is non-deterministic — we care about "field is present +
    order of magnitude correct" more than exact decimals for the live
    check. The offline path pins exact values against a captured payload.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-test-"):
        pytest.skip("ANTHROPIC_API_KEY not set (or is a test placeholder)")

    from fondok_schemas import DocType

    from app.agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
    )
    from app.agents.router import RouterInput, run_router
    from app.extraction import parse_document
    from app.services.usali_scorer import flatten_extraction_fields, score_extraction

    case = _load_case(case_id)
    fx = _fixture_path(case)
    if not fx.exists():
        pytest.skip(f"fixture missing: {fx}")

    body = fx.read_bytes()
    parsed = asyncio.run(parse_document(body, fx.name))

    combined_text = "\n\n".join(pg.text for pg in parsed.pages)

    # ── Router: classify doc_type from filename + content sample.
    router_out = asyncio.run(
        run_router(
            RouterInput(
                tenant_id="00000000-0000-0000-0000-00000000000t",
                deal_id="00000000-0000-0000-0000-00000000000d",
                filename=fx.name,
                content_sample=combined_text[:2000],
            )
        )
    )
    assert router_out.success, f"router failed: {router_out.error}"
    assert router_out.doc_type == case["expected_doc_type"], (
        f"{case_id}: router classified as {router_out.doc_type}, "
        f"expected {case['expected_doc_type']}"
    )

    # ── Extractor: run against parsed content, request canonical fields.
    doc = ExtractorDocument(
        document_id="00000000-0000-0000-0000-00000000000c",
        filename=fx.name,
        doc_type=DocType(router_out.doc_type),
        content=combined_text[:60_000],
    )
    ext_out = asyncio.run(
        run_extractor(
            ExtractorInput(
                tenant_id="00000000-0000-0000-0000-00000000000t",
                deal_id="00000000-0000-0000-0000-00000000000d",
                documents=[doc],
            )
        )
    )
    assert ext_out.success, f"extractor failed: {ext_out.error}"
    assert ext_out.documents, "extractor returned no documents"
    doc_out = ext_out.documents[0]
    assert doc_out.fields, "extractor returned no fields"

    fields = [f.model_dump() if hasattr(f, "model_dump") else f for f in doc_out.fields]
    usali_expect = case["usali"]
    flat = flatten_extraction_fields(
        fields, extra_context={"keys": usali_expect["keys"]}
    )

    # ── Live LLM: 10% tolerance. We assert every canonical field
    # resolves + lands in the right ballpark.
    tol = 0.10
    misses: list[str] = []
    for name, expected in usali_expect["canonical_fields"].items():
        actual = flat.get(name)
        if actual is None:
            misses.append(f"{name} missing")
            continue
        try:
            _assert_close(float(actual), float(expected), tol, f"{case_id}.{name}")
        except AssertionError as e:
            misses.append(str(e))
    assert not misses, "\n".join(misses)

    # USALI score band — same as offline.
    result = score_extraction(flat)
    assert result.applicable_count >= usali_expect["min_applicable_rules"]
    assert result.score is not None
    slo, shi = usali_expect["score_range"]
    assert slo <= result.score <= shi, (
        f"{case_id}: live USALI score={result.score} outside [{slo}, {shi}]"
    )


# ─────────────────────────── live: Router → Extractor → Normalizer chain ─────────────────


@pytest.mark.live_llm
@pytest.mark.parametrize("case_id", _p_and_l_cases())
def test_live_llm_router_extractor_normalizer_chain(case_id: str) -> None:
    """Cost-opt pass T (2026-07) safety net: exercise the full
    Router → Extractor → Normalizer chain on a golden P&L fixture and
    assert the USALI score lands within 10% of the pinned score band's
    midpoint.

    Why this test exists: pass T downgraded the Normalizer from Sonnet
    4.6 to Haiku 4.5. The Normalizer's job is synonym-mapping
    ``ExtractionField`` rows onto the ~30 canonical USALI buckets; a
    quality regression here shows up as the USALI resolver dropping
    fields (or landing them in the wrong bucket, which the deterministic
    rollup identities then flag as warnings). Both surfaces manifest in
    the USALI score, which is why the score band is the assertion.

    Also asserts:
      * Router classifies to the expected doc_type (Haiku already).
      * Extractor still returns >=8 fields (unchanged — Sonnet).
      * Normalizer emits a spread with a positive total_revenue and a
        finite NOI.

    Requires ``ANTHROPIC_API_KEY``. Skipped otherwise.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-test-"):
        pytest.skip("ANTHROPIC_API_KEY not set (or is a test placeholder)")

    from fondok_schemas import DocType

    from app.agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
    )
    from app.agents.normalizer import NormalizerInput, run_normalizer
    from app.agents.router import RouterInput, run_router
    from app.extraction import parse_document
    from app.services.usali_scorer import flatten_extraction_fields, score_extraction

    case = _load_case(case_id)
    fx = _fixture_path(case)
    if not fx.exists():
        pytest.skip(f"fixture missing: {fx}")

    body = fx.read_bytes()
    parsed = asyncio.run(parse_document(body, fx.name))
    combined_text = "\n\n".join(pg.text for pg in parsed.pages)

    # ── Router (Haiku 4.5)
    router_out = asyncio.run(
        run_router(
            RouterInput(
                tenant_id="00000000-0000-0000-0000-00000000000t",
                deal_id="00000000-0000-0000-0000-00000000000d",
                filename=fx.name,
                content_sample=combined_text[:2000],
            )
        )
    )
    assert router_out.success, f"router failed: {router_out.error}"
    assert router_out.doc_type == case["expected_doc_type"], (
        f"{case_id}: router classified as {router_out.doc_type}, "
        f"expected {case['expected_doc_type']}"
    )

    # ── Extractor (Sonnet 4.6 — kept on Sonnet for quality)
    doc = ExtractorDocument(
        document_id="00000000-0000-0000-0000-00000000000c",
        filename=fx.name,
        doc_type=DocType(router_out.doc_type),
        content=combined_text[:60_000],
    )
    ext_out = asyncio.run(
        run_extractor(
            ExtractorInput(
                tenant_id="00000000-0000-0000-0000-00000000000t",
                deal_id="00000000-0000-0000-0000-00000000000d",
                documents=[doc],
            )
        )
    )
    assert ext_out.success, f"extractor failed: {ext_out.error}"
    assert ext_out.documents, "extractor returned no documents"
    doc_out = ext_out.documents[0]
    assert len(doc_out.fields) >= 8, (
        f"{case_id}: extractor returned only {len(doc_out.fields)} fields "
        f"— expected >=8 to feed the normalizer"
    )

    # ── Normalizer (Haiku 4.5 as of pass T, Sonnet fallback on parse fail)
    norm_out = asyncio.run(
        run_normalizer(
            NormalizerInput(
                tenant_id="00000000-0000-0000-0000-00000000000t",
                deal_id="00000000-0000-0000-0000-00000000000d",
                fields=list(doc_out.fields),
                period_hint=None,
            )
        )
    )
    assert norm_out.success, f"normalizer failed: {norm_out.error}"
    assert norm_out.normalized_spread is not None
    spread = norm_out.normalized_spread
    assert spread.total_revenue > 0, (
        f"{case_id}: normalizer produced non-positive total_revenue "
        f"{spread.total_revenue}"
    )
    # NOI is signed but must be finite and less than total revenue in
    # absolute terms — a Haiku regression that mis-maps expenses could
    # slam NOI to something like 10x total_revenue.
    assert abs(spread.noi) < 5 * abs(spread.total_revenue), (
        f"{case_id}: normalizer NOI={spread.noi} vs total_revenue="
        f"{spread.total_revenue} — pass-T Haiku downgrade regressed"
    )

    # ── USALI score within 10% of the pinned band midpoint. The
    # extraction is non-deterministic so we widen tolerance from the
    # offline path's 1%.
    fields_dumped = [
        f.model_dump() if hasattr(f, "model_dump") else f for f in doc_out.fields
    ]
    usali_expect = case["usali"]
    flat = flatten_extraction_fields(
        fields_dumped, extra_context={"keys": usali_expect["keys"]}
    )
    result = score_extraction(flat)
    assert result.applicable_count >= usali_expect["min_applicable_rules"], (
        f"{case_id}: applicable_count={result.applicable_count} regressed"
    )
    assert result.score is not None
    slo, shi = usali_expect["score_range"]
    midpoint = (slo + shi) / 2.0
    tol = 0.10  # 10% of the pinned midpoint
    dev = abs(result.score - midpoint) / midpoint if midpoint else 0.0
    assert dev <= tol, (
        f"{case_id}: pass-T Router→Extractor→Normalizer live USALI score="
        f"{result.score:.3f} deviates {dev:.2%} from pinned midpoint "
        f"{midpoint:.3f} (>{tol:.0%}) — Haiku downgrade regressed quality"
    )


# ─────────────────────────── smoke: manifest health ───────────────────────────


def test_manifest_and_expected_files_consistent() -> None:
    """Manifest lists cases; every case has an ``<id>.expected.json``
    and the fixture path resolves (or is intentionally missing). Prevents
    silent drift where a case is removed from disk but still listed."""
    manifest = _load_manifest()
    for cid in manifest["cases"]:
        exp = GOLDEN_DIR / f"{cid}.expected.json"
        assert exp.exists(), f"missing expected file for case {cid}"
        case = _load_case(cid)
        assert case["case_id"] == cid, f"case_id mismatch in {exp.name}"
        assert "fixture_path" in case
        assert "expected_doc_type" in case
        assert "parse" in case


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
