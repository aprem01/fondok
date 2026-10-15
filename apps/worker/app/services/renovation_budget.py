"""FON-101 — renovation-budget candidates and their precedence.

Sam's decision (2026-10-14): when a deal carries BOTH an OM and a separate
PIP / CapEx document that states a renovation budget, the PIP / CapEx
document wins — it is the more detailed source. Both values stay visible
with their sources; the analyst can override the selection; dates decide
between several PIP / CapEx documents (a newer approved capital plan
supersedes an older PIP).

CAPEX lanes (``documents.doc_subtype``, FON-41 / R-036 + the PIP lane):

* ``pip`` — "PIP / Renovation budget": the day-one PIP, owner budget,
  approved capital plan or estimate. FEEDS the model.
* ``NULL`` — untagged (bulk drop, legacy upload, a worker without the
  column): the classifier decides, so it MAY ground the model exactly like
  ``pip`` — a CAPEX file nobody laned is not silently refused.
* ``future`` — "Future CapEx": forward hold-period capital plan. Reference
  only; the loader reads nothing but ``future_capex.*`` off it.
* ``historic`` — "Historic CapEx": spend already incurred. Reference only;
  the loader reads nothing but ``historic_capex.*`` off it.

A ``future`` or ``historic`` document never grounds ``renovation_budget``
even when the extractor labelled its rows ``capex_plan.pip.*`` — the lane
the analyst filed it under wins over the LLM's namespace. Conversely a
brand PIP dropped in Future CapEx is listed as reference only WITH its
stated PIP total and a re-tag hint, never quietly ignored.

Precedence, highest first::

    analyst override  (``field_overrides.renovation_budget``)
    > the CAPEX document with the latest plan date
        (``capex_plan.document.plan_date``, else ``documents.report_as_of``,
         else ``documents.uploaded_at``), ``doc_subtype IN ('pip', NULL)``,
         carrying ``capex_plan.pip.total_usd`` > 0
    > the OM's ``broker_proforma.renovation_budget_usd``
    > the seed

This module owns the document read (``load_capex_documents``), the
selection rule (``select_pip_candidate``) and the analyst-facing candidate
list (``build_candidates``). ``services.engine_runner._load_engine_inputs``
calls the first two to ground ``renovation_budget`` and stamps the
``pip_document`` source id; ``GET /deals/{id}/renovation-budget-candidates``
calls all three so the web shows every candidate, which one the model is
on, and why.

Nothing here invents a number: a document with no extracted PIP total is
listed with ``value_usd = None`` and the reason, never a placeholder.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: The extracted path the loader reads as the PIP / CapEx document's budget.
PIP_TOTAL_PATH = "capex_plan.pip.total_usd"
#: ``documents.doc_subtype`` values — the CAPEX lanes. The contract the
#: upload / reclassify endpoints enforce is ``api.documents
#: .DOC_SUBTYPES_BY_DOC_TYPE``; these are the loader's reading of it.
#: ``pip`` is the "PIP / Renovation budget" lane (wizard slot
#: ``pip_capex``) — the only tagged lane that feeds the model.
PIP_CAPEX_SUBTYPE = "pip"
#: "Future CapEx" (FON-41 / R-036; wizard slot ``future_capex``). Reference only.
FUTURE_CAPEX_SUBTYPE = "future"
#: "Historic CapEx" (wizard slot ``capex``). Reference only.
HISTORIC_CAPEX_SUBTYPE = "historic"
#: Lanes whose documents never ground ``renovation_budget``, whatever
#: namespace the extractor put their rows under.
REFERENCE_ONLY_CAPEX_SUBTYPES: frozenset[str] = frozenset(
    {FUTURE_CAPEX_SUBTYPE, HISTORIC_CAPEX_SUBTYPE}
)
#: The one namespace the loader reads off each reference-only lane.
REFERENCE_ONLY_NAMESPACES: dict[str, str] = {
    FUTURE_CAPEX_SUBTYPE: "future_capex",
    HISTORIC_CAPEX_SUBTYPE: "historic_capex",
}

#: Source id stamped on ``renovation_budget`` when a PIP / CapEx document
#: grounds it. Registered in ``ontology/concepts.yaml`` (``sources``) and
#: mirrored to the web by ``scripts/gen_ontology.py``.
SOURCE_PIP_DOCUMENT = "pip_document"

_SCOPE_ITEM_RE = re.compile(r"^capex_plan\.pip\.scope_items\.(\d+)\.(name|usd|category|brand_required)$")
_FUTURE_ITEM_RE = re.compile(r"^future_capex\.items\.(\d+)\.(name|year|usd|category|timing)$")
_HISTORIC_ITEM_RE = re.compile(r"^historic_capex\.items\.(\d+)\.(name|year|usd|category|timing)$")
_HISTORIC_BY_YEAR_RE = re.compile(r"^historic_capex\.by_year\.(\d{4})\.total_usd$")
_TIMING_RE = re.compile(r"^capex_plan\.pip\.timing\.(\d{4})\.(pct|usd)$")

_SCALARS: dict[str, str] = {
    # extracted path → CapexDocument attribute
    "capex_plan.pip.per_key_usd": "pip_per_key_usd",
    "capex_plan.pip.hard_usd": "pip_hard_usd",
    "capex_plan.pip.soft_usd": "pip_soft_usd",
    "capex_plan.pip.contingency_usd": "pip_contingency_usd",
    "capex_plan.pip.contingency_pct": "pip_contingency_pct",
    "capex_plan.pip.completion_quarter": "pip_completion_quarter",
    "capex_plan.document.plan_date": "plan_date",
    "capex_plan.document.status": "status",
    "capex_plan.document.prepared_by": "prepared_by",
    "capex_plan.document.brand": "brand",
    "capex_plan.document.scope_keys": "scope_keys",
    "future_capex.total_usd": "future_total_usd",
    "future_capex.plan_date": "future_plan_date",
    "future_capex.prepared_by": "future_prepared_by",
    "historic_capex.total_usd": "historic_total_usd",
}

#: ``_SCALARS`` attributes that hold a number (the rest hold text).
_NUMERIC_SCALARS: frozenset[str] = frozenset(
    {
        "pip_per_key_usd", "pip_hard_usd", "pip_soft_usd",
        "pip_contingency_usd", "pip_contingency_pct", "scope_keys",
        "future_total_usd", "historic_total_usd",
    }
)

_DOC_COLUMNS_CACHE_KEY = "fondok_documents_columns_fon101"


@dataclass
class CapexDocument:
    """One CAPEX document on the deal, with what its latest extraction says."""

    document_id: str
    filename: str
    doc_subtype: str | None = None
    uploaded_at: str | None = None  # ISO date
    report_as_of: str | None = None  # ISO date (``documents.report_as_of``)
    extraction_result_id: str | None = None
    extracted: bool = False
    # ``capex_plan.pip.*``
    pip_total_usd: float | None = None
    pip_total_row: dict[str, Any] | None = None  # the raw extraction row
    pip_per_key_usd: float | None = None
    pip_hard_usd: float | None = None
    pip_soft_usd: float | None = None
    pip_contingency_usd: float | None = None
    pip_contingency_pct: float | None = None
    pip_completion_quarter: str | None = None
    timing_by_year: dict[str, dict[str, float]] = field(default_factory=dict)
    scope_items: list[dict[str, Any]] = field(default_factory=list)
    # ``capex_plan.document.*``
    plan_date: str | None = None  # as extracted (ISO date / YYYY-MM / YYYY)
    status: str | None = None
    prepared_by: str | None = None
    brand: str | None = None
    scope_keys: float | None = None
    # ``future_capex.*``
    future_items: list[dict[str, Any]] = field(default_factory=list)
    future_total_usd: float | None = None
    future_plan_date: str | None = None
    future_prepared_by: str | None = None
    # ``historic_capex.*``
    historic_items: list[dict[str, Any]] = field(default_factory=list)
    historic_total_usd: float | None = None
    historic_by_year: dict[str, float] = field(default_factory=dict)

    @property
    def lane(self) -> str | None:
        """The stored ``doc_subtype``, normalised (``None`` = untagged)."""
        s = (self.doc_subtype or "").strip().lower()
        return s or None

    @property
    def is_future(self) -> bool:
        return self.lane == FUTURE_CAPEX_SUBTYPE

    @property
    def is_historic(self) -> bool:
        return self.lane == HISTORIC_CAPEX_SUBTYPE

    @property
    def is_reference_only(self) -> bool:
        """Filed under Future or Historic CapEx — never grounds the model."""
        return self.lane in REFERENCE_ONLY_CAPEX_SUBTYPES

    @property
    def may_ground_model(self) -> bool:
        """``pip`` or untagged (classifier-assigned). An unknown subtype
        string is treated as untagged — the upload contract never stores
        one, and refusing a document over a stray tag would hide a PIP."""
        return not self.is_reference_only

    @property
    def has_pip_total(self) -> bool:
        return self.pip_total_usd is not None and self.pip_total_usd > 0

    @property
    def effective_date(self) -> tuple[str | None, str | None]:
        """``(iso_date, basis)`` — the date the precedence rule orders on.

        The document's own plan date first (the analyst's reading of
        "newer supersedes older"), then the derived report date, then the
        upload date; ``(None, None)`` when none is known.
        """
        for raw, basis in (
            (self.plan_date, "plan_date"),
            (self.report_as_of, "report_as_of"),
            (self.uploaded_at, "uploaded_at"),
        ):
            iso = _iso_date(raw)
            if iso is not None:
                return iso, basis
        return None, None

    @property
    def sort_key(self) -> tuple[str, str]:
        iso, _basis = self.effective_date
        return (iso or "", self.uploaded_at or "")


# ───────────────────────────── parsing ──────────────────────────────


def _iso_date(value: Any) -> str | None:
    """``YYYY-MM-DD`` for a date / datetime / ISO-ish string.

    ``YYYY-MM`` reads as the first of that month and ``YYYY`` as 1 January
    — the extractor emits whichever precision the document prints — so
    that two plans dated to different precisions still order. Anything
    else is ``None`` (unknown), never a guess.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1).isoformat()
        except ValueError:
            return None
    if re.match(r"^\d{4}$", s):
        return date(int(s), 1, 1).isoformat()
    return None


