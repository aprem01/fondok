"""STR_TREND comp-set roster rollup — backend assembly unit tests.

Eshan reported (June 2026) that the Index Analysis "Available Rooms"
row was zero across all 15 years even when MPI/ARI/RGI ratios came
through. Root cause: the STR Trend extractor sometimes drops the
`comp_set.total_keys` rollup field even when it surfaces the per-row
``ttm_performance.compset.<n>.keys`` values from the report's
Response tab. The frontend Index-Analysis table multiplies
``days × total_keys`` for the Available-Rooms row — a missing total
collapses every cell to zero.

FON-60 (Sam QA 2026-11-16) — the envelope's roster, ``total_keys`` and
``comp_set_size`` are now the Market tab's ONE comp-set derivation
(``market_comp_set.derive_comp_set_union``): the ACTIVE hotels of the
roster unioned across every STR-lane extraction. The report's own rollup
is used only when no roster was extracted at all — it is never preferred
over the roster (E-009: the rollup counts closed hotels the keys exclude,
and on the live deal read 419 / 1,011 against a 344-key active roster).

Pure unit test — no DB, no LLM, no I/O.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

# Force an isolated SQLite so module import of ``app.api.documents`` —
# which pulls the whole FastAPI app graph — doesn't touch any shared
# state. Mirrors the convention in test_documents.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-compset-rollup.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

# Ensure the worker app is importable when pytest is run from the repo
# root vs from apps/worker.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.documents import (  # noqa: E402
    _aggregate_market_data,
    _bucket_str_trend,
    _build_str_trend_block,
)

STR_STAMP = "template:str_trend:v1;ps=959bc26b;reg=2;pv=v8"


def _record(flat: dict[str, Any], compset: dict[int, dict[str, Any]], *, filename: str = "ANG-20250500-USD-E.xlsx") -> dict[str, Any]:
    fields: list[dict[str, Any]] = [{"field_name": k, "value": v} for k, v in flat.items()]
    for idx, row in compset.items():
        for attr, value in row.items():
            fields.append({"field_name": f"ttm_performance.compset.{idx}.{attr}", "value": value})
    return {
        "document_id": uuid4(),
        "doc_type": "STR_TREND",
        "agent_version": STR_STAMP,
        "ai_proposed_doc_type": "STR_TREND",
        "filename": filename,
        "fields": fields,
    }


def _envelope_str(flat: dict[str, Any], compset: dict[int, dict[str, Any]]):
    env = _aggregate_market_data([_record(flat, compset)], uuid4())
    assert env.str_trend is not None
    return env.str_trend


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

    block = _envelope_str(flat, compset)

    # 40 + 75 + 129 + 73 + 107 = 424
    assert block.total_keys == 424
    assert block.comp_set_size == 5
    assert len(block.compset) == 5
    # Spot-check the roster came through with names + keys intact.
    names = [e.name for e in block.compset]
    assert "Blue Moon Hotel" in names
    assert block.compset[0].keys == 40


def test_active_roster_wins_over_the_extracted_rollup() -> None:
    """FON-60 / E-009 — while a roster exists, the headline is the active
    roster's keys and count; the report's rollup is never preferred (live:
    ``comp_set.total_keys`` 419 counted the closed Blue Moon the roster
    excluded, and Index Analysis disagreed with Market Overview)."""
    flat = {
        "comp_set.total_keys": 556,
        "comp_set.comp_set_size": 5,
    }
    compset = {
        1: {"name": "Z Ocean", "keys": 40},
        2: {"name": "Blue Moon", "keys": 75},
    }

    block = _envelope_str(flat, compset)
    assert block.total_keys == 115
    assert block.comp_set_size == 2


def test_rollup_used_only_when_no_roster_was_extracted() -> None:
    flat = {
        "ttm_performance.subject.occupancy_pct": 0.7,
        "comp_set.total_keys": 556,
        "comp_set.comp_set_size": 5,
    }
    block = _envelope_str(flat, {})
    assert block.total_keys == 556
    assert block.comp_set_size == 5
    assert block.compset == []


def test_empty_roster_with_no_rollup_yields_null_total() -> None:
    """No compset rows, no rollup → don't fabricate a zero. The
    frontend distinguishes ``null`` (no STR data) from ``0`` (extracted
    but empty)."""
    flat = {
        "ttm_performance.subject.occupancy_pct": 0.7,
    }
    block = _envelope_str(flat, {})
    assert block.total_keys is None
    assert block.comp_set_size is None
    assert block.compset == []


def test_build_block_direct_with_no_roster_and_no_headline() -> None:
    block = _build_str_trend_block({"ttm_performance.subject.occupancy_pct": 0.7}, [])
    assert block is not None
    assert block.total_keys is None and block.comp_set_size is None and block.compset == []
    assert _build_str_trend_block({}, []) is None


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
    block = _envelope_str(flat, compset)
    assert block.total_keys == 150
    assert block.comp_set_size == 3


def test_bucket_str_trend_keeps_indexed_compset_fields_out_of_the_flat_dict() -> None:
    """The flat dict carries the subject / indices / rollups; the indexed
    compset rows are read by the comp-set union, never bucketed here."""
    flat: dict[str, object] = {}

    _bucket_str_trend("ttm_performance.compset.3.keys", 129, flat)
    _bucket_str_trend("ttm_performance.compset.3.name", "The Betsy South Beach", flat)
    _bucket_str_trend("ttm_performance.indices.rgi_revpar_index", 0.86, flat)

    assert flat == {"ttm_performance.indices.rgi_revpar_index": 0.86}
