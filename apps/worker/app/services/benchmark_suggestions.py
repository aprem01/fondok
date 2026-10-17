"""FON-109 — P&L benchmark ratios as analyst-approved SUGGESTIONS.

Sam's decision (2026-10-15): "implement the change before this re-test so
CBRE/HotStats benchmarks are suggestions that require analyst approval rather
than automatically flowing into the model."

Before this, ``engine_runner._load_engine_inputs`` read the newest
``PNL_BENCHMARK`` extraction on every run and applied ``rooms_dept_pct``,
``fb_dept_pct`` (1 − ``fb_dept_margin``), ``mgmt_fee_pct`` and
``ffe_reserve_pct`` straight into engine input. It no longer does. This module
computes, per assumption, what the benchmark WOULD set — with the line, the
peer-set size and the document + page it was read from — so the analyst can
apply it as an ordinary ``field_overrides`` entry with a justification note
(``GET /deals/{id}/benchmark-suggestions``). Nothing here writes an override or
feeds an engine.

The suggested ratio is the SAME number the Future P&L's Benchmark column
prints for that line (``pnl_benchmark_map.map_benchmark_to_categories`` — line
total ÷ revenue total, else PAR ÷ PAR, else the printed ratio, else the legacy
summary alias). Management fee falls back to the legacy
``pnl_benchmark.mgmt_fee_pct`` alias; FF&E reserve has no per-line mapping and
reads only ``pnl_benchmark.ffe_reserve_pct``. A value outside the plausibility
window the old loader enforced (a departmental ratio in (0, 1); management fee
and FF&E reserve in (0, 10%)) is not suggested.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .pnl_benchmark_map import BENCHMARK_COLUMN, CATEGORY_SPECS, map_benchmark_to_categories


@dataclass(frozen=True)
class _SuggestionSpec:
    #: The ``field_overrides`` key an applied suggestion writes.
    key: str
    label: str
    #: ``pnl_benchmark.categories[].key`` the ratio comes from (None = legacy only).
    category: str | None
    #: Legacy summary alias read when the category has no ratio.
    legacy_field: str | None
    #: Exclusive upper bound of the plausibility window (lower bound is 0).
    upper: float
    #: ``t12_expense_actuals`` line that, when stated, wins for Year 1.
    t12_line: str


SUGGESTION_SPECS: tuple[_SuggestionSpec, ...] = (
    _SuggestionSpec("rooms_dept_pct", "Rooms departmental expense (% of rooms revenue)",
                    "rooms", None, 1.0, "rooms_dept_expense"),
    _SuggestionSpec("fb_dept_pct", "F&B departmental expense (% of F&B revenue)",
                    "food_beverage", None, 1.0, "fb_dept_expense"),
    _SuggestionSpec("mgmt_fee_pct", "Management fee (% of total revenue)",
                    "mgmt_fee", "pnl_benchmark.mgmt_fee_pct", 0.10, "mgmt_fee"),
    _SuggestionSpec("ffe_reserve_pct", "FF&E reserve (% of total revenue)",
                    None, "pnl_benchmark.ffe_reserve_pct", 0.10, "ffe_reserve"),
)

SUGGESTION_KEYS: frozenset[str] = frozenset(s.key for s in SUGGESTION_SPECS)

_LEGACY_BY_CATEGORY: dict[str, str | None] = {c.key: c.legacy_field for c in CATEGORY_SPECS}
_LINE_LABEL_BY_CATEGORY: dict[str, str] = {c.key: c.label for c in CATEGORY_SPECS}


@dataclass(frozen=True)
class _FieldMeta:
    document_id: str | None
    extraction_result_id: str | None
    filename: str | None
    page: int | None


def _page(raw: dict[str, Any]) -> int | None:
    for k in ("source_page", "page_number", "page"):
        v = raw.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


async def load_benchmark_rows(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> list[dict[str, Any]]:
    """Every PNL_BENCHMARK extraction on the deal, newest first (tenant-scoped)."""
    rows = await session.execute(
        text(
            # tenant-scope predicate required by tenant_middleware
            """
            SELECT er.id AS extraction_result_id,
                   er.document_id,
                   er.fields,
                   d.filename
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.tenant_id = :tenant
               AND UPPER(COALESCE(d.doc_type, '')) = 'PNL_BENCHMARK'
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": deal_id, "tenant": tenant_id},
    )
    return [dict(r._mapping) for r in rows.fetchall()]


