"""FON-86 — the market-data envelope and the STR forecast loader select
documents by the LANE each extraction ran in, not by ``documents.doc_type``.

Live fact on both tester deals: the CoStar market reports carry the analyst
tag ``STR_TREND`` while their extraction ran in the ``MARKET_STUDY`` lane
(``agent_version`` ``dt:MARKET_STUDY``; fields ``market_study.*`` /
``pnl_benchmark.market.*``). Selecting by tag let that market report feed
Index Analysis and the STR forward forecast as the subject's own STR trend.

The Market overview blocks already route by ``services.market_fields.
extraction_lane``; these lock the two remaining readers onto the same rule:

  * ``_aggregate_market_data`` — a mis-tagged market report lands in
    ``market_history`` (doc_type ``MARKET_STUDY``) and ``sources``, never in
    ``str_trend``; every field keeps its document + page provenance.
  * ``str_forecast_loader`` — ``load_str_history_for_deal`` and
    ``load_str_subject_ttm`` read nothing from a MARKET_STUDY-lane row.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.api.documents import _aggregate_market_data  # noqa: E402
from app.services.str_forecast_loader import (  # noqa: E402
    load_str_history_for_deal,
    load_str_subject_ttm,
    str_lane_rows,
)

# The stamp the extraction pipeline writes (``api.documents._agent_version``):
# ``router:{route};dt:{doc_type};extractor;ps=…;reg=…;pv=vN``.
MARKET_STUDY_STAMP = "router:extractor;dt:MARKET_STUDY;extractor;ps=1a2b3c4d;reg=3;pv=v6"
STR_STAMP = "router:extractor;dt:STR_TREND;extractor;ps=9f8e7d6c;reg=3;pv=v6"

COSTAR_NAME = "CoStar Submarket Report — Miami Beach.pdf"
STR_NAME = "STR Trend May 2025.xlsx"


def _mis_tagged_costar(doc_id: Any) -> dict[str, Any]:
    """Tag STR_TREND, lane MARKET_STUDY — the tester-deal shape."""
    return {
        "document_id": doc_id,
        "doc_type": "STR_TREND",
        "agent_version": MARKET_STUDY_STAMP,
        "ai_proposed_doc_type": "MARKET_STUDY",
        "filename": COSTAR_NAME,
        "fields": [
            {"field_name": "market_study.trend.2023.occupancy_pct", "value": 0.712, "source_page": 9},
            {"field_name": "market_study.trend.2023.adr_usd", "value": 288.0, "source_page": 9},
            {"field_name": "market_study.trend.2023.period", "value": "actual"},
            {"field_name": "market_study.trend.2024.occupancy_pct", "value": 0.734, "source_page": 9},
            {"field_name": "market_study.trend.2024.period", "value": "actual"},
            {"field_name": "pnl_benchmark.market.revpar_2024", "value": 219.0, "source_page": 11},
            # A market report's "subject" headline — must NOT become the
            # subject's STR TTM or a monthly history point.
            {"field_name": "ttm_performance.subject.occupancy_pct", "value": 61.0},
            {"field_name": "ttm_performance.subject.adr_usd", "value": 150.0},
            {"field_name": "ttm_performance.subject.monthly.2024_06.occupancy_pct", "value": 60.0},
            {"field_name": "ttm_performance.subject.monthly.2024_06.adr_usd", "value": 150.0},
        ],
    }


def _genuine_str(doc_id: Any) -> dict[str, Any]:
    return {
        "document_id": doc_id,
        "doc_type": "STR_TREND",
        "agent_version": STR_STAMP,
        "ai_proposed_doc_type": "STR_TREND",
        "filename": STR_NAME,
        "fields": [
            {"field_name": "ttm_performance.subject.occupancy_pct", "value": 78.4, "source_page": 2},
            {"field_name": "ttm_performance.subject.adr_usd", "value": 341.0, "source_page": 2},
            {"field_name": "ttm_performance.subject.annual.2024.adr_usd", "value": 335.0, "source_page": 3},
            {"field_name": "ttm_performance.subject.monthly.2024_05.occupancy_pct", "value": 80.0},
            {"field_name": "ttm_performance.subject.monthly.2024_05.adr_usd", "value": 350.0},
            {"field_name": "ttm_performance.subject.monthly.2024_06.occupancy_pct", "value": 76.0},
            {"field_name": "ttm_performance.subject.monthly.2024_06.adr_usd", "value": 330.0},
        ],
    }


# ─────────────────────────── market-data envelope ───────────────────────────


def test_mis_tagged_market_report_lands_in_market_history_not_str_trend() -> None:
    deal_id, doc_id = uuid4(), uuid4()
    env = _aggregate_market_data([_mis_tagged_costar(doc_id)], deal_id)

    # Not an STR trend: no subject block, no STR source, no STR doc name.
    assert env.str_trend is None
    assert "STR_TREND" not in env.sources
    assert env.sources == {"MARKET_STUDY": [doc_id]}

    by = {(p.doc_type, p.segment, p.period, p.calendar_year): p for p in env.market_history}
    assert all(k[0] == "MARKET_STUDY" for k in by), sorted(by)
    y2023 = by[("MARKET_STUDY", None, "annual", 2023)]
    assert (y2023.occupancy_pct, y2023.adr_usd) == (0.712, 288.0)
    # Provenance survives the re-routing: the document and page it was read from.
    assert (y2023.doc_name, y2023.page) == (COSTAR_NAME, 9)
    assert "market_study.trend.2023.occupancy_pct" in y2023.fields
    y2024 = by[("MARKET_STUDY", None, "annual", 2024)]
    assert y2024.occupancy_pct == 0.734 and y2024.revpar_usd == 219.0
    assert set(y2024.fields) == {
        "market_study.trend.2024.occupancy_pct", "pnl_benchmark.market.revpar_2024",
    }


def test_market_report_tagged_str_does_not_pollute_a_real_str_trend_on_the_same_deal() -> None:
    """Both reports on one deal (the tester shape): the newer, mis-tagged
    CoStar row arrives FIRST (created_at DESC) and must not win the STR
    subject slots over the genuine STR report."""
    deal_id, costar_id, str_id = uuid4(), uuid4(), uuid4()
    env = _aggregate_market_data([_mis_tagged_costar(costar_id), _genuine_str(str_id)], deal_id)

    assert env.str_trend is not None
    assert env.str_trend.doc_name == STR_NAME
    assert env.str_trend.subject_occupancy_pct == 78.4
    assert env.str_trend.subject_adr_usd == 341.0
    assert env.str_trend.subject_annual[0].calendar_year == 2024
    assert env.str_trend.subject_annual[0].page == 3

    assert env.sources == {"MARKET_STUDY": [costar_id], "STR_TREND": [str_id]}
    study_points = [p for p in env.market_history if p.doc_type == "MARKET_STUDY"]
    assert {p.calendar_year for p in study_points} == {2023, 2024}
    assert all(p.doc_name == COSTAR_NAME for p in study_points)
    # The STR report's own market rows (none here) are the only STR_TREND
    # history — the CoStar rows never appear under the STR_TREND doc_type.
    assert not [p for p in env.market_history if p.doc_type == "STR_TREND"]


def test_lane_is_read_from_the_stamp_alone_when_fields_are_ambiguous() -> None:
    """``agent_version`` ``dt:MARKET_STUDY`` on an STR-tagged row is enough —
    no market_study.* field, no router proposal needed."""
    deal_id, doc_id = uuid4(), uuid4()
    row = {
        "document_id": doc_id,
        "doc_type": "STR_TREND",
        "agent_version": MARKET_STUDY_STAMP,
        "ai_proposed_doc_type": None,
        "filename": COSTAR_NAME,
        "fields": [{"field_name": "ttm_performance.subject.occupancy_pct", "value": 61.0}],
    }
    env = _aggregate_market_data([row], deal_id)
    assert env.str_trend is None
    assert env.sources == {"MARKET_STUDY": [doc_id]}


def test_rows_without_lane_columns_keep_the_tag_behaviour() -> None:
    """``due_diligence`` / legacy callers pass rows with no ``agent_version``:
    an STR_TREND (or bare STR) tag still buckets as the STR block."""
    deal_id, doc_id = uuid4(), uuid4()
    legacy = {
        "document_id": doc_id,
        "doc_type": "STR",
        "fields": [{"field_name": "ttm_performance.subject.occupancy_pct", "value": 71.0}],
    }
    env = _aggregate_market_data([legacy], deal_id)
    assert env.str_trend is not None
    assert env.str_trend.subject_occupancy_pct == 71.0
    assert env.sources == {"STR_TREND": [doc_id]}


# ─────────────────────────── STR forecast loader ────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return [SimpleNamespace(_mapping=dict(r)) for r in self._rows]


class _FakeSession:
    """Answers every ``execute`` with the same rows; records the SQL + params."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(self.rows)


