"""Sibling cell-mapping reuse never pre-empts a deterministic template.

2026-10-07: two STR monthly trend workbooks were served by ``template:sibling``
(placeholder hotel names, no TTM block) instead of ``template:str_trend``.
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Force SQLite + a temp DB before app imports (same pattern as the other
# DB-backed modules — the first module to pin wins; they share one engine).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-sibling-gate.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

from app.api import documents as docs_module  # noqa: E402


@pytest.mark.parametrize(
    "dt", ["STR", "STR_TREND", "STR_SEGMENTATION", "CBRE_HORIZONS", "PARTNERSHIP", " str_trend "]
)
def test_template_handled_types_are_never_sibling_candidates(dt: str) -> None:
    assert docs_module._sibling_reuse_allowed(dt) is False


@pytest.mark.parametrize("dt", ["T12", "PNL", "PNL_MONTHLY", "PNL_YTD", "OM", "CAPEX", None, ""])
def test_other_types_may_use_sibling_reuse(dt: str | None) -> None:
    assert docs_module._sibling_reuse_allowed(dt) is True


def test_gate_covers_every_type_the_template_dispatcher_handles() -> None:
    """The dispatcher and the gate must not drift apart."""
    from app.extraction import template_extractors as te

    src = inspect.getsource(te.try_template_extract)
    dispatched = {
        tok
        for tok in ("STR", "STR_TREND", "CBRE_HORIZONS", "PARTNERSHIP", "STR_SEGMENTATION")
        if f'"{tok}"' in src
    }
    assert dispatched, "dispatcher parse found no doc types"
    missing = dispatched - docs_module._TEMPLATE_HANDLED_DOC_TYPES
    assert not missing, f"template-handled types missing from the sibling gate: {missing}"


def test_pipeline_gates_attempt_hit_and_learning() -> None:
    """All three gate sites exist in the pipeline body: the attempt is gated
    on the analyst's tag, a hit is rejected by its source type, and learning
    is gated on the classified type."""
    src = inspect.getsource(docs_module._run_extraction_pipeline_inner)
    flat = src.replace("\n", "").replace(" ", "")
    assert "SIBLING_TEMPLATE_REUSE_ENABLEDand_sibling_reuse_allowed(user_provided_doc_type)" in flat
    assert "sibling reuse REJECTED" in src
    assert "fieldsand_sibling_reuse_allowed(classified_doc_type)" in flat


# ─────────────────────────── real dispatch (FON-88 regression) ───────────────────────────
#
# The three tests above pin the gate's shape. This one runs the REAL
# extraction dispatch (``_run_extraction_pipeline_inner`` → sibling lookup →
# ``_run_graph_extraction`` → pre-router structural override → template
# extractor) on the STR trend fixture with a learned sibling mapping present
# for its fingerprint — the exact 2026-10-07 situation — and asserts the
# persisted ``agent_version`` is the template's, not ``template:sibling``.
# No LLM: the structural override pins STR_TREND before the Router runs,
# and the Router is stubbed to raise in case it is ever reached.


_FIXTURES = Path(__file__).parent / "fixtures"
_STR_TREND_FIXTURE = _FIXTURES / "sample_str_trend.xls"


def _pages_payload(parsed) -> list[dict]:
    """Mirror of the page serialisation ``_run_parse_and_extract`` persists."""
    return [
        {
            "page_num": p.page_num,
            "text": p.text,
            "tables": p.tables,
            "metadata": p.metadata,
        }
        for p in parsed.pages
    ]


async def _seed_str_doc(
    session,
    *,
    tenant_id: str,
    deal_id: str,
    pages: list[dict],
    parser: str,
    fingerprint: str,
    content_hash: str,
    user_provided_doc_type: str | None,
) -> str:
    from sqlalchemy import text

    doc_id = str(uuid4())
    extraction_data = {
        "parser": parser,
        "total_pages": len(pages),
        "content_hash": content_hash,
        "pages": pages,
    }
    await session.execute(
        text(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
            "status, uploaded_at, content_hash, storage_key, parser, "
            "template_fingerprint, user_provided_doc_type, extraction_data) "
            "VALUES (:id, :deal, :tenant, :fn, 'STR_TREND', 'UPLOADED', :ts, :ch, "
            ":sk, :parser, :tfp, :updt, :data)"
        ),
        {
            "id": doc_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "fn": _STR_TREND_FIXTURE.name,
            "ts": "2026-07-01 00:00:00",
            "ch": content_hash,
            "sk": f"raw/{content_hash}",
            "parser": parser,
            "tfp": fingerprint,
            "updt": user_provided_doc_type,
            "data": json.dumps(extraction_data),
        },
    )
    await session.commit()
    return doc_id


@pytest.fixture
async def str_db_env():
    """Migrated sqlite DB + a deal + the parsed STR trend fixture."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_session_factory
    from app.extraction.parser import parse_document
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    tenant_id = str(get_settings().DEFAULT_TENANT_ID)
    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, "
                "updated_at) VALUES (:id, :tenant, 'STR Gate Hotel', 'Draft', :ts, :ts)"
            ),
            {"id": deal_id, "tenant": tenant_id, "ts": "2026-07-01 00:00:00"},
        )
        await session.commit()
    parsed = await parse_document(
        file_bytes=_STR_TREND_FIXTURE.read_bytes(), filename=_STR_TREND_FIXTURE.name
    )
    yield factory, tenant_id, deal_id, parsed


