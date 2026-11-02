"""W5 review I3 — the eight stored display strings ("$10M", "$1.24M") on
analyst-edited USD fields are repaired by an additive data migration.

A number is restored ONLY when the field record holds the exact value the
string was displayed from; otherwise the value is cleared, the field is
marked for review, and an audit row records before → after. Never a number
guessed from "$10M". Runs at boot; idempotent on SQLite.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-display-string-repair.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-0000000000d5"

# The prod shape (doc 5b452ac8 and three copies): two broken edits beside
# legitimate fields.
_PROD_FIELDS = [
    {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": "$10M", "unit": "USD",
     "confidence": 1.0, "reviewed": "edited",
     "raw_text": "D_ROOMS_CON 400000 ... 9.49641e+06"},
    {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": "$1.24M", "unit": "USD",
     "confidence": 1.0, "reviewed": "edited"},
    # A legitimate numeric edit.
    {"field_name": "p_and_l_usali.rooms_revenue_transient", "value": 6339944.54, "unit": "USD",
     "confidence": 1.0, "reviewed": "edited"},
    # Text fields are text.
    {"field_name": "p_and_l_usali.period_type", "value": "annual", "confidence": 0.97,
     "reviewed": "edited"},
    # A string on a USD field that was NOT an analyst edit is not this defect.
    {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": "n/a", "unit": "USD",
     "confidence": 0.5},
    # A field whose record keeps the exact pre-edit value the string displays.
    {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": "$2.74M",
     "unit": "USD", "confidence": 1.0, "reviewed": "edited", "value_before_edit": 2739040.71},
]


async def _seed() -> tuple[str, str]:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    deal_id, doc_id = str(uuid4()), str(uuid4())
    ts = datetime.now(UTC)
    async with get_session_factory()() as s:
        for t in ("audit_log", "extraction_results", "documents", "deals"):
            await s.execute(text(f"DELETE FROM {t}"))
        await s.execute(
            text("INSERT INTO deals (id, tenant_id, name, status, ai_confidence, created_at, updated_at) "
                 "VALUES (:id,:t,'d','Draft',0.0,:ts,:ts)"),
            {"id": deal_id, "t": _TENANT, "ts": ts},
        )
        await s.execute(
            text("INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at) "
                 "VALUES (:id,:deal,:t,'Angler_s 2024 Full Year Detailed P&L.xlsm','T12','EXTRACTED',:ts)"),
            {"id": doc_id, "deal": deal_id, "t": _TENANT, "ts": ts},
        )
        cr = {"overall": 0.9, "by_field": {f["field_name"]: f["confidence"] for f in _PROD_FIELDS},
              "low_confidence_fields": [], "requires_human_review": False}
        await s.execute(
            text("INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields, "
                 "confidence_report, agent_version, created_at) "
                 "VALUES (:id,:doc,:deal,:t,:f,:cr,'router:extractor;extractor;pv=v1',:ts)"),
            {"id": str(uuid4()), "doc": doc_id, "deal": deal_id, "t": _TENANT,
             "f": json.dumps(_PROD_FIELDS), "cr": json.dumps(cr), "ts": ts},
        )
        await s.commit()
    return deal_id, doc_id


async def _stored(doc_id: str) -> tuple[dict[str, dict], dict]:
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        row = (await s.execute(
            text("SELECT fields, confidence_report FROM extraction_results WHERE document_id = :d"),
            {"d": doc_id})).first()
    fields = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    cr = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return {f["field_name"]: f for f in fields}, cr


@pytest.mark.asyncio
async def test_repair_clears_unrecoverable_strings_restores_exact_ones_and_audits() -> None:
    from app.database import get_migrations_engine, get_session_factory
    from app.migrations import repair_display_string_edits

    _deal, doc_id = await _seed()
    result = await repair_display_string_edits(get_migrations_engine(), is_sqlite=True)
    assert result == {"rows": 1, "restored": 1, "cleared": 2}

    by, cr = await _stored(doc_id)
    rooms = by["p_and_l_usali.operating_revenue.rooms_revenue"]
    assert rooms["value"] is None  # never a number guessed from "$10M"
    assert rooms["confidence"] == 0.0
    assert "reviewed" not in rooms
    assert rooms["note"].startswith("Needs review") and "'$10M'" in rooms["note"]
    assert by["p_and_l_usali.operating_revenue.other_revenue"]["value"] is None
    fb = by["p_and_l_usali.operating_revenue.food_beverage_revenue"]
    assert fb["value"] == pytest.approx(2739040.71)
    assert fb["reviewed"] == "edited"
    # Untouched: the legitimate edit, text fields, a non-edited string.
    assert by["p_and_l_usali.rooms_revenue_transient"]["value"] == 6339944.54
    assert by["p_and_l_usali.period_type"]["value"] == "annual"
    assert by["p_and_l_usali.operating_revenue.misc_revenue"]["value"] == "n/a"
    # The confidence report follows: the cleared fields now need review.
    assert "p_and_l_usali.operating_revenue.rooms_revenue" in cr["low_confidence_fields"]

    async with get_session_factory()() as s:
        audits = (await s.execute(
            text("SELECT resource_id, before, after, diff_summary, actor_id FROM audit_log "
                 "WHERE action = 'extraction_field.display_string_repaired'"))).fetchall()
    assert len(audits) == 3
    assert all(a[4] == "system:migration" for a in audits)
    summaries = " | ".join(a[3] for a in audits)
    assert "'$10M' → None (cleared)" in summaries
    assert "'$1.24M' → None (cleared)" in summaries
    assert "(restored)" in summaries


@pytest.mark.asyncio
async def test_repair_is_idempotent() -> None:
    from app.database import get_migrations_engine
    from app.migrations import repair_display_string_edits

    _deal, doc_id = await _seed()
    await repair_display_string_edits(get_migrations_engine(), is_sqlite=True)
    first, _ = await _stored(doc_id)
    again = await repair_display_string_edits(get_migrations_engine(), is_sqlite=True)
    assert again == {"rows": 0, "restored": 0, "cleared": 0}
    second, _ = await _stored(doc_id)
    assert first == second


@pytest.mark.asyncio
async def test_boot_runs_the_repair() -> None:
    from app.migrations import run_startup_migrations

    _deal, doc_id = await _seed()  # _seed already booted once BEFORE inserting
    await run_startup_migrations()
    by, _ = await _stored(doc_id)
    assert by["p_and_l_usali.operating_revenue.rooms_revenue"]["value"] is None


def test_display_strings_never_match_a_rounded_neighbour():
    from app.extraction.edited_figures import display_strings

    # "$10M" is not how 9,496,407.22 was ever displayed ($9.50M / $9.5M / $9M).
    assert "$10M" not in display_strings(9_496_407.22)
    assert "$9.50M" in display_strings(9_496_407.22)
