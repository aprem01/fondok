"""Build a ``DealDossier`` from the persisted DB state.

Pure composition over existing tables (deals, documents,
extraction_results, engine_outputs, /analysis/{id}/variance). No
side effects, no LLM calls. Caller decides whether to cache.

The builder is intentionally generous in what it includes: every
extracted field, every engine output, every variance flag — but
each one carries its source so a downstream consumer can trim
without losing provenance.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .schema import (
    DealDossier,
    DossierCitation,
    DossierConfidenceRollup,
    DossierDocument,
    DossierEngine,
    DossierField,
    DossierVarianceFlag,
)

logger = logging.getLogger(__name__)


_PER_PAGE_EXCERPT_CAP = 1500  # chars per page text snapshot


async def build_dossier(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    include_page_excerpts: bool = True,
) -> DealDossier:
    """Compose the deal's full Context Data Product from persisted state.

    ``include_page_excerpts=False`` skips the per-page text snapshots
    on each document, which trims the dossier substantially when used
    as an LLM input where the agent only needs structured fields, not
    raw narrative text.
    """
    deal_row = await _fetch_deal_row(session, deal_id=deal_id)
    deal_data = _coerce_deal_row(deal_row) if deal_row is not None else {}
    if "id" not in deal_data:
        deal_data["id"] = deal_id

    doc_rows = await _fetch_document_rows(session, deal_id=deal_id)
    extraction_rows = await _fetch_extraction_rows(session, deal_id=deal_id)
    engine_rows = await _fetch_engine_rows(session, deal_id=deal_id)

    # Build a doc_id → DocumentRow lookup so we can join extraction
    # rows back to filename / doc_type / page_count for citations.
    docs_by_id: dict[str, dict[str, Any]] = {
        str(r["id"]): r for r in doc_rows
    }

    documents: list[DossierDocument] = []
    extracted_fields: list[DossierField] = []
    extracted_count = 0
    confidence_sum = 0.0
    confidence_n = 0

    for d in doc_rows:
        doc_id = str(d["id"])
        # Pull the latest extraction for this doc.
        ext = next(
            (e for e in extraction_rows if str(e["document_id"]) == doc_id),
            None,
        )
        fields_blob = ext["fields"] if ext else None
        if isinstance(fields_blob, str):
            try:
                fields_blob = json.loads(fields_blob)
            except json.JSONDecodeError:
                fields_blob = None
        cr_blob = ext["confidence_report"] if ext else None
        if isinstance(cr_blob, str):
            try:
                cr_blob = json.loads(cr_blob) if cr_blob else None
            except json.JSONDecodeError:
                cr_blob = None
        overall_conf = (
            float(cr_blob["overall"])
            if isinstance(cr_blob, dict) and "overall" in cr_blob
            else None
        )
        n_fields = len(fields_blob) if isinstance(fields_blob, list) else 0
        if n_fields > 0:
            extracted_count += 1

        excerpts: dict[int, str] = {}
        if include_page_excerpts:
            ed = d.get("extraction_data")
            if isinstance(ed, str):
                try:
                    ed = json.loads(ed) if ed else None
                except json.JSONDecodeError:
                    ed = None
            for page in (ed or {}).get("pages") or []:
                try:
                    page_num = int(page.get("page_num", 0))
                except (TypeError, ValueError):
                    continue
                if page_num < 1:
                    continue
                txt = (page.get("text") or "").strip()
                if not txt:
                    continue
                if len(txt) > _PER_PAGE_EXCERPT_CAP:
                    txt = txt[:_PER_PAGE_EXCERPT_CAP] + "…[truncated]"
                excerpts[page_num] = txt

        documents.append(
            DossierDocument(
                document_id=doc_id,
                filename=d.get("filename") or "unknown.pdf",
                doc_type=d.get("doc_type"),
                status=d.get("status") or "UNKNOWN",
                page_count=_coerce_optional_int(d.get("page_count")),
                parser=d.get("parser"),
                field_count=n_fields,
                overall_confidence=overall_conf,
                excerpts_by_page=excerpts,
            )
        )

        if isinstance(fields_blob, list):
            for f in fields_blob:
                if not isinstance(f, dict):
                    continue
                name = (f.get("field_name") or "").strip()
                if not name:
                    continue
                conf = f.get("confidence")
                conf_f = (
                    float(conf)
                    if isinstance(conf, (int, float)) and 0 <= conf <= 1
                    else None
                )
                if conf_f is not None:
                    confidence_sum += conf_f
                    confidence_n += 1
                page_raw = f.get("source_page")
                page = _coerce_optional_int(page_raw)
                excerpt = f.get("raw_text")
                if isinstance(excerpt, str) and len(excerpt) > 500:
                    excerpt = excerpt[:500] + "…[truncated]"
                extracted_fields.append(
                    DossierField(
                        name=name,
                        value=f.get("value"),
                        unit=f.get("unit"),
                        confidence=conf_f,
                        source="extraction",
                        citations=[
                            DossierCitation(
                                document_id=doc_id,
                                document_type=d.get("doc_type"),
                                filename=d.get("filename"),
                                page=page if page and page >= 1 else None,
                                field=name,
                                excerpt=excerpt,
                            )
                        ],
                    )
                )

    # Engine outputs — flatten into DossierEngine entries, latest per name.
    seen_engines: set[str] = set()
    engines: list[DossierEngine] = []
    for r in engine_rows:
        name = str(r.get("engine_name") or "").strip()
        if not name or name in seen_engines:
            continue
        seen_engines.add(name)
        outputs_blob = r.get("outputs")
        if isinstance(outputs_blob, str):
            try:
                outputs_blob = json.loads(outputs_blob) if outputs_blob else {}
            except json.JSONDecodeError:
                outputs_blob = {}
        if not isinstance(outputs_blob, dict):
            outputs_blob = {}
        engines.append(
            DossierEngine(
                name=name,
                status=str(r.get("status") or "unknown"),
                summary=str(r.get("summary") or ""),
                outputs=outputs_blob,
                runtime_ms=_coerce_optional_int(r.get("runtime_ms")),
                completed_at=_coerce_optional_datetime(r.get("completed_at")),
            )
        )

    # Variance — reuse the existing analysis variance helper so the
    # severity counts + flag list match what the /analysis endpoint
    # (and the web Variance tab) returns.
    variance, variance_counts = await _build_variance(
        session, deal_id=deal_id, tenant_id=tenant_id, docs_by_id=docs_by_id
    )

    # Spread actuals / broker — reuse _load_critic_inputs so the
    # dossier matches what the Critic + memo Analyst already see.
    spread_actuals, spread_broker = await _build_spreads(
        session, deal_id=deal_id
    )

    has_t12 = any(
        (d.doc_type or "").upper() in {"T12", "PNL"}
        for d in documents
    )
    has_om = any((d.doc_type or "").upper() == "OM" for d in documents)

    rollup = DossierConfidenceRollup(
        avg_field_confidence=(confidence_sum / confidence_n) if confidence_n else 0.0,
        extracted_field_count=len(extracted_fields),
        docs_extracted=extracted_count,
        docs_total=len(documents),
        variance_critical_count=variance_counts.get("critical", 0),
        variance_warn_count=variance_counts.get("warn", 0),
        variance_info_count=variance_counts.get("info", 0),
        has_t12_actuals=has_t12,
        has_om=has_om,
    )

    return DealDossier(
        deal_id=deal_id,
        tenant_id=tenant_id,
        deal=deal_data,
        documents=documents,
        spread_actuals=spread_actuals,
        spread_broker=spread_broker,
        extracted_fields=extracted_fields,
        engines=engines,
        variance=variance,
        confidence=rollup,
        composed_at=datetime.now(UTC),
    )


# ───────────────────────────── helpers ─────────────────────────────


async def _fetch_deal_row(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any] | None:
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return None
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, city, keys, brand, service, status,
                           deal_stage, return_profile, positioning,
                           purchase_price, ai_confidence, created_at,
                           updated_at
                      FROM deals
                     WHERE id = :id
                    """
                ),
                {"id": deal_id},
            )
        ).first()
    except Exception:
        return None
    return dict(row._mapping) if row is not None else None


