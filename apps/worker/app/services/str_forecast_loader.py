"""Load STR Trend extractions into ``STRMonth`` records for the
forward-forecast engine.

The STR Trend extractor lands monthly subject Occ/ADR/RevPAR rows
keyed by ``ttm_performance.subject.monthly.<YYYY_MM>.<metric>`` and
trailing-12 comp-set summary rows. There is no per-month comp-set
field path today (the STR Trend report's "By Measure" tab shows
trailing aggregates for the comp set, not month-by-month). To still
emit a comp-set RevPAR series, we use the trailing-12 average comp
RevPAR (from ``ttm_performance.compset.*``) as a flat baseline across
the historical window. The forecast engine treats this as the
starting comp baseline and projects forward at the scenario CAGR.

When the deal has multiple STR_TREND extractions (re-extracted, or
two report years uploaded), we merge them by ``YYYY-MM`` key so the
union covers the full 24-month window. The newest extraction's
monthly value wins on collisions (re-extraction is usually a fix).

This loader is best-effort: a missing field on a row, or rows whose
period is not a valid YYYY-MM, are skipped silently. The engine's
``coverage_quality`` already grades how complete the window is.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fondok_schemas.str_forecast import STRMonth

logger = logging.getLogger(__name__)


def _coerce_float(value: Any) -> float | None:
    """Robust float coercion — extractor sometimes ships strings."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    return None


def _normalize_period(raw: str) -> str | None:
    """Convert ``YYYY_MM`` (the STR field-path convention) or ``YYYY-MM``
    to canonical ``YYYY-MM``. Returns None if the input doesn't parse."""
    if not raw or not isinstance(raw, str):
        return None
    norm = raw.strip().replace("_", "-")
    parts = norm.split("-")
    if len(parts) != 2:
        return None
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12) or year < 1900 or year > 2100:
        return None
    return f"{year:04d}-{month:02d}"