def coerce_usd(value: Any) -> float | None:
    """An extracted dollar amount → float. Accepts a number or a numeric
    string as the extractor sometimes emits it (``"$1,200,000"``,
    ``"1200000"``); bool / blank / prose → ``None``. The ONE coercion every
    CAPEX reader uses (``agents.critic.check_capex_plausibility`` imports
    it), so the critic and the loader can never disagree about whether a
    row carries a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


_num = coerce_usd


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _page(row: Mapping[str, Any]) -> int | None:
    """``source_page`` as a 1-based int. The extractor emits it as an int,
    but rows also arrive with ``12.0`` or ``"12"`` (the market readers'
    ``coerce_page`` tolerates the same); anything that is not a positive
    whole number is ``None``, never a guess."""
    page = row.get("source_page")
    if page is None or isinstance(page, bool):
        return None
    try:
        f = float(str(page).strip())
    except (TypeError, ValueError):
        return None
    # ``12.5`` is not a page — truncating it to 12 would be a guess.
    if not math.isfinite(f) or not f.is_integer():
        return None
    n = int(f)
    return n if n >= 1 else None


def parse_capex_fields(doc: CapexDocument, fields: Any) -> CapexDocument:
    """Fill ``doc`` from one extraction's ``fields`` list (in place)."""
    if isinstance(fields, str):
        try:
            fields = json.loads(fields) if fields else []
        except (json.JSONDecodeError, TypeError):
            fields = []
    if not isinstance(fields, list):
        return doc
    scope: dict[int, dict[str, Any]] = {}
    future: dict[int, dict[str, Any]] = {}
    historic: dict[int, dict[str, Any]] = {}
    for row in fields:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("field_name") or "").strip()
        if not name:
            continue
        value = row.get("value")
        if name == PIP_TOTAL_PATH:
            total = _num(value)
            if total is not None and doc.pip_total_usd is None:
                doc.pip_total_usd = total
                doc.pip_total_row = dict(row)
            continue
        attr = _SCALARS.get(name)
        if attr is not None:
            if getattr(doc, attr) is not None:
                continue
            if attr in _NUMERIC_SCALARS:
                setattr(doc, attr, _num(value))
            else:
                setattr(doc, attr, _text(value))
            continue
        m = _SCOPE_ITEM_RE.match(name)
        if m:
            item = scope.setdefault(int(m.group(1)), {"page": _page(row)})
            key = m.group(2)
            if key == "usd":
                item["usd"] = _num(value)
            elif key == "brand_required":
                item["brand_required"] = value if isinstance(value, bool) else None
            else:
                item[key] = _text(value)
            continue
        m = _FUTURE_ITEM_RE.match(name) or _HISTORIC_ITEM_RE.match(name)
        if m:
            bucket = future if name.startswith("future_capex.") else historic
            item = bucket.setdefault(int(m.group(1)), {"page": _page(row)})
            key = m.group(2)
            if key == "usd":
                item["usd"] = _num(value)
            elif key == "year":
                year = _num(value)
                item["year"] = int(year) if year is not None else None
            else:
                item[key] = _text(value)
            continue
        m = _HISTORIC_BY_YEAR_RE.match(name)
        if m:
            amount = _num(value)
            if amount is not None and m.group(1) not in doc.historic_by_year:
                doc.historic_by_year[m.group(1)] = amount
            continue
        m = _TIMING_RE.match(name)
        if m:
            amount = _num(value)
            if amount is not None:
                doc.timing_by_year.setdefault(m.group(1), {})[m.group(2)] = amount
    doc.scope_items = [
        {"index": idx, **scope[idx]} for idx in sorted(scope)
    ]
    doc.future_items = [
        {"index": idx, **future[idx]} for idx in sorted(future)
    ]
    doc.historic_items = [
        {"index": idx, **historic[idx]} for idx in sorted(historic)
    ]
    return doc


