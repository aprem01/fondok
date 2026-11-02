"""Review I-3 (W5 topline fix) — a CoStar submarket report extracted BEFORE the
class-bucket series were read (pipeline v9) is "on file but extracted before
class forecasts were read — re-extract", never "not in the report".

Plan §6: detection reads the extraction's ``agent_version`` ``;pv=`` suffix.
Sam's QA deal carries ``Miami Beach-Hospitality-Submarket-2025-12-10.pdf``
extracted under v8: 233 overall ``market_study.trend.*`` rows, no
``market_study.class.*`` rows — yet the Luxury & Upper Upscale table is on
p.25/26 of the PDF.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-topline-reextract.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

import pytest
from sqlalchemy import text

from app.services.index_methodology import rows_from_records
from app.services.market_fields import pipeline_version_number
from app.services.market_study_reader import read_class_forecast
from app.services.topline_growth import GrowthOverrides, resolve_growth_by_year
from app.tenants import TenantDataPolicies
from fondok_schemas.reasons import ReasonCode

INPUT = Path(__file__).resolve().parents[3] / "evals" / "golden-set" / "kimpton-angler" / "input"
CAL = [2026, 2027, 2028, 2029, 2030]
REEXTRACT = "extracted before class forecasts were read — re-extract"


def _costar(*, pv: str | None, keep_class: bool = True, keep_trend: bool = True) -> list:
    d = json.loads((INPUT / "costar_submarket_extracted.json").read_text())
    fields = [
        f for f in d["fields"]
        if (keep_class or not f["field_name"].startswith("market_study.class."))
        and (keep_trend or not f["field_name"].startswith("market_study.trend."))
    ]
    agent = f"router:costar;dt:MARKET_STUDY;extractor;ps=1;reg=1;pv={pv}" if pv else "fixture"
    return rows_from_records(
        [
            {
                "extraction_id": "ext-costar",
                "fields": fields,
                "document_id": d["document_id"],
                "filename": d["filename"],
                "doc_type": "MARKET_STUDY",
                "report_as_of": d.get("report_as_of"),
                "created_at": "2025-12-11",
                "agent_version": agent,
            }
        ]
    )


def _growth(rows, bucket: str):
    return resolve_growth_by_year(
        rows, GrowthOverrides(), calendar_years=CAL, hold_years=5,
        class_bucket=bucket, policy=TenantDataPolicies(),
    )


def test_pipeline_version_is_read_off_the_agent_version() -> None:
    assert pipeline_version_number("router:x;dt:MARKET_STUDY;extractor;ps=1;reg=2;pv=v8") == 8
    assert pipeline_version_number("router:x;dt:MARKET_STUDY;extractor;pv=v10") == 10
    assert pipeline_version_number("fixture") is None
    assert pipeline_version_number(None) is None


def test_pre_v9_report_without_class_rows_asks_for_a_reextract_not_not_in_report() -> None:
    rows = _costar(pv="v8", keep_class=False)
    reading = read_class_forecast(rows)
    assert reading.predates_class_series is True
    series = reading.series("luxury_upper_upscale")
    assert series.years == ()  # the overall series is NOT served under a stale extraction
    assert REEXTRACT in (series.fallback_reason or "")
    assert "not in the report" not in (series.fallback_reason or "")

    g = _growth(rows, "luxury_upper_upscale")
    assert any(REEXTRACT in r for r in g.reextract_needed), g.reextract_needed
    assert REEXTRACT in (g.bucket_note or "")
    for y in range(1, 6):
        for metric in ("occupancy", "adr"):
            hit = g.year(y, metric)
            assert hit.value is None and hit.source == "unavailable"
            assert hit.reason is ReasonCode.REEXTRACT_NEEDED
            assert REEXTRACT in hit.label
            assert "No market" not in hit.label


def test_pre_v9_report_serves_the_overall_series_when_the_deal_asks_for_it() -> None:
    """The overall (all classes) series existed before v9; nothing to re-extract."""
    g = _growth(_costar(pv="v8", keep_class=False), "all")
    assert g.reextract_needed == []
    assert g.year(1, "adr").source == "costar_submarket_forecast"


def test_pre_v9_report_with_no_series_at_all_asks_for_a_reextract() -> None:
    g = _growth(_costar(pv="v8", keep_class=False, keep_trend=False), "luxury_upper_upscale")
    assert len(g.reextract_needed) == 1 and REEXTRACT in g.reextract_needed[0]
    assert g.year(2, "adr").reason is ReasonCode.REEXTRACT_NEEDED


def test_current_extraction_without_the_bucket_falls_back_to_the_overall_series() -> None:
    """A v9+ extraction that truly lacks the bucket: the overall series, labelled."""
    rows = _costar(pv="v9", keep_class=False)
    g = _growth(rows, "luxury_upper_upscale")
    assert g.reextract_needed == []
    assert "not in the report" in (g.bucket_note or "")
    assert g.year(1, "adr").source == "costar_submarket_forecast"


@pytest.mark.parametrize("pv", [None, "v9"])
def test_current_or_unstamped_extraction_with_class_rows_reads_the_bucket(pv) -> None:
    g = _growth(_costar(pv=pv), "luxury_upper_upscale")
    assert g.reextract_needed == []
    assert g.bucket_note is None
    assert g.year(1, "adr").value == pytest.approx(0.018)


@pytest.mark.asyncio
async def test_loader_reports_the_reextract_on_sams_qa_deal_shape() -> None:
    """The QA deal's shape: an upper-upscale deal whose CoStar report was
    extracted under v8 (overall trend rows, no class rows)."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import _load_engine_inputs

    from tests.fixtures.kimpton_docs_fixture import seed_kimpton_docs_deal

    await run_startup_migrations()
    async with get_session_factory()() as s:
        for tbl in ("engine_outputs", "extraction_results", "documents", "deals"):
            await s.execute(text(f"DELETE FROM {tbl}"))
        deal_id, tenant_id = await seed_kimpton_docs_deal(s)
        row = (await s.execute(
            text("SELECT id, fields FROM extraction_results WHERE deal_id = :d AND fields LIKE '%market_study.class%'"),
            {"d": deal_id},
        )).first()
        fields = [f for f in json.loads(row[1]) if not f["field_name"].startswith("market_study.class.")]
        await s.execute(
            text("UPDATE extraction_results SET fields = :f, agent_version = :a WHERE id = :id"),
            {"f": json.dumps(fields), "a": "router:costar;dt:MARKET_STUDY;extractor;pv=v8", "id": row[0]},
        )
        await s.commit()
        base = await _load_engine_inputs(s, deal_id, tenant_id=tenant_id)
    st = base["__topline_status__"]
    assert any(REEXTRACT in r for r in st["reextract_needed"])
    assert REEXTRACT in (st["bucket_note"] or "")
    assert "not in the report" not in json.dumps(st)
    # 2027–2029 were CoStar-only years (2026 / 2030 carry the analyst's keys).
    g27 = st["growth_by_year"][1]["adr"]
    assert g27["value"] is None and g27["reason"] == "reextract_needed"