@pytest.mark.real_extraction_backend
@pytest.mark.parametrize("user_provided_doc_type", [None, "STR_TREND"])
async def test_real_dispatch_prefers_str_trend_template_over_learned_sibling(
    str_db_env, monkeypatch, user_provided_doc_type: str | None
) -> None:
    """With a sibling mapping learned from an STR_TREND workbook of the same
    fingerprint (the 2026-10-07 state), the real dispatch must persist
    ``agent_version`` starting ``template:str_trend`` — for an untagged
    drop (attempt happens, HIT is rejected by its source type) and for an
    analyst-tagged one (attempt is skipped)."""
    from sqlalchemy import text

    import app.agents.router as router_mod
    import app.api.documents as docs_api
    from app.extraction.template_extractors import try_template_extract
    from app.services.sibling_template import (
        compute_template_fingerprint,
        maybe_learn_mapping,
    )

    factory, tenant_id, deal_id, parsed = str_db_env

    async def _router_must_not_run(*args, **kwargs):
        raise AssertionError("Router LLM must not be reached for an STR trend workbook")

    monkeypatch.setattr(router_mod, "run_router", _router_must_not_run)

    pages = _pages_payload(parsed)
    fp = compute_template_fingerprint(parsed.pages, parser=parsed.parser)
    assert fp, "the STR trend fixture must fingerprint as a workbook"
    template_fields = try_template_extract(parsed, "STR_TREND").fields
    assert len(template_fields) >= 10

    async with factory() as session:
        # The learned mapping: a prior STR_TREND workbook, same template,
        # learned before the gate existed.
        source_doc = await _seed_str_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            pages=pages,
            parser=parsed.parser,
            fingerprint=fp,
            content_hash=uuid4().hex * 2,
            user_provided_doc_type="STR_TREND",
        )
        await maybe_learn_mapping(
            session,
            tenant_id=tenant_id,
            doc_id=source_doc,
            doc_type="STR_TREND",
            fields=template_fields,
            extraction_data={"pages": pages},
        )
        mapping_rows = (
            await session.execute(
                text(
                    "SELECT source_doc_id FROM template_mappings "
                    "WHERE tenant_id = :t AND fingerprint = :fp"
                ),
                {"t": tenant_id, "fp": fp},
            )
        ).fetchall()
        # (one mapping per tenant+fingerprint — the second parametrization
        # finds the first one's row, learned from an STR_TREND source too)
        assert mapping_rows, "precondition: a sibling mapping must exist for the fingerprint"
        target_doc = await _seed_str_doc(
            session,
            tenant_id=tenant_id,
            deal_id=deal_id,
            pages=pages,
            parser=parsed.parser,
            fingerprint=fp,
            content_hash=uuid4().hex * 2,
            user_provided_doc_type=user_provided_doc_type,
        )

    await docs_api._run_extraction_pipeline_inner(
        deal_id=deal_id, doc_id=target_doc, tenant_id=tenant_id
    )

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT agent_version, fields FROM extraction_results "
                    "WHERE document_id = :d AND tenant_id = :t "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"d": target_doc, "t": tenant_id},
            )
        ).first()
        status_row = (
            await session.execute(
                text(
                    "SELECT status, doc_type FROM documents "
                    "WHERE id = :d AND tenant_id = :t"
                ),
                {"d": target_doc, "t": tenant_id},
            )
        ).first()
    assert row is not None, "the dispatch must persist an extraction_results row"
    agent_version = row._mapping["agent_version"]
    assert agent_version.startswith("template:str_trend"), agent_version
    assert not agent_version.startswith("template:sibling")
    assert agent_version.endswith(f";pv={docs_api.EXTRACTION_PIPELINE_VERSION}")
    fields = row._mapping["fields"]
    fields = json.loads(fields) if isinstance(fields, str) else fields
    assert len(fields) == len(template_fields)
    assert status_row._mapping["status"] == "EXTRACTED"
    assert status_row._mapping["doc_type"] == "STR_TREND"