# ───────────────────────────── loading ──────────────────────────────


async def _documents_columns(session: AsyncSession) -> frozenset[str]:
    """Which optional ``documents`` columns the live schema carries
    (``doc_subtype`` / ``report_as_of``). Catalog introspection, memoised
    on the session — a failed probe SELECT would poison an open
    transaction. Any failure answers "none" (both read as NULL)."""
    try:
        cached = session.info.get(_DOC_COLUMNS_CACHE_KEY)
    except Exception:  # noqa: BLE001
        cached = None
    if isinstance(cached, frozenset):
        return cached
    wanted = {"doc_subtype", "report_as_of"}
    have: set[str] = set()
    try:
        from ..config import get_settings as _gs

        is_sqlite = str(_gs().async_database_url).startswith("sqlite")
    except Exception:  # noqa: BLE001
        is_sqlite = False
    try:
        if is_sqlite:
            rows = await session.execute(text("PRAGMA table_info(documents)"))
            have = {str(r[1]).lower() for r in rows.fetchall()}
        else:
            rows = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'documents'"
                )
            )
            have = {str(r[0]).lower() for r in rows.fetchall()}
    except Exception:  # noqa: BLE001
        have = set()
    answer = frozenset(wanted & have)
    try:
        session.info[_DOC_COLUMNS_CACHE_KEY] = answer
    except Exception:  # noqa: BLE001
        pass
    return answer