def _flatten(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, _FieldMeta]]:
    """Newest extraction wins per field — the same merge ``/market-data`` uses
    (``api.documents._bucket_pnl``), so the suggestion and the Benchmark column
    read one set of numbers. Each field keeps the document + page it came from."""
    flat: dict[str, Any] = {}
    meta: dict[str, _FieldMeta] = {}
    for row in rows:
        blob = row.get("fields")
        if isinstance(blob, str):
            try:
                blob = json.loads(blob) if blob else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(blob, list):
            continue
        filename = row.get("filename")
        doc_id = row.get("document_id")
        er_id = row.get("extraction_result_id")
        for f in blob:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            if not name or name in flat:
                continue
            flat[name] = f.get("value")
            meta[name] = _FieldMeta(
                document_id=str(doc_id) if doc_id is not None else None,
                extraction_result_id=str(er_id) if er_id is not None else None,
                filename=filename.strip() if isinstance(filename, str) and filename.strip() else None,
                page=_page(f),
            )
    return flat, meta


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("%", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _primary_field(category: dict[str, Any]) -> str | None:
    """The extracted field a mapped category's ratio was read from."""
    line = category.get("benchmark_line")
    src = category.get("ratio_source")
    prefix = f"pnl_benchmark.{BENCHMARK_COLUMN}.{line}"
    if src == "computed_from_totals":
        return f"{prefix}.total_usd"
    if src == "computed_from_par":
        return f"{prefix}.par_usd"
    if src == "reported":
        return f"{prefix}.ratio_pct"
    if src == "legacy_summary":
        return _LEGACY_BY_CATEGORY.get(category.get("key") or "")
    return None


def format_pct(fraction: float) -> str:
    """0.246 → "24.6%", 0.0325 → "3.25%" (at most two decimals, trailing zeros dropped)."""
    s = f"{fraction * 100:.2f}".rstrip("0").rstrip(".")
    return f"{s}%"


def suggested_note(*, line_label: str, value: float, filename: str | None, page: int | None) -> str:
    """The prefilled justification ("Applied CBRE benchmark <line> <value> from
    <doc> p.<n>"). The analyst can edit it before saving."""
    doc = filename or "the uploaded P&L benchmark report"
    where = f" p.{page}" if page is not None else " (page not stated)"
    return f"Applied CBRE benchmark {line_label} {format_pct(value)} from {doc}{where}"


def build_suggestions(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int | None]:
    """Per-assumption suggestions from the deal's benchmark extractions.

    Returns ``(suggestions, peer_set_size)``. Each suggestion::

        {key, label, suggested_value, benchmark_line, benchmark_line_label,
         ratio_source, field_name, document_id, extraction_result_id,
         filename, page, suggested_note}

    An assumption the report does not price (or prices implausibly) is
    omitted — never filled.
    """
    flat, meta = _flatten(rows)
    if not flat:
        return [], None
    peer = _num(flat.get("pnl_benchmark.peer_set_size"))
    peer_set_size = int(peer) if peer is not None and peer > 0 else None
    categories = {c["key"]: c for c in map_benchmark_to_categories(flat)}

    out: list[dict[str, Any]] = []
    for spec in SUGGESTION_SPECS:
        value: float | None = None
        line: str | None = None
        line_label: str | None = None
        ratio_source: str | None = None
        field_name: str | None = None
        cat = categories.get(spec.category) if spec.category else None
        if cat is not None and isinstance(cat.get("ratio"), (int, float)):
            value = float(cat["ratio"])
            line = cat.get("benchmark_line")
            line_label = cat.get("label")
            ratio_source = cat.get("ratio_source")
            field_name = _primary_field(cat)
        elif spec.legacy_field is not None:
            legacy = _num(flat.get(spec.legacy_field))
            if legacy is not None:
                # The old loader's normalization: a percent emitted as 0..100.
                value = legacy / 100.0 if legacy > 1.0 else legacy
                line = spec.legacy_field.split(".", 1)[1]
                line_label = (
                    _LINE_LABEL_BY_CATEGORY.get(spec.category or "")
                    or ("FF&E Reserve" if spec.key == "ffe_reserve_pct" else spec.label)
                )
                ratio_source = "legacy_summary"
                field_name = spec.legacy_field
        if value is None or not (0.0 < value < spec.upper):
            continue
        m = meta.get(field_name or "")
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "suggested_value": value,
                "benchmark_line": line,
                "benchmark_line_label": line_label,
                "ratio_source": ratio_source,
                "field_name": field_name,
                "document_id": m.document_id if m else None,
                "extraction_result_id": m.extraction_result_id if m else None,
                "filename": m.filename if m else None,
                "page": m.page if m else None,
                "t12_line": spec.t12_line,
                "suggested_note": suggested_note(
                    line_label=line_label or spec.label,
                    value=value,
                    filename=m.filename if m else None,
                    page=m.page if m else None,
                ),
            }
        )
    return out, peer_set_size


__all__ = [
    "SUGGESTION_KEYS",
    "SUGGESTION_SPECS",
    "build_suggestions",
    "format_pct",
    "load_benchmark_rows",
    "suggested_note",
]