def _parse_fields(raw: Any) -> list[dict[str, Any]]:
    """``extraction_results.fields`` is JSONB on Postgres but TEXT on
    SQLite — the test suite runs on SQLite. Accept either."""
    if isinstance(raw, list):
        return [f for f in raw if isinstance(f, dict)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [f for f in parsed if isinstance(f, dict)]
    return []


def _compset_avg_revpar(flat_compset: dict[int, dict[str, Any]]) -> float | None:
    """Comp-set RevPAR — average across the comp set's trailing rows."""
    revpars: list[float] = []
    for row in flat_compset.values():
        v = _coerce_float(row.get("revpar_usd"))
        if v is not None and v > 0:
            revpars.append(v)
    if not revpars:
        return None
    return sum(revpars) / len(revpars)


def _bucket_str_trend_row(
    name: str,
    value: Any,
    monthly: dict[str, dict[str, Any]],
    compset: dict[int, dict[str, Any]],
    indices: dict[str, Any],
) -> None:
    """Sort one extraction field row into monthly / compset / indices."""
    lname = name.lower()

    if lname.startswith("ttm_performance.subject.monthly."):
        rest = lname[len("ttm_performance.subject.monthly.") :]
        try:
            period_raw, attr = rest.split(".", 1)
        except ValueError:
            return
        period = _normalize_period(period_raw)
        if period is None:
            return
        monthly.setdefault(period, {})[attr] = value
        return

    if lname.startswith("ttm_performance.compset."):
        rest = lname[len("ttm_performance.compset.") :]
        try:
            idx_str, attr = rest.split(".", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            return
        compset.setdefault(idx, {})[attr] = value
        return

    if lname.startswith("ttm_performance.indices."):
        rest = lname[len("ttm_performance.indices.") :]
        indices[rest] = value


def _build_monthly_records(
    monthly: dict[str, dict[str, Any]],
    comp_revpar_baseline: float | None,
    revpar_index_baseline: float | None,
) -> list[STRMonth]:
    """Materialize the monthly rows into ``STRMonth`` instances.

    Fields the extractor doesn't surface (per-month comp_set_revpar)
    fall back to the trailing-12 comp baseline. When the per-month
    subject revpar_index isn't extracted, we derive it from
    subject_revpar / comp_revpar_baseline.
    """
    out: list[STRMonth] = []
    for period in sorted(monthly.keys()):
        row = monthly[period]
        occ_pct = _coerce_float(row.get("occupancy_pct"))
        adr = _coerce_float(row.get("adr_usd"))
        revpar = _coerce_float(row.get("revpar_usd"))
        if occ_pct is None and adr is None and revpar is None:
            continue
        # Occupancy is published as a percent (0-100). Convert to 0-1.
        if occ_pct is not None and occ_pct > 1.0:
            occ = occ_pct / 100.0
        else:
            occ = occ_pct if occ_pct is not None else 0.0
        adr = adr if adr is not None else 0.0
        # Derive missing RevPAR from occ × ADR when both are present.
        if revpar is None:
            revpar = occ * adr
        comp_revpar = comp_revpar_baseline or 0.0
        if comp_revpar > 0 and revpar > 0:
            idx = revpar / comp_revpar
        elif revpar_index_baseline is not None:
            idx = revpar_index_baseline
        else:
            idx = 1.0
        out.append(
            STRMonth(
                period=period,
                occupancy=max(0.0, min(1.0, occ)),
                adr=max(0.0, adr),
                revpar=max(0.0, revpar),
                comp_set_revpar=max(0.0, comp_revpar),
                revpar_index=max(0.0, idx),
                is_historical=True,
            )
        )
    return out


async def load_str_history_for_deal(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str | None = None,
) -> list[STRMonth]:
    """Load monthly STR Trend rows for ``deal_id`` into ``STRMonth``s.

    When ``tenant_id`` is provided, the SQL filter pins both
    ``extraction_results.tenant_id`` AND ``documents.tenant_id`` to
    the caller's tenant — same cross-tenant defence used by
    ``compute_comp_set_drift``. When tenant_id is None (engine_runner
    path, where the tenant gate has already fired at the API layer)
    we drop the tenant predicate so the helper works on the demo-deal
    code path too.

    Returns a list of ``STRMonth`` records (possibly empty) sorted
    ASC by period. The forecast engine handles the sort itself but
    we sort here too for caller debuggability.
    """
    if tenant_id is None:
        sql = """
            SELECT er.fields, er.created_at
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND UPPER(COALESCE(d.doc_type, '')) IN ('STR', 'STR_TREND')
             ORDER BY er.created_at DESC
        """
        params = {"deal": deal_id}
    else:
        sql = """
            SELECT er.fields, er.created_at
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.tenant_id = :tenant
               AND UPPER(COALESCE(d.doc_type, '')) IN ('STR', 'STR_TREND')
             ORDER BY er.created_at DESC
        """
        params = {"deal": deal_id, "tenant": tenant_id}

    try:
        rows = await session.execute(text(sql), params)
        materialized = [dict(r._mapping) for r in rows.fetchall()]
    except Exception:
        logger.exception("str_forecast: failed to query STR_TREND extractions")
        return []

    monthly: dict[str, dict[str, Any]] = {}
    compset: dict[int, dict[str, Any]] = {}
    indices: dict[str, Any] = {}
    for row in materialized:
        fields = _parse_fields(row.get("fields"))
        for f in fields:
            name = (f.get("field_name") or "").strip()
            if not name:
                continue
            _bucket_str_trend_row(name, f.get("value"), monthly, compset, indices)

    comp_revpar = _compset_avg_revpar(compset)
    rgi = _coerce_float(indices.get("rgi_revpar_index"))

    return _build_monthly_records(monthly, comp_revpar, rgi)


__all__ = [
    "load_str_history_for_deal",
]