async def load_capex_documents(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> list[CapexDocument]:
    """Every CAPEX document on the deal with its LATEST extraction parsed.

    Tenant-scoped on both ``documents`` and ``extraction_results``. A
    document that has not been extracted yet is still listed
    (``extracted=False``, no total) so the analyst sees it is there.
    Returns ``[]`` for a non-UUID deal id or on any read failure — the
    loader then falls through to the OM / seed exactly as before FON-101.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return []
    cols = await _documents_columns(session)
    subtype_col = "d.doc_subtype" if "doc_subtype" in cols else "NULL"
    as_of_col = "d.report_as_of" if "report_as_of" in cols else "NULL"
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                f"""
                SELECT d.id AS document_id, d.filename, d.uploaded_at,
                       {subtype_col} AS doc_subtype, {as_of_col} AS report_as_of,
                       er.id AS extraction_result_id, er.fields, er.created_at
                  FROM documents d
                  LEFT JOIN extraction_results er
                    ON er.document_id = d.id
                   AND er.tenant_id = :tenant
                 WHERE d.deal_id = :deal
                   AND d.tenant_id = :tenant
                   AND UPPER(COALESCE(d.doc_type, '')) = 'CAPEX'
                 ORDER BY er.created_at DESC, d.uploaded_at DESC
                """  # the two column expressions are fixed literals, never user input
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
        all_rows = rows.fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("renovation_budget: CAPEX document read failed for deal %s", deal_id)
        return []

    out: dict[str, CapexDocument] = {}
    for r in all_rows:
        m = r._mapping
        doc_id = str(m["document_id"])
        if doc_id in out:
            # Rows are newest-extraction-first per document; the first one
            # seen is the latest. Older extractions are ignored.
            continue
        subtype = m.get("doc_subtype")
        doc = CapexDocument(
            document_id=doc_id,
            filename=str(m.get("filename") or ""),
            doc_subtype=str(subtype).strip().lower() if isinstance(subtype, str) and subtype.strip() else None,
            uploaded_at=_iso_date(m.get("uploaded_at")),
            report_as_of=_iso_date(m.get("report_as_of")),
        )
        er_id = m.get("extraction_result_id")
        if er_id is not None:
            doc.extraction_result_id = str(er_id)
            doc.extracted = True
            parse_capex_fields(doc, m.get("fields"))
        out[doc_id] = doc
    return list(out.values())


# ───────────────────────────── selection ────────────────────────────


def eligible_pip_documents(docs: list[CapexDocument]) -> list[CapexDocument]:
    """The documents that may ground the model: laned ``pip`` or untagged
    (``doc_subtype`` NULL), with a positive extracted PIP total. Future and
    Historic CapEx are reference only and never appear here, whatever
    namespace their rows carry. Newest effective date first."""
    return sorted(
        (d for d in docs if d.may_ground_model and d.has_pip_total),
        key=lambda d: d.sort_key,
        reverse=True,
    )


def select_pip_candidate(docs: list[CapexDocument]) -> CapexDocument | None:
    """The PIP / CapEx document the model's renovation budget should ground
    on, or ``None`` when no eligible document exists.

    Latest ``effective_date`` wins (plan date > report date > upload date);
    the upload date breaks a tie. A ``future`` or ``historic`` document
    never qualifies, whatever its rows say.
    """
    eligible = eligible_pip_documents(docs)
    return eligible[0] if eligible else None


# ───────────────────────── analyst-facing payload ───────────────────


@dataclass(frozen=True)
class OmRenovationBudget:
    """The OM's broker figure, read BEFORE any analyst override."""

    value_usd: float
    document_id: str | None
    filename: str | None
    page: int | None
    field_name: str | None
    report_as_of: str | None


async def load_om_renovation_budget(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> OmRenovationBudget | None:
    """``broker_proforma.renovation_budget_usd`` (or its catalog aliases)
    off the deal's most recent OM extraction — the raw broker figure, with
    no override applied. Mirrors the alias tail-match the capital loader
    uses so the two never disagree about WHICH row is the OM's budget."""
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return None
    cols = await _documents_columns(session)
    as_of_col = "d.report_as_of" if "report_as_of" in cols else "NULL"
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                f"""
                SELECT er.fields, er.document_id, d.filename,
                       {as_of_col} AS report_as_of
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:  # noqa: BLE001
        return None
    from ..extraction.field_catalog import OM_CAPITAL_FIELD_ALIASES

    for r in rows.fetchall():
        m = r._mapping
        fields = m["fields"]
        if isinstance(fields, str):
            try:
                fields = json.loads(fields)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(fields, list):
            continue
        for f in fields:
            if not isinstance(f, Mapping):
                continue
            name = str(f.get("field_name") or "").strip().lower()
            if not name:
                continue
            canonical = OM_CAPITAL_FIELD_ALIASES.get(name)
            if canonical is None:
                tail = name.rsplit(".", 1)[-1] if "." in name else name
                canonical = OM_CAPITAL_FIELD_ALIASES.get(tail)
            if canonical != "renovation_budget":
                continue
            value = _num(f.get("value"))
            if value is None:
                continue
            return OmRenovationBudget(
                value_usd=value,
                document_id=str(m.get("document_id")) if m.get("document_id") else None,
                filename=m.get("filename"),
                page=_page(f),
                field_name=str(f.get("field_name") or "") or None,
                report_as_of=_iso_date(m.get("report_as_of")),
            )
    return None


_STATUS_LABEL: dict[str, str] = {
    "brand_issued_pip": "Brand-issued PIP",
    "owner_budget": "Owner budget",
    "approved_capital_plan": "Approved capital plan",
    "estimate": "Estimate",
}


def status_label(status: str | None) -> str | None:
    if not status:
        return None
    key = status.strip().lower().replace(" ", "_").replace("-", "_")
    return _STATUS_LABEL.get(key, status.strip())


def _per_key(value: float | None, keys: Any) -> float | None:
    try:
        k = float(keys) if keys is not None else 0.0
    except (TypeError, ValueError):
        return None
    if value is None or k <= 0:
        return None
    return value / k


def build_candidates(
    docs: list[CapexDocument],
    *,
    om: OmRenovationBudget | None,
    override_value: float | None,
    override_note: str | None,
    selected_source: str | None,
    selected_document_id: str | None,
    keys: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(candidates, reference_only)`` for the analyst.

    ``selected_source`` is the loader's ``__sources__['renovation_budget']``
    (``analyst_override`` / ``pip_document`` / ``om_broker`` / ``seed``) and
    ``selected_document_id`` the document behind it when a document grounds
    it — the candidate list REPORTS the model's state, it does not re-derive
    it, so the two can never disagree.
    """
    candidates: list[dict[str, Any]] = []
    eligible = eligible_pip_documents(docs)
    winner = eligible[0] if eligible else None

    if override_value is not None:
        candidates.append(
            {
                "id": "override:renovation_budget",
                "kind": "analyst_override",
                "label": "Analyst override",
                "value_usd": override_value,
                "per_key_usd": _per_key(override_value, keys),
                "document_id": None,
                "filename": None,
                "page": None,
                "field_name": "field_overrides.renovation_budget",
                "date": None,
                "date_basis": None,
                "status": None,
                "status_label": None,
                "prepared_by": None,
                "note": override_note,
                "selected": selected_source == "analyst_override",
                "reason": (
                    "Your override — wins over every document."
                    if selected_source == "analyst_override"
                    else "Your override equals the selected source's figure, so the model reports that source."
                ),
            }
        )

    for d in docs:
        if d.is_reference_only:
            continue
        iso, basis = d.effective_date
        selected = selected_source == SOURCE_PIP_DOCUMENT and d.document_id == selected_document_id
        if not d.extracted:
            reason = "Not extracted yet — no figure to read."
        elif not d.has_pip_total:
            reason = (
                "No PIP total was extracted from this document "
                f"(``{PIP_TOTAL_PATH}``); nothing to select."
            )
        elif selected:
            reason = (
                "Latest-dated PIP / CapEx document — outranks the OM's broker figure."
                if len(eligible) > 1 or om is not None
                else "The only document stating a renovation budget."
            )
            if override_value is not None and selected_source != "analyst_override":
                reason += " Your override equals this figure."
        elif winner is not None and d is not winner:
            w_iso, _ = winner.effective_date
            reason = (
                f"Superseded by {winner.filename or 'a newer document'}"
                + (f" dated {w_iso}" if w_iso else " (newer upload)")
                + "."
            )
        elif selected_source == "analyst_override":
            reason = "Your override is active; this document is the next in line."
        else:
            reason = "Eligible, but the model is on another source."
        candidates.append(
            {
                "id": f"document:{d.document_id}",
                "kind": SOURCE_PIP_DOCUMENT,
                "label": d.filename or "PIP / CapEx document",
                "value_usd": d.pip_total_usd,
                "per_key_usd": (
                    d.pip_per_key_usd
                    if d.pip_per_key_usd is not None
                    else _per_key(d.pip_total_usd, keys)
                ),
                "document_id": d.document_id,
                "filename": d.filename,
                "page": _page(d.pip_total_row) if d.pip_total_row else None,
                "field_name": PIP_TOTAL_PATH if d.pip_total_row else None,
                "date": iso,
                "date_basis": basis,
                "status": d.status,
                "status_label": status_label(d.status),
                "prepared_by": d.prepared_by,
                "hard_usd": d.pip_hard_usd,
                "soft_usd": d.pip_soft_usd,
                "contingency_usd": d.pip_contingency_usd,
                "contingency_pct": d.pip_contingency_pct,
                "completion_quarter": d.pip_completion_quarter,
                "scope_items": d.scope_items,
                "note": None,
                "selected": selected,
                "reason": reason,
            }
        )

    if om is not None:
        selected = selected_source == "om_broker"
        if selected:
            reason = "The OM's broker pro forma — no PIP / CapEx document states a budget."
            if override_value is not None:
                reason = "The OM's broker pro forma — your override equals this figure."
        elif winner is not None:
            w_iso, _ = winner.effective_date
            reason = (
                f"Outranked by the PIP / CapEx document {winner.filename or ''}".rstrip()
                + (f" dated {w_iso}" if w_iso else "")
                + " — a separate PIP is the more detailed source."
            )
        elif selected_source == "analyst_override":
            reason = "Your override is active; the OM figure is the fallback."
        else:
            reason = "Eligible, but the model is on another source."
        candidates.append(
            {
                "id": "om:renovation_budget",
                "kind": "om_broker",
                "label": om.filename or "Offering Memorandum",
                "value_usd": om.value_usd,
                "per_key_usd": _per_key(om.value_usd, keys),
                "document_id": om.document_id,
                "filename": om.filename,
                "page": om.page,
                "field_name": om.field_name,
                "date": om.report_as_of,
                "date_basis": "report_as_of" if om.report_as_of else None,
                "status": "broker_pro_forma",
                "status_label": "OM broker pro forma",
                "prepared_by": None,
                "note": None,
                "selected": selected,
                "reason": reason,
            }
        )

    reference_only: list[dict[str, Any]] = []
    for d in docs:
        if not d.is_reference_only:
            continue
        reference_only.append(reference_only_entry(d))
    return candidates, reference_only


RETAG_HINT = (
    " It states a PIP total; re-tag it as PIP / Renovation budget in the "
    "Data Room to make it a candidate."
)


def reference_only_entry(d: CapexDocument) -> dict[str, Any]:
    """The analyst-facing row for a Future / Historic CapEx document.

    Each lane exposes ONLY its own namespace — ``future_capex.*`` for
    Future CapEx, ``historic_capex.*`` for Historic CapEx. A PIP total the
    extractor also found is reported under ``pip_total_usd`` with a re-tag
    hint so a mis-laned brand PIP is visible, never silently dropped — but
    it is never the lane's ``total_usd``.
    """
    iso, basis = d.effective_date
    if d.is_future:
        plan_iso = _iso_date(d.future_plan_date)
        return {
            "id": f"document:{d.document_id}",
            "lane": FUTURE_CAPEX_SUBTYPE,
            "lane_label": "Future CapEx",
            "document_id": d.document_id,
            "filename": d.filename,
            "date": plan_iso or iso,
            "date_basis": "plan_date" if plan_iso else basis,
            "prepared_by": d.future_prepared_by or d.prepared_by,
            "total_usd": d.future_total_usd,
            "items": d.future_items,
            "by_year": {},
            "extracted": d.extracted,
            "pip_total_usd": d.pip_total_usd,
            "reason": (
                "Future CapEx — reference only, never incorporated in the model."
                + (RETAG_HINT if d.has_pip_total else "")
            ),
        }
    return {
        "id": f"document:{d.document_id}",
        "lane": HISTORIC_CAPEX_SUBTYPE,
        "lane_label": "Historic CapEx",
        "document_id": d.document_id,
        "filename": d.filename,
        "date": iso,
        "date_basis": basis,
        "prepared_by": None,
        "total_usd": d.historic_total_usd,
        "items": d.historic_items,
        "by_year": dict(sorted(d.historic_by_year.items())),
        "extracted": d.extracted,
        "pip_total_usd": d.pip_total_usd,
        "reason": (
            "Historic CapEx — reference only, never incorporated in the model."
            + (RETAG_HINT if d.has_pip_total else "")
        ),
    }


__all__ = [
    "FUTURE_CAPEX_SUBTYPE",
    "HISTORIC_CAPEX_SUBTYPE",
    "PIP_CAPEX_SUBTYPE",
    "PIP_TOTAL_PATH",
    "REFERENCE_ONLY_CAPEX_SUBTYPES",
    "REFERENCE_ONLY_NAMESPACES",
    "RETAG_HINT",
    "SOURCE_PIP_DOCUMENT",
    "CapexDocument",
    "OmRenovationBudget",
    "build_candidates",
    "coerce_usd",
    "eligible_pip_documents",
    "reference_only_entry",
    "load_capex_documents",
    "load_om_renovation_budget",
    "parse_capex_fields",
    "select_pip_candidate",
    "status_label",
]