def _db_row(rec: dict[str, Any], created_at: str) -> dict[str, Any]:
    """What the loader's SELECT returns — ``fields`` as JSON TEXT (SQLite)."""
    return {
        "fields": json.dumps(rec["fields"]),
        "created_at": created_at,
        "agent_version": rec["agent_version"],
        "doc_type": rec["doc_type"],
        "ai_proposed_doc_type": rec["ai_proposed_doc_type"],
    }


def test_str_lane_rows_drops_the_market_study_lane_and_parses_fields_once() -> None:
    rows = [_db_row(_mis_tagged_costar(uuid4()), "2026-10-06"), _db_row(_genuine_str(uuid4()), "2026-09-01")]
    kept = str_lane_rows(rows)
    assert [r["created_at"] for r in kept] == ["2026-09-01"]
    assert kept[0]["_fields"][0]["field_name"] == "ttm_performance.subject.occupancy_pct"


@pytest.mark.asyncio
async def test_str_history_ignores_a_market_report_tagged_str() -> None:
    session = _FakeSession([_db_row(_mis_tagged_costar(uuid4()), "2026-10-06")])
    history = await load_str_history_for_deal(session, deal_id="deal-1", tenant_id="tenant-1")  # type: ignore[arg-type]
    assert history == []
    # Tenant scope stays on both sides of the join.
    sql, params = session.calls[0]
    assert "er.tenant_id = :tenant" in sql and "d.tenant_id = :tenant" in sql
    assert params == {"deal": "deal-1", "tenant": "tenant-1"}


