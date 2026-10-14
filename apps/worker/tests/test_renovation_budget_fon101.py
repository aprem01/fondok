"""FON-101 — PIP / CapEx documents feed the renovation budget, OM-vs-PIP
precedence shown.

Sam's decision (2026-10-14): a separate PIP or CapEx document outranks the
OM when both state a renovation budget; both values stay visible with their
sources; the analyst can override the selection; a newer plan supersedes an
older PIP; Future CapEx documents are reference only.

Covered here:

* the ``capex.md`` extraction schema loads through the dynamic loader AND
  the legacy ``SYSTEM_PROMPT`` carries the same contract;
* the critic's PIP-total-vs-scope-items check flags and never alters;
* loader precedence — OM only · OM + one PIP · two PIPs by date · plan date
  beats upload order · override wins · shadow override keeps the PIP label ·
  ``future`` is never selected · the Year-1 displacement guard sees a PIP;
* source stamping (``pip_document`` + the exact row, dated);
* ``GET /deals/{id}/renovation-budget-candidates`` — every candidate with
  its selected flag and reason, Future CapEx under ``reference_only``,
  tenant-scoped (cross-tenant id → 404).

Real per-test SQLite DB; no LLM.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-renovation-budget-fon101.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-00000000f101"
_OTHER_TENANT = "00000000-0000-0000-0000-00000000f102"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


# ───────────────────────────── fixtures ─────────────────────────────


async def _insert_deal(
    deal_id: UUID,
    *,
    keys: int = 132,
    purchase: float = 36_400_000,
    field_overrides: dict[str, object] | None = None,
    tenant: str = _TENANT,
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, field_overrides, created_at, updated_at
                ) VALUES (
                    :id, :tenant, 'FON-101 Hotel', 'Underwriting', 0.0, :keys,
                    :pp, :fo, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": tenant,
                "keys": keys,
                "pp": purchase,
                "fo": json.dumps(field_overrides or {}),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_doc(
    deal_id: UUID,
    *,
    doc_type: str,
    filename: str,
    fields: list[dict[str, object]] | None,
    ts: datetime,
    doc_subtype: str | None = None,
    tenant: str = _TENANT,
) -> tuple[UUID, UUID | None]:
    """An uploaded document (+ its extraction when ``fields`` is given)."""
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    er_id: UUID | None = None
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, doc_subtype, status,
                    uploaded_at, page_count
                ) VALUES (
                    :id, :deal, :tenant, :filename, :doc_type, :subtype,
                    :status, :ts, 1
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant,
                "filename": filename,
                "doc_type": doc_type,
                "subtype": doc_subtype,
                "status": "EXTRACTED" if fields is not None else "UPLOADED",
                "ts": ts,
            },
        )
        if fields is not None:
            er_id = uuid4()
            await session.execute(
                text(
                    """
                    INSERT INTO extraction_results (
                        id, document_id, deal_id, tenant_id, fields,
                        confidence_report, agent_version, created_at
                    ) VALUES (
                        :id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts
                    )
                    """
                ),
                {
                    "id": str(er_id),
                    "doc": str(doc_id),
                    "deal": str(deal_id),
                    "tenant": tenant,
                    "fields": json.dumps(fields),
                    "ts": ts,
                },
            )
        await session.commit()
    return doc_id, er_id


def _row(name: str, value: object, page: int = 1) -> dict[str, object]:
    return {
        "field_name": name,
        "value": value,
        "source_page": page,
        "confidence": 0.9,
        "unit": "USD" if isinstance(value, (int, float)) else "text",
    }


_OM_FIELDS = [_row("broker_proforma.renovation_budget_usd", 8_000_000.0, page=31)]


def _pip_fields(
    total: float,
    *,
    plan_date: str | None,
    status: str | None = "brand_issued_pip",
    items: list[float] | None = None,
) -> list[dict[str, object]]:
    out = [_row("capex_plan.pip.total_usd", total, page=4)]
    if plan_date is not None:
        out.append(_row("capex_plan.document.plan_date", plan_date, page=1))
    if status is not None:
        out.append(_row("capex_plan.document.status", status, page=1))
    out.append(_row("capex_plan.document.prepared_by", "Hilton PIP Team", page=1))
    for i, usd in enumerate(items or [], start=1):
        out.append(_row(f"capex_plan.pip.scope_items.{i}.name", f"Scope {i}", page=5))
        out.append(_row(f"capex_plan.pip.scope_items.{i}.usd", usd, page=5))
        out.append(_row(f"capex_plan.pip.scope_items.{i}.category", "guestrooms", page=5))
    return out


def _future_fields(total: float) -> list[dict[str, object]]:
    return [
        _row("future_capex.items.1.name", "Roof replacement", page=2),
        _row("future_capex.items.1.year", 2028, page=2),
        _row("future_capex.items.1.usd", total * 0.4, page=2),
        _row("future_capex.items.2.name", "Soft-goods refresh", page=2),
        _row("future_capex.items.2.year", 2029, page=2),
        _row("future_capex.items.2.usd", total * 0.6, page=2),
        _row("future_capex.total_usd", total, page=2),
        _row("future_capex.plan_date", "2026-09-01", page=1),
    ]


async def _base_for(deal_id: UUID, tenant: str = _TENANT) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        return await _load_engine_inputs(session, str(deal_id), tenant_id=tenant)


_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# ───────────────────────────── schema ───────────────────────────────


def test_capex_schema_loads_and_names_every_contract_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.extraction_schemas.loader import available_doc_types, build_system_prompt

    assert "CAPEX" in available_doc_types()
    monkeypatch.setenv("EXTRACTOR_USE_DYNAMIC_SCHEMAS", "1")
    prompt = build_system_prompt("CAPEX")
    assert prompt is not None
    for path in (
        "capex_plan.pip.total_usd",
        "capex_plan.pip.per_key_usd",
        "capex_plan.pip.hard_usd",
        "capex_plan.pip.soft_usd",
        "capex_plan.pip.contingency_usd",
        "capex_plan.pip.completion_quarter",
        "capex_plan.pip.timing",
        "capex_plan.pip.scope_items.<n>.name",
        "capex_plan.pip.scope_items.<n>.usd",
        "capex_plan.pip.scope_items.<n>.category",
        "capex_plan.document.plan_date",
        "capex_plan.document.status",
        "capex_plan.document.prepared_by",
        "future_capex.items.<n>.year",
        "future_capex.items.<n>.usd",
    ):
        assert path in prompt, path
    for status in ("brand_issued_pip", "owner_budget", "approved_capital_plan", "estimate"):
        assert status in prompt, status
    # The base preamble (page + source text per field) is always prepended.
    assert "source_page" in prompt and "raw_text" in prompt
    # No generic fallback note — the doc type has its own schema.
    assert "no doc-type-specific schema available" not in prompt


def test_legacy_system_prompt_carries_the_same_capex_contract() -> None:
    """Production sends the embedded prompt unless the dynamic flag is on, so
    the CAPEX contract must live there too or nothing is read from a PIP."""
    from app.agents.extractor import SYSTEM_PROMPT

    for path in (
        "capex_plan.pip.total_usd",
        "capex_plan.pip.scope_items.<n>.name",
        "capex_plan.document.plan_date",
        "capex_plan.document.status",
        "future_capex.items.<n>.name",
    ):
        assert path in SYSTEM_PROMPT, path
    assert "NEVER emit its total as ``capex_plan.pip.total_usd``" in SYSTEM_PROMPT


def test_pipeline_version_bumped_for_the_capex_contract() -> None:
    from app.api.documents import EXTRACTION_PIPELINE_VERSION, _sibling_reuse_allowed

    assert EXTRACTION_PIPELINE_VERSION == "v8"
    # CAPEX is LLM-extracted (no deterministic template), so sibling reuse is
    # allowed for it — the loader gates on the document's own doc_subtype.
    assert _sibling_reuse_allowed("CAPEX") is True


def test_source_id_is_registered_everywhere() -> None:
    from app.ontology.registry import load_registry
    from app.services import lineage
    from app.services.engine_runner import (
        _NON_DOCUMENT_SOURCES,
        _SEED_REASON_DOC_TYPES,
        _SOURCE_TO_DOC_TYPES,
        SOURCE_PIP_DOCUMENT,
    )
    from app.services.renovation_budget import SOURCE_PIP_DOCUMENT as OWNER_ID

    assert SOURCE_PIP_DOCUMENT == OWNER_ID == "pip_document"
    reg = load_registry()
    src = reg.sources["pip_document"]
    assert src.kind == "grounded"
    assert list(src.doc_types) == ["CAPEX"]
    assert _SOURCE_TO_DOC_TYPES[SOURCE_PIP_DOCUMENT] == ("CAPEX",)
    assert SOURCE_PIP_DOCUMENT not in _NON_DOCUMENT_SOURCES
    assert _SEED_REASON_DOC_TYPES["renovation_budget"] == ("OM", "CAPEX")
    assert "pip_document" in lineage._DOCUMENT_LABELS


# ───────────────────────────── critic ───────────────────────────────


def test_critic_flags_pip_total_off_from_scope_items_without_altering() -> None:
    from app.agents.critic import apply_capex_plausibility

    fields = _pip_fields(5_000_000, plan_date="2026-03-01", items=[1_000_000, 1_000_000, 1_000_000])
    fields[0]["confidence"] = 0.93
    out_fields, conf, flags = apply_capex_plausibility(fields, {"overall": 0.9}, doc_type="CAPEX")

    assert len(flags) == 1
    flag = flags[0]
    assert flag.field_name == "capex_plan.pip.total_usd"
    assert flag.total_usd == 5_000_000 and flag.scope_items_sum_usd == 3_000_000
    assert flag.scope_items_count == 3
    assert flag.delta_pct == pytest.approx(0.4)
    total = next(f for f in out_fields if f["field_name"] == "capex_plan.pip.total_usd")
    # Flag, never alter: value AND confidence untouched; the reason rides the note.
    assert total["value"] == 5_000_000
    assert total["confidence"] == 0.93
    assert "scope items" in total["note"]
    assert conf["requires_human_review"] is True
    assert conf["plausibility_flags"][0]["field_name"] == "capex_plan.pip.total_usd"
    assert "low_confidence_fields" not in conf
    # Inputs are not mutated.
    assert "note" not in fields[0]


def test_critic_accepts_total_within_two_percent_and_skips_other_doc_types() -> None:
    from app.agents.critic import apply_capex_plausibility, check_capex_plausibility

    fields = _pip_fields(3_050_000, plan_date=None, items=[1_000_000, 1_000_000, 1_000_000])
    assert check_capex_plausibility(fields, doc_type="CAPEX") == []
    # No scope items → nothing to compare.
    assert check_capex_plausibility(_pip_fields(9_000_000, plan_date=None), doc_type="CAPEX") == []
    # Same rows on a non-CAPEX document are not this check's business.
    bad = _pip_fields(5_000_000, plan_date=None, items=[1_000_000])
    assert check_capex_plausibility(bad, doc_type="OM") == []
    out, conf, flags = apply_capex_plausibility(bad, None, doc_type="T12")
    assert flags == [] and conf == {} and out[0]["value"] == 5_000_000


def test_critic_respects_an_analyst_reviewed_total() -> None:
    from app.agents.critic import check_capex_plausibility

    fields = _pip_fields(5_000_000, plan_date=None, items=[1_000_000])
    fields[0]["reviewed"] = "accepted"
    assert check_capex_plausibility(fields, doc_type="CAPEX") == []


# ───────────────────────── pure precedence ──────────────────────────


def test_iso_date_precision_and_selection_rule() -> None:
    from app.services.renovation_budget import CapexDocument, _iso_date, select_pip_candidate

    assert _iso_date("2026-03-15") == "2026-03-15"
    assert _iso_date("2026-03") == "2026-03-01"
    assert _iso_date("2026") == "2026-01-01"
    assert _iso_date("Q3 2027") is None
    assert _iso_date(datetime(2026, 1, 2, 3, tzinfo=UTC)) == "2026-01-02"

    older = CapexDocument(document_id="a", filename="old.pdf", uploaded_at="2026-10-05",
                          pip_total_usd=5_000_000, plan_date="2025-11-01")
    newer = CapexDocument(document_id="b", filename="new.pdf", uploaded_at="2026-10-01",
                          pip_total_usd=6_000_000, plan_date="2026-03-01")
    future = CapexDocument(document_id="c", filename="plan.pdf", uploaded_at="2026-10-09",
                           pip_total_usd=9_000_000, plan_date="2026-09-01", doc_subtype="future")
    no_total = CapexDocument(document_id="d", filename="x.pdf", uploaded_at="2026-10-09",
                             plan_date="2026-12-01")
    assert select_pip_candidate([older, newer, future, no_total]) is newer
    assert select_pip_candidate([future, no_total]) is None
    assert select_pip_candidate([]) is None
    # Without a plan date the upload date orders.
    undated_a = CapexDocument(document_id="e", filename="a.pdf", uploaded_at="2026-10-02", pip_total_usd=1)
    undated_b = CapexDocument(document_id="f", filename="b.pdf", uploaded_at="2026-10-03", pip_total_usd=2)
    assert select_pip_candidate([undated_a, undated_b]) is undated_b
    assert undated_b.effective_date == ("2026-10-03", "uploaded_at")
    assert newer.effective_date == ("2026-03-01", "plan_date")


# ───────────────────────── loader precedence ────────────────────────


@pytest.mark.asyncio
async def test_om_only_stays_on_the_broker_figure() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)

    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(8_000_000)
    assert base["__sources__"]["renovation_budget"] == "om_broker"


@pytest.mark.asyncio
async def test_pip_document_outranks_the_om_and_is_stamped_with_its_row() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    pip_doc, pip_er = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="Hilton PIP 2026.pdf",
        fields=_pip_fields(6_250_000, plan_date="2026-03-15"), ts=_T0 + timedelta(hours=1),
    )

    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(6_250_000)
    assert base["__sources__"]["renovation_budget"] == "pip_document"
    row = base["__source_fields__"]["renovation_budget"]
    assert row["document_id"] == str(pip_doc)
    assert row["extraction_result_id"] == str(pip_er)
    assert row["field_name"] == "capex_plan.pip.total_usd"
    assert row["source_page"] == 4
    assert row["doc_type"] == "CAPEX"
    assert row["as_of"] == "2026-03-15"
    # The registry classifies the CAPEX path onto the same concept.
    assert row["concept"] == "renovation_budget"
    assert row["basis"] == "budget"
    # A material PIP displaces Year 1 (the guard sees a deal-specific budget).
    assert base["y1_occupancy_displacement_pct"] == pytest.approx(0.15)
    assert "renovation_budget" not in base["__reasons__"]


@pytest.mark.asyncio
async def test_latest_plan_date_wins_over_upload_order() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    # Uploaded LAST but dated earlier — the old brand PIP.
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="Brand PIP 2025.pdf",
        fields=_pip_fields(5_000_000, plan_date="2025-11-01"), ts=_T0 + timedelta(days=2),
    )
    # Uploaded first but dated later — the approved capital plan supersedes.
    newer_doc, _ = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="Approved Capital Plan 2026.xlsx",
        fields=_pip_fields(7_100_000, plan_date="2026-04-01", status="approved_capital_plan"),
        ts=_T0 + timedelta(days=1),
    )

    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(7_100_000)
    assert base["__source_fields__"]["renovation_budget"]["document_id"] == str(newer_doc)


@pytest.mark.asyncio
async def test_without_plan_dates_the_newest_upload_wins() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="first.pdf",
        fields=_pip_fields(5_000_000, plan_date=None), ts=_T0,
    )
    later, _ = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="second.pdf",
        fields=_pip_fields(5_500_000, plan_date=None), ts=_T0 + timedelta(days=1),
    )
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(5_500_000)
    assert base["__source_fields__"]["renovation_budget"]["document_id"] == str(later)
    assert base["__source_fields__"]["renovation_budget"]["as_of"] == "2026-10-02"


@pytest.mark.asyncio
async def test_analyst_override_wins_over_the_pip_document() -> None:
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        field_overrides={"renovation_budget": {"value": 9_000_000, "note": "IC-approved budget"}},
    )
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="PIP.pdf",
        fields=_pip_fields(6_250_000, plan_date="2026-03-15"), ts=_T0 + timedelta(hours=1),
    )
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(9_000_000)
    assert base["__sources__"]["renovation_budget"] == "analyst_override"
    # An override is not a document row.
    assert "renovation_budget" not in base["__source_fields__"]


@pytest.mark.asyncio
async def test_override_equal_to_the_pip_keeps_the_pip_label() -> None:
    """FON-65 shadow rule: "Use this" writes an override equal to the PIP
    figure, and the model keeps reporting the PIP document as the source."""
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        field_overrides={"renovation_budget": {"value": 6_250_000, "note": "Selected PIP.pdf dated 2026-03-15"}},
    )
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    pip_doc, _ = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="PIP.pdf",
        fields=_pip_fields(6_250_000, plan_date="2026-03-15"), ts=_T0 + timedelta(hours=1),
    )
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(6_250_000)
    assert base["__sources__"]["renovation_budget"] == "pip_document"
    assert base["__source_fields__"]["renovation_budget"]["document_id"] == str(pip_doc)


@pytest.mark.asyncio
async def test_future_capex_is_never_selected() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    # A Future CapEx document that even carries a PIP total — still reference only.
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="5yr capital plan.xlsx", doc_subtype="future",
        fields=_future_fields(2_000_000) + _pip_fields(12_000_000, plan_date="2026-09-01"),
        ts=_T0 + timedelta(days=3),
    )
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(8_000_000)
    assert base["__sources__"]["renovation_budget"] == "om_broker"


@pytest.mark.asyncio
async def test_future_capex_alone_leaves_the_seed_with_a_reason() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="5yr capital plan.xlsx", doc_subtype="future",
        fields=_future_fields(2_000_000), ts=_T0,
    )
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(5_280_000)  # Kimpton seed
    assert base["__sources__"]["renovation_budget"] == "seed"
    # A CAPEX document is on the deal but nothing in it resolves → no_source.
    assert base["__reasons__"]["renovation_budget"]["code"] == "no_source"


@pytest.mark.asyncio
async def test_capex_document_without_a_pip_total_does_not_ground() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="historic capex.xlsx", doc_subtype="historic",
        fields=[_row("historic_capex.total_usd", 1_200_000)], ts=_T0 + timedelta(days=1),
    )
    # And one that is uploaded but not yet extracted.
    await _insert_doc(deal_id, doc_type="CAPEX", filename="pending.pdf", fields=None, ts=_T0 + timedelta(days=2))
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(8_000_000)
    assert base["__sources__"]["renovation_budget"] == "om_broker"


# ───────────────────────────── endpoint ─────────────────────────────


async def _get(path: str, tenant: str) -> tuple[int, dict]:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(path, headers={"X-Tenant-Id": tenant})
    return r.status_code, (r.json() if r.content else {})


@pytest.mark.asyncio
async def test_candidates_endpoint_lists_every_source_and_marks_the_selection() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, keys=100)
    om_doc, _ = await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)
    old_doc, _ = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="Brand PIP 2025.pdf",
        fields=_pip_fields(5_000_000, plan_date="2025-11-01", items=[2_500_000, 2_500_000]),
        ts=_T0 + timedelta(days=2),
    )
    new_doc, _ = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="Approved Capital Plan 2026.xlsx",
        fields=_pip_fields(7_100_000, plan_date="2026-04-01", status="approved_capital_plan"),
        ts=_T0 + timedelta(days=1),
    )
    future_doc, _ = await _insert_doc(
        deal_id, doc_type="CAPEX", filename="5yr plan.xlsx", doc_subtype="future",
        fields=_future_fields(2_000_000), ts=_T0 + timedelta(days=3),
    )
    pending_doc, _ = await _insert_doc(deal_id, doc_type="CAPEX", filename="pending.pdf", fields=None, ts=_T0)

    code, body = await _get(f"/deals/{deal_id}/renovation-budget-candidates", _TENANT)
    assert code == 200, body
    assert body["selected_source"] == "pip_document"
    assert body["selected_value_usd"] == pytest.approx(7_100_000)
    assert body["keys"] == 100

    by_id = {c["id"]: c for c in body["candidates"]}
    assert set(by_id) == {
        f"document:{old_doc}", f"document:{new_doc}", f"document:{pending_doc}", "om:renovation_budget",
    }
    selected = [c for c in body["candidates"] if c["selected"]]
    assert len(selected) == 1 and selected[0]["document_id"] == str(new_doc)

    newest = by_id[f"document:{new_doc}"]
    assert newest["kind"] == "pip_document"
    assert newest["value_usd"] == pytest.approx(7_100_000)
    assert newest["per_key_usd"] == pytest.approx(71_000)
    assert newest["date"] == "2026-04-01" and newest["date_basis"] == "plan_date"
    assert newest["status"] == "approved_capital_plan"
    assert newest["status_label"] == "Approved capital plan"
    assert newest["prepared_by"] == "Hilton PIP Team"
    assert newest["page"] == 4
    assert "outranks the OM" in newest["reason"]

    older = by_id[f"document:{old_doc}"]
    assert older["selected"] is False
    assert older["status_label"] == "Brand-issued PIP"
    assert "Superseded by Approved Capital Plan 2026.xlsx dated 2026-04-01" in older["reason"]
    assert [i["usd"] for i in older["scope_items"]] == [2_500_000, 2_500_000]

    pending = by_id[f"document:{pending_doc}"]
    assert pending["value_usd"] is None
    assert pending["selected"] is False
    assert "Not extracted yet" in pending["reason"]

    om = by_id["om:renovation_budget"]
    assert om["kind"] == "om_broker"
    assert om["value_usd"] == pytest.approx(8_000_000)
    assert om["document_id"] == str(om_doc)
    assert om["page"] == 31
    assert om["selected"] is False
    assert "Outranked by the PIP / CapEx document" in om["reason"]

    assert len(body["reference_only"]) == 1
    ref = body["reference_only"][0]
    assert ref["document_id"] == str(future_doc)
    assert ref["total_usd"] == pytest.approx(2_000_000)
    assert ref["date"] == "2026-09-01"
    assert [i["year"] for i in ref["items"]] == [2028, 2029]
    assert "reference only" in ref["reason"]
    assert "Future CapEx" in body["precedence"]


@pytest.mark.asyncio
async def test_candidates_endpoint_reports_the_override_and_the_om_when_no_pip() -> None:
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        field_overrides={"renovation_budget": {"value": 9_000_000, "note": "IC-approved budget"}},
    )
    await _insert_doc(deal_id, doc_type="OM", filename="OM.pdf", fields=_OM_FIELDS, ts=_T0)

    code, body = await _get(f"/deals/{deal_id}/renovation-budget-candidates", _TENANT)
    assert code == 200, body
    assert body["selected_source"] == "analyst_override"
    assert body["selected_value_usd"] == pytest.approx(9_000_000)
    kinds = [c["kind"] for c in body["candidates"]]
    assert kinds == ["analyst_override", "om_broker"]
    override = body["candidates"][0]
    assert override["selected"] is True
    assert override["note"] == "IC-approved budget"
    assert override["value_usd"] == pytest.approx(9_000_000)
    om = body["candidates"][1]
    assert om["selected"] is False and "override is active" in om["reason"]
    assert body["reference_only"] == []


@pytest.mark.asyncio
async def test_candidates_endpoint_with_nothing_but_the_seed() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    code, body = await _get(f"/deals/{deal_id}/renovation-budget-candidates", _TENANT)
    assert code == 200, body
    assert body["selected_source"] == "seed"
    assert body["candidates"] == [] and body["reference_only"] == []
    assert body["selected_value_usd"] == pytest.approx(5_280_000)


@pytest.mark.asyncio
async def test_candidates_endpoint_is_tenant_scoped() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_doc(
        deal_id, doc_type="CAPEX", filename="PIP.pdf",
        fields=_pip_fields(6_250_000, plan_date="2026-03-15"), ts=_T0,
    )
    code, _ = await _get(f"/deals/{deal_id}/renovation-budget-candidates", _OTHER_TENANT)
    assert code == 404
    code, _ = await _get(f"/deals/{uuid4()}/renovation-budget-candidates", _TENANT)
    assert code == 404

    # A same-id CAPEX document under ANOTHER tenant never leaks into this
    # deal's candidates (the document read is tenant-scoped on both tables).
    other_deal = uuid4()
    await _insert_deal(other_deal, tenant=_OTHER_TENANT)
    await _insert_doc(
        other_deal, doc_type="CAPEX", filename="Other tenant PIP.pdf",
        fields=_pip_fields(99_000_000, plan_date="2027-01-01"), ts=_T0, tenant=_OTHER_TENANT,
    )
    code, body = await _get(f"/deals/{deal_id}/renovation-budget-candidates", _TENANT)
    assert code == 200
    assert [c["filename"] for c in body["candidates"]] == ["PIP.pdf"]
    assert body["selected_value_usd"] == pytest.approx(6_250_000)
