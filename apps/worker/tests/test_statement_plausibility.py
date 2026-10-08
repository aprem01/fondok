"""FON-41 — per-deal statement-plausibility critic (25% rule).

A P&L whose F&B / Rooms / Total revenue is below 25% of the median of the
deal's OTHER full-period statements for the same line is capped at 0.5
confidence with the reason on the field and on the confidence report. The
Angler's shape: 2024 P&L F&B $96,528 vs a 2023 P&L at $2.11M and a T-12 at
$3.22M.

LLM-free. The last block exercises the sibling loader against the sqlite
test DB (one row per sibling document, P&L-family only, current doc and OM
excluded).
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.agents.critic import (
    PLAUSIBILITY_CONFIDENCE_CAP,
    apply_statement_plausibility,
    check_statement_plausibility,
)

EN_DASH = chr(0x2013)
FB = "p_and_l_usali.operating_revenue.food_beverage_revenue"
ROOMS = "p_and_l_usali.operating_revenue.rooms_revenue"
TOTAL = "p_and_l_usali.operating_revenue.total_revenue"


def _stmt(
    *, fb: float | None = None, rooms: float | None = None, total: float | None = None,
    period_type: str = "annual", fb_conf: float = 0.9, reviewed: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if fb is not None:
        f = {"field_name": FB, "value": fb, "unit": "USD", "source_page": 3, "confidence": fb_conf}
        if reviewed:
            f["reviewed"] = reviewed
        out.append(f)
    if rooms is not None:
        out.append({"field_name": ROOMS, "value": rooms, "unit": "USD", "source_page": 6, "confidence": 0.9})
    if total is not None:
        out.append({"field_name": TOTAL, "value": total, "unit": "USD", "source_page": 5, "confidence": 0.9})
    out.append({"field_name": "p_and_l_usali.period_type", "value": period_type, "source_page": 1, "confidence": 0.9})
    return out


def _conf(fields: list[dict[str, Any]]) -> dict[str, Any]:
    by_field = {f["field_name"]: float(f["confidence"]) for f in fields}
    return {
        "overall": sum(by_field.values()) / len(by_field),
        "by_field": by_field,
        "low_confidence_fields": [n for n, c in by_field.items() if c < 0.85],
        "requires_human_review": False,
    }


_SIBLINGS = [
    ("PNL", _stmt(fb=2_110_000.0, rooms=8_900_000.0, total=12_400_000.0)),
    ("T12", _stmt(fb=3_220_000.0, rooms=9_600_000.0, total=14_100_000.0)),
]


def test_outlet_level_fb_is_flagged_with_the_deal_context_in_the_reason():
    fields = _stmt(fb=96_528.1, rooms=9_496_407.22, total=13_481_730.29)
    flags = check_statement_plausibility(fields, doc_type="PNL", siblings=_SIBLINGS)
    assert [f.field_name for f in flags] == [FB]
    flag = flags[0]
    assert flag.concept == "fb_revenue"
    assert flag.sibling_values == [2_110_000.0, 3_220_000.0]
    assert flag.sibling_median == 2_665_000.0
    assert 0.03 < flag.ratio < 0.04
    assert flag.reason == (
        f"F&B revenue $96,528 is 4% of the deal's other statements ($2.1M{EN_DASH}$3.2M); "
        "likely a single outlet or GL line, not the department total"
    )


def test_apply_caps_confidence_and_writes_the_reason_everywhere():
    fields = _stmt(fb=96_528.1, rooms=9_496_407.22, total=13_481_730.29)
    out_fields, out_conf, flags = apply_statement_plausibility(
        fields, _conf(fields), doc_type="PNL", siblings=_SIBLINGS
    )
    assert len(flags) == 1
    fb = next(f for f in out_fields if f["field_name"] == FB)
    assert fb["confidence"] == PLAUSIBILITY_CONFIDENCE_CAP == 0.5
    assert fb["value"] == 96_528.1  # the value is never rewritten — only its confidence
    assert fb["note"] == flags[0].reason
    assert out_conf["by_field"][FB] == 0.5
    assert FB in out_conf["low_confidence_fields"]
    assert out_conf["requires_human_review"] is True
    assert out_conf["plausibility_flags"][0]["reason"] == flags[0].reason
    # Inputs are not mutated; other fields are untouched.
    assert fields[0]["confidence"] == 0.9 and "note" not in fields[0]
    rooms = next(f for f in out_fields if f["field_name"] == ROOMS)
    assert rooms["confidence"] == 0.9 and "note" not in rooms


def test_cap_never_raises_an_already_low_confidence():
    fields = _stmt(fb=96_528.1, fb_conf=0.3)
    out_fields, _conf_out, flags = apply_statement_plausibility(
        fields, _conf(fields), doc_type="PNL", siblings=_SIBLINGS
    )
    assert flags and next(f for f in out_fields if f["field_name"] == FB)["confidence"] == 0.3


def test_at_or_above_25_percent_is_not_flagged():
    # 700,000 / 2,665,000 = 26.3%
    fields = _stmt(fb=700_000.0)
    assert check_statement_plausibility(fields, doc_type="PNL", siblings=_SIBLINGS) == []
    # exactly 25% is not "below"
    fields = _stmt(fb=666_250.0)
    assert check_statement_plausibility(fields, doc_type="PNL", siblings=_SIBLINGS) == []
    # 24% is
    fields = _stmt(fb=639_600.0)
    assert len(check_statement_plausibility(fields, doc_type="PNL", siblings=_SIBLINGS)) == 1


def test_single_sibling_reason_names_one_figure():
    fields = _stmt(fb=96_528.1)
    flags = check_statement_plausibility(fields, doc_type="PNL", siblings=_SIBLINGS[:1])
    assert "($2.1M);" in flags[0].reason
    assert EN_DASH not in flags[0].reason


def test_partial_period_statements_take_no_part_on_either_side():
    # A monthly sibling is legitimately ~1/12 of the year — it must not set the bar.
    monthly_sibling = [("PNL_MONTHLY", _stmt(fb=200_000.0, period_type="monthly"))]
    fields = _stmt(fb=96_528.1)
    assert check_statement_plausibility(fields, doc_type="PNL", siblings=monthly_sibling) == []
    # A monthly CURRENT statement is never compared against annual siblings.
    monthly = _stmt(fb=96_528.1, period_type="monthly")
    assert check_statement_plausibility(monthly, doc_type="PNL_MONTHLY", siblings=_SIBLINGS) == []
    ytd = _stmt(fb=96_528.1, period_type="ytd")
    assert check_statement_plausibility(ytd, doc_type="PNL_YTD", siblings=_SIBLINGS) == []


def test_analyst_accepted_value_is_not_second_guessed():
    fields = _stmt(fb=96_528.1, reviewed="accepted")
    assert check_statement_plausibility(fields, doc_type="PNL", siblings=_SIBLINGS) == []


def test_no_siblings_or_no_comparable_line_means_no_flag():
    fields = _stmt(fb=96_528.1)
    assert check_statement_plausibility(fields, doc_type="PNL", siblings=[]) == []
    assert check_statement_plausibility(fields, doc_type="PNL", siblings=[("T12", _stmt(rooms=9e6))]) == []
    out_fields, out_conf, flags = apply_statement_plausibility(fields, _conf(fields), doc_type="PNL", siblings=[])
    assert flags == [] and out_fields == fields and "plausibility_flags" not in out_conf


def test_rooms_and_total_lines_use_their_own_wording():
    fields = _stmt(rooms=900_000.0, total=1_000_000.0)
    flags = check_statement_plausibility(fields, doc_type="T12", siblings=_SIBLINGS)
    reasons = {f.concept: f.reason for f in flags}
    assert reasons["rooms_revenue"].startswith(
        f"Rooms revenue $900,000 is 10% of the deal's other statements ($8.9M{EN_DASH}$9.6M); "
        "likely a single segment"
    )
    assert reasons["total_revenue"].startswith(
        f"Total revenue $1,000,000 is 8% of the deal's other statements ($12.4M{EN_DASH}$14.1M); "
        "likely a single department"
    )


# ─────────────────────────── sibling loader (sqlite) ───────────────────────────


@pytest.fixture
async def db_env():
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import dispose_engine, get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    tenant_id = str(get_settings().DEFAULT_TENANT_ID)
    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Plausibility Test Hotel', 'Draft', :ts, :ts)"
            ),
            {"id": deal_id, "tenant": tenant_id, "ts": "2026-10-01 00:00:00"},
        )
        await session.commit()
    yield factory, tenant_id, deal_id
    await dispose_engine()


async def _seed(session, *, tenant_id: str, deal_id: str, doc_type: str, extractions: list[tuple[str, list[dict[str, Any]]]]) -> str:
    """A document with one extraction_results row per ``(created_at, fields)``."""
    from sqlalchemy import text

    doc_id = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at) "
            "VALUES (:id, :deal, :tenant, :fn, :dt, 'EXTRACTED', :ts)"
        ),
        {"id": doc_id, "deal": deal_id, "tenant": tenant_id, "fn": f"{doc_id}.xlsx", "dt": doc_type, "ts": "2026-10-01 00:00:00"},
    )
    for created_at, fields in extractions:
        await session.execute(
            text(
                "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields, "
                "confidence_report, agent_version, created_at) "
                "VALUES (:id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts)"
            ),
            {"id": str(uuid4()), "doc": doc_id, "deal": deal_id, "tenant": tenant_id, "fields": json.dumps(fields), "ts": created_at},
        )
    await session.commit()
    return doc_id


async def test_sibling_loader_returns_latest_row_per_pnl_family_document(db_env):
    from app.api.documents import _load_pnl_sibling_fields

    factory, tenant_id, deal_id = db_env
    async with factory() as session:
        current = await _seed(session, tenant_id=tenant_id, deal_id=deal_id, doc_type="PNL",
                              extractions=[("2026-10-07 10:00:00", _stmt(fb=96_528.1))])
        pnl_2023 = await _seed(session, tenant_id=tenant_id, deal_id=deal_id, doc_type="PNL",
                               extractions=[("2026-10-01 10:00:00", _stmt(fb=1.0)),  # superseded
                                            ("2026-10-02 10:00:00", _stmt(fb=2_110_000.0))])
        t12 = await _seed(session, tenant_id=tenant_id, deal_id=deal_id, doc_type="T12",
                          extractions=[("2026-10-03 10:00:00", _stmt(fb=3_220_000.0))])
        await _seed(session, tenant_id=tenant_id, deal_id=deal_id, doc_type="OM",
                    extractions=[("2026-10-04 10:00:00", [{"field_name": "broker_proforma.fb_revenue_usd", "value": 5.0}])])
        await _seed(session, tenant_id=tenant_id, deal_id=str(uuid4()), doc_type="T12",
                    extractions=[("2026-10-05 10:00:00", _stmt(fb=7.0))])  # another deal

        siblings = await _load_pnl_sibling_fields(
            session, deal_id=deal_id, tenant_id=tenant_id, exclude_doc_id=current
        )
    assert sorted(dt for dt, _ in siblings) == ["PNL", "T12"]
    fb_values = sorted(next(f["value"] for f in fl if f["field_name"] == FB) for _dt, fl in siblings)
    assert fb_values == [2_110_000.0, 3_220_000.0], (pnl_2023, t12)

    # And the critic reads them the way the hook does.
    current_fields = _stmt(fb=96_528.1)
    _fields_out, conf_out, flags = apply_statement_plausibility(
        current_fields, _conf(current_fields), doc_type="PNL", siblings=siblings
    )
    assert len(flags) == 1 and conf_out["by_field"][FB] == 0.5