@pytest.mark.asyncio
async def test_str_subject_ttm_ignores_a_market_report_tagged_str() -> None:
    session = _FakeSession([_db_row(_mis_tagged_costar(uuid4()), "2026-10-06")])
    assert await load_str_subject_ttm(session, deal_id="deal-1", tenant_id=None) is None  # type: ignore[arg-type]
    sql, params = session.calls[0]
    assert ":tenant" not in sql and params == {"deal": "deal-1"}


@pytest.mark.asyncio
async def test_str_readers_take_the_genuine_str_report_past_a_newer_mis_tagged_one() -> None:
    """Newest-first order puts the CoStar row first; the STR lane filter means
    the genuine report's subject TTM and months are what the forecast reads."""
    rows = [_db_row(_mis_tagged_costar(uuid4()), "2026-10-06"), _db_row(_genuine_str(uuid4()), "2026-09-01")]
    session = _FakeSession(rows)

    ttm = await load_str_subject_ttm(session, deal_id="deal-1", tenant_id="tenant-1")  # type: ignore[arg-type]
    assert ttm == (pytest.approx(0.784), 341.0)

    history = await load_str_history_for_deal(session, deal_id="deal-1", tenant_id="tenant-1")  # type: ignore[arg-type]
    assert [m.period for m in history] == ["2024-05", "2024-06"]
    june = history[1]
    # The STR report's June (76% / $330), never the market report's (60% / $150).
    assert june.occupancy == pytest.approx(0.76)
    assert june.adr == 330.0


# ───────────── comp-set roster + indices: one report, newest wins ─────────────


def _str_report(*, compset_revpars: list[float], rgi: float | None, with_month: bool = True) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    if with_month:
        fields += [
            {"field_name": "ttm_performance.subject.monthly.2024_06.occupancy_pct", "value": 80.0},
            {"field_name": "ttm_performance.subject.monthly.2024_06.adr_usd", "value": 250.0},
        ]
    for i, rp in enumerate(compset_revpars):
        fields.append({"field_name": f"ttm_performance.compset.{i}.name", "value": f"Hotel {i}"})
        fields.append({"field_name": f"ttm_performance.compset.{i}.revpar_usd", "value": rp})
    if rgi is not None:
        fields.append({"field_name": "ttm_performance.indices.rgi_revpar_index", "value": rgi})
    return {
        "doc_type": "STR_TREND",
        "agent_version": STR_STAMP,
        "ai_proposed_doc_type": "STR_TREND",
        "fields": fields,
    }