def _coerce_deal_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": str(row.get("id"))}
    for k in (
        "name",
        "city",
        "brand",
        "service",
        "status",
        "deal_stage",
        "return_profile",
        "positioning",
    ):
        v = row.get(k)
        if v is not None:
            out[k] = v
    if row.get("keys") is not None:
        try:
            out["keys"] = int(row["keys"])
        except (TypeError, ValueError):
            pass
    if row.get("purchase_price") is not None:
        try:
            out["purchase_price"] = float(row["purchase_price"])
        except (TypeError, ValueError):
            pass
    if row.get("ai_confidence") is not None:
        try:
            out["ai_confidence"] = float(row["ai_confidence"])
        except (TypeError, ValueError):
            pass
    if "city" in out:
        out["location"] = out["city"]
    return out


async def _fetch_document_rows(
    session: AsyncSession, *, deal_id: str
) -> list[dict[str, Any]]:
    try:
        rows = await session.execute(
            text(
                """
                SELECT id, filename, doc_type, status, page_count, parser,
                       extraction_data
                  FROM documents
                 WHERE deal_id = :deal
                 ORDER BY uploaded_at ASC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return []
    return [dict(r._mapping) for r in rows.fetchall()]


async def _fetch_extraction_rows(
    session: AsyncSession, *, deal_id: str
) -> list[dict[str, Any]]:
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.document_id, er.fields, er.confidence_report,
                       er.created_at
                  FROM extraction_results er
                 WHERE er.deal_id = :deal
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return []
    return [dict(r._mapping) for r in rows.fetchall()]


async def _fetch_engine_rows(
    session: AsyncSession, *, deal_id: str
) -> list[dict[str, Any]]:
    """Latest row per engine (engine_name)."""
    from ..services.engine_runner import _coerce_uuid, get_latest_outputs

    try:
        latest = await get_latest_outputs(session, deal_id=deal_id)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for name, envelope in latest.items():
        envelope_copy = dict(envelope)
        envelope_copy["engine_name"] = name
        out.append(envelope_copy)
    _ = _coerce_uuid  # imported for parity; not directly needed here
    return out


async def _build_variance(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    docs_by_id: dict[str, dict[str, Any]],
) -> tuple[list[DossierVarianceFlag], dict[str, int]]:
    """Reuse the deterministic variance pass from the analysis API.

    Falls back to an empty list when broker and / or T-12 actuals
    aren't both present — same contract the live /variance endpoint
    surfaces.
    """
    counts = {"critical": 0, "warn": 0, "info": 0}
    try:
        from ..api.documents import _load_critic_inputs
        from ..agents.variance import (
            VarianceBrokerField,
            _build_flags,
            _to_uuid as _variance_to_uuid,
        )

        broker, actuals, _market, _keys = await _load_critic_inputs(
            session, deal_id=deal_id
        )
        if broker is None or actuals is None:
            return [], counts

        broker_fields: list[VarianceBrokerField] = []
        for field_name, value in (
            ("noi", broker.noi),
            ("rooms_revenue", broker.rooms_revenue),
            ("fb_revenue", broker.fb_revenue),
            ("total_revenue", broker.total_revenue),
            ("departmental_expenses", broker.dept_expenses.total),
            ("undistributed_expenses", broker.undistributed.total),
            ("gop", broker.gop),
            ("mgmt_fee", broker.mgmt_fee),
            ("ffe_reserve", broker.ffe_reserve),
            ("fixed_charges", broker.fixed_charges.total),
            ("insurance", broker.fixed_charges.insurance),
            ("occupancy", broker.occupancy),
            ("adr", broker.adr),
            ("revpar", broker.revpar),
        ):
            if value is None:
                continue
            try:
                broker_fields.append(
                    VarianceBrokerField(field=field_name, value=float(value))
                )
            except Exception:  # noqa: BLE001
                continue

        deal_uuid = _variance_to_uuid(deal_id)
        flags = _build_flags(
            deal_uuid=deal_uuid,
            actuals=actuals,
            broker_fields=broker_fields,
        )
        out: list[DossierVarianceFlag] = []
        for f in flags:
            sev = f.severity.value
            if sev == "Critical":
                counts["critical"] += 1
            elif sev == "Warn":
                counts["warn"] += 1
            else:
                counts["info"] += 1
            out.append(
                DossierVarianceFlag(
                    field=f.field,
                    rule_id=f.rule_id,
                    severity=sev,
                    actual=f.actual,
                    broker=f.broker,
                    delta=f.delta,
                    delta_pct=f.delta_pct,
                    note=f.note,
                    citations=[],
                )
            )
        _ = (tenant_id, docs_by_id)
        return out, counts
    except Exception as exc:  # noqa: BLE001
        logger.warning("dossier: variance build failed for deal=%s: %s", deal_id, exc)
        return [], counts


async def _build_spreads(
    session: AsyncSession, *, deal_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Project the broker + T-12 ``USALIFinancials`` snapshots into
    plain dicts so the dossier stays self-describing."""
    try:
        from ..api.documents import _load_critic_inputs

        broker, actuals, _m, _k = await _load_critic_inputs(
            session, deal_id=deal_id
        )

        def _serialize(spread: Any) -> dict[str, Any] | None:
            if spread is None:
                return None
            if hasattr(spread, "model_dump"):
                return spread.model_dump(mode="json")
            return None

        return _serialize(actuals), _serialize(broker)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dossier: spread build failed for deal=%s: %s", deal_id, exc)
        return None, None


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
