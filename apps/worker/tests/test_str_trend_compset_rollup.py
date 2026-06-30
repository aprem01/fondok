"""STR_TREND comp-set roster rollup — backend assembly unit tests.

Eshan reported (June 2026) that the Index Analysis "Available Rooms"
row was zero across all 15 years even when MPI/ARI/RGI ratios came
through. Root cause: the STR Trend extractor sometimes drops the
`comp_set.total_keys` rollup field even when it surfaces the per-row
``ttm_performance.compset.<n>.keys`` values from the report's
Response tab. The frontend Index-Analysis table multiplies
``days × total_keys`` for the Available-Rooms row — a missing total
collapses every cell to zero.

This module exercises the fallback path that derives ``total_keys``
(and ``comp_set_size``) by summing the per-comp roster when the
explicit rollup is missing, plus the happy path that keeps the
extracted rollup when both are present.

Pure unit test — no DB, no LLM, no I/O.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Force an isolated SQLite so module import of ``app.api.documents`` —
# which pulls the whole FastAPI app graph — doesn't touch any shared
# state. Mirrors the convention in test_documents.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-compset-rollup.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("EVALS_MOCK", "true")

# Ensure the worker app is importable when pytest is run from the repo
# root vs from apps/worker.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.documents import (  # noqa: E402
    _bucket_str_trend,
    _build_str_trend_block,
)


def test_total_keys_derived_from_roster_when_rollup_missing() -> None:
    """The Eshan-reported bug: indexed compset rows are present, but
    ``comp_set.total_keys`` was not extracted. We must sum the roster
    rather than hand the frontend a null/zero total."""
    flat: dict[str, object] = {
        "ttm_performance.subject.occupancy_pct": 0.878,
        "ttm_performance.subject.adr_usd": 278.77,
        "ttm_performance.subject.revpar_usd": 244.84,
        "ttm_performance.indices.rgi_revpar_index": 0.86,
        "ttm_performance.indices.ari_adr_index": 0.79,
        "ttm_performance.indices.mpi_occupancy_index": 1.09,
        # Notably NO ``comp_set.total_keys`` / ``comp_set.comp_set_size``.
    }
    compset = {
        1: {"name": "Z Ocean Hotel", "keys": 40},
        2: {"name": "Blue Moon Hotel", "keys": 75},
        3: {"name": "The Betsy South Beach", "keys": 129},
        4: {"name": "The Tony Hotel of South Beach", "keys": 73},
        5: {"name": "Dream South Beach", "keys": 107},
    }

    block = _build_str_trend_block(flat, compset)

    assert block is not None
    # 40 + 75 + 129 + 73 + 107 = 424
    assert block.total_keys == 424
    assert block.comp_set_size == 5
    assert len(block.compset) == 5
    # Spot-check the roster came through with names + keys intact.
    names = [e.name for e in block.compset]
    assert "Blue Moon Hotel" in names
    assert block.compset[0].keys == 40


def test_extracted_total_keys_wins_over_derived_sum() -> None:
    """When the LLM does emit ``comp_set.total_keys`` we trust it
    rather than recomputing — the extracted value can legitimately
    include the subject itself or reflect a numbers-only row on the
    STR Summary tab that the indexed roster misses."""
    flat = {
        "comp_set.total_keys": 556,
        "comp_set.comp_set_size": 5,
    }
    compset = {
        1: {"name": "Z Ocean", "keys": 40},
        2: {"name": "Blue Moon", "keys": 75},
    }

    block = _build_str_trend_block(flat, compset)
    assert block is not None
    # 556 (extracted) over 115 (sum) — extracted wins.
    assert block.total_keys == 556
    assert block.comp_set_size == 5


def test_empty_roster_with_no_rollup_yields_null_total() -> None:
    """No compset rows, no rollup → don't fabricate a zero. The
    frontend distinguishes ``null`` (no STR data) from ``0`` (extracted
    but empty)."""
    flat = {
        "ttm_performance.subject.occupancy_pct": 0.7,
    }
    block = _build_str_trend_block(flat, {})
    assert block is not None
    assert block.total_keys is None
    assert block.comp_set_size is None
    assert block.compset == []


def test_partial_keys_in_roster_still_yields_useful_total() -> None:
    """Real-world STR reports sometimes leave one property's room
    count blank (boutique that didn't disclose). Sum only the
    non-null entries — better an underestimate than a zero."""
    flat: dict[str, object] = {}
    compset = {
        1: {"name": "Prop A", "keys": 100},
        2: {"name": "Prop B", "keys": None},
        3: {"name": "Prop C", "keys": 50},
    }
    block = _build_str_trend_block(flat, compset)
    assert block is not None
    assert block.total_keys == 150
    assert block.comp_set_size == 3


def test_bucket_str_trend_routes_indexed_compset_fields() -> None:
    """The bucketing step is what feeds ``_build_str_trend_block`` —
    confirm an indexed compset field lands in the right row, not the
    flat dict."""
    flat: dict[str, object] = {}
    compset: dict[int, dict[str, object]] = {}

    _bucket_str_trend("ttm_performance.compset.3.keys", 129, flat, compset)
    _bucket_str_trend(
        "ttm_performance.compset.3.name", "The Betsy South Beach", flat, compset
    )
    _bucket_str_trend("ttm_performance.indices.rgi_revpar_index", 0.86, flat, compset)

    assert compset == {
        3: {"keys": 129, "name": "The Betsy South Beach"},
    }
    assert flat == {"ttm_performance.indices.rgi_revpar_index": 0.86}