@pytest.mark.asyncio
async def test_str_history_comp_set_roster_is_the_newest_report_only() -> None:
    """Newest report lists 2 hotels; an older one lists 3. The comp-set RevPAR
    baseline must be the newest report's 2-hotel average — the older report's
    hotel at index 2 must not be merged in by position."""
    newest = _str_report(compset_revpars=[100.0, 200.0], rgi=None)
    older = _str_report(compset_revpars=[900.0, 900.0, 900.0], rgi=None, with_month=False)
    session = _FakeSession([_db_row(newest, "2026-10-06"), _db_row(older, "2026-09-01")])
    history = await load_str_history_for_deal(session, deal_id="deal-1", tenant_id="tenant-1")  # type: ignore[arg-type]
    assert len(history) == 1
    assert history[0].comp_set_revpar == pytest.approx(150.0)


@pytest.mark.asyncio
async def test_str_history_roster_falls_back_to_an_older_report_when_newest_has_none() -> None:
    newest = _str_report(compset_revpars=[], rgi=None)
    older = _str_report(compset_revpars=[120.0, 180.0], rgi=None, with_month=False)
    session = _FakeSession([_db_row(newest, "2026-10-06"), _db_row(older, "2026-09-01")])
    history = await load_str_history_for_deal(session, deal_id="deal-1", tenant_id="tenant-1")  # type: ignore[arg-type]
    assert history[0].comp_set_revpar == pytest.approx(150.0)


@pytest.mark.asyncio
async def test_str_history_indices_newest_extraction_wins() -> None:
    """No comp set -> the month's index falls back to the RGI; the newest
    extraction's RGI (1.10) must win over the older one's (0.80)."""
    newest = _str_report(compset_revpars=[], rgi=1.10)
    older = _str_report(compset_revpars=[], rgi=0.80, with_month=False)
    session = _FakeSession([_db_row(newest, "2026-10-06"), _db_row(older, "2026-09-01")])
    history = await load_str_history_for_deal(session, deal_id="deal-1", tenant_id="tenant-1")  # type: ignore[arg-type]
    assert history[0].revpar_index == pytest.approx(1.10)


# ───────────── market-data envelope: closed comp-set hotels excluded ─────────────


def _roster_report(doc_id: Any, roster: list[tuple[str, int]]) -> dict[str, Any]:
    fields: list[dict[str, Any]] = [
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 78.4},
    ]
    for i, (name, keys) in enumerate(roster, start=1):
        fields.append({"field_name": f"ttm_performance.compset.{i}.name", "value": name})
        fields.append({"field_name": f"ttm_performance.compset.{i}.keys", "value": keys})
    return {
        "document_id": doc_id,
        "doc_type": "STR_TREND",
        "agent_version": STR_STAMP,
        "ai_proposed_doc_type": "STR_TREND",
        "filename": STR_NAME,
        "fields": fields,
    }


def test_envelope_roster_excludes_a_hotel_an_older_report_marks_closed() -> None:
    """Live E-009 shape: the newest trend report lists "Blue Moon Hotel · 75
    rooms" with no marker; an older daily report lists "Closed - Blue Moon
    Hotel · 0 rooms". The envelope shows the newest roster, flags Blue Moon
    closed, and leaves its 75 keys out of the total Index Analysis reads."""
    newest = _roster_report(uuid4(), [("The Betsy", 130), ("Blue Moon Hotel", 75), ("The Tony", 214)])
    older = _roster_report(uuid4(), [("Closed - Blue Moon Hotel", 0), ("The Tony", 214)])
    env = _aggregate_market_data([newest, older], uuid4())
    assert env.str_trend is not None
    by_name = {e.name: e for e in env.str_trend.compset}
    assert set(by_name) == {"The Betsy", "Blue Moon Hotel", "The Tony"}
    assert by_name["Blue Moon Hotel"].status == "closed"
    assert by_name["The Betsy"].status == "active"
    assert env.str_trend.total_keys == 130 + 214
    assert env.str_trend.comp_set_size == 2


def test_envelope_roster_without_closure_markers_counts_every_hotel() -> None:
    """A 0-room row with no marker is NOT closed — nothing is inferred."""
    only = _roster_report(uuid4(), [("The Betsy", 130), ("Blue Moon Hotel", 0)])
    env = _aggregate_market_data([only], uuid4())
    assert env.str_trend is not None
    assert all(e.status == "active" for e in env.str_trend.compset)
    assert env.str_trend.total_keys == 130
    assert env.str_trend.comp_set_size == 2
