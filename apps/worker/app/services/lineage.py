"""Build the evidence chain — KPI → engine → assumption → field → doc → page.

Phase 2.3. Read-only. Nothing in this module mutates a deal, an engine output
or an extraction; :func:`persist_for_run` writes exactly one row to
``lineage_records`` and nothing else.

What it joins
-------------

Four existing spines, none of which previously knew about the others:

``engine_outputs[run].outputs["provenance"]``
    The per-value :class:`~fondok_schemas.provenance.ValueTrace` sidecar each
    engine emits — a formula, its named inputs, and (sometimes) a
    ``traces_to`` pointer at another traced value in the same or another
    engine's map.

``_load_engine_inputs(...)["__sources__"]``
    Which ``SOURCE_*`` label produced each canonical assumption
    (``t12_actual`` / ``seed`` / ``analyst_override`` / ``cbre_horizons`` …).

``__source_fields__`` *(feature-detected)*
    When the engine runner supplies it, the exact extraction row behind each
    assumption: ``{key: {document_id, extraction_result_id, field_name,
    source_page, concept, scope, basis, doc_type, as_of}}``. This module
    prefers it and falls back to resolving the field itself (see below) so it
    is never blocked on that sidecar existing.

``documents`` / ``extraction_results`` + the concept registry
    The fallback path: ``_load_source_documents`` says WHICH document backed an
    assumption; ``app.ontology.registry.resolve`` then says which FIELD on that
    document's newest extraction carries the concept, and on which page.

Where the engines stop, this module bridges
-------------------------------------------

No engine emits ``ValueInput.assumption_key`` today, and the returns engine's
IRR trace lists its cash-flow stream rather than the upstream values that
produced it. So the walk resolves an input in this order:

1. an explicit ``assumption_key`` → the assumption node;
2. an explicit ``traces_to`` → that engine value (exact key, else every key
   under it when the reference is a prefix like ``"expense.years"``);
3. the input's *name* matched against the canonical assumption vocabulary
   (:data:`_INPUT_ASSUMPTION_KEYS` — e.g. the revenue engine's ``occupied_rooms``
   / ``adr``, whose own trace note says they "chain back to the
   starting_occupancy / starting_adr assumptions");
4. a **bridge** derived from the engine dependency graph
   (:func:`_bridge_targets` — e.g. the returns engine's ``cash_flow_year_3`` is
   Year-3 NOI less Year-3 debt service). Bridge edges carry their rationale in
   ``LineageEdge.formula`` and say ``(lineage bridge)`` so they are never
   mistaken for something an engine asserted.

Anything still unresolved is recorded, never dropped: a root or an assumption
that does not reach a ``page:`` node lands in ``LineageRecord.unresolved`` as a
typed :class:`~fondok_schemas.reasons.Refusal`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fondok_schemas.lineage import LineageEdge, LineageNode, LineageRecord
from fondok_schemas.reasons import ReasonCode, Refusal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ─────────────────────────── node id helpers ──────────────────────────
#
# One prefix per node kind. Ids are the API — the web app routes on the
# prefix alone — so they are built here and nowhere else.


def _kpi_node(name: str) -> str:
    return f"kpi:{name}"


def _engine_node(engine: str, path: str) -> str:
    return f"engine:{engine}.{path}"


def _assumption_node(key: str) -> str:
    return f"assumption:{key}"


def _field_node(extraction_result_id: str, field_name: str) -> str:
    return f"field:{extraction_result_id}:{field_name}"


def _document_node(document_id: str) -> str:
    return f"doc:{document_id}"


def _page_node(document_id: str, page: int) -> str:
    return f"page:{document_id}:{page}"


def _override_node(key: str) -> str:
    return f"override:{key}"


def _seed_node(key: str) -> str:
    return f"seed:{key}"


def _benchmark_node(key: str) -> str:
    return f"benchmark:{key}"


def _memo_node(section_id: str) -> str:
    return f"memo:{section_id}"


# ─────────────────────────── the KPI roots ────────────────────────────
#
# The headline numbers a deal is judged on. Each names the engine output
# field the value is read from and the traced path the walk starts at.
# ``min DSCR`` and ``Year-1 NOI`` are positional (they live inside a list),
# so they resolve their traced path at build time — see ``_root_specs``.

_ROOT_LEVERED_IRR = "returns.levered_irr"
_ROOT_UNLEVERED_IRR = "returns.unlevered_irr"
_ROOT_EQUITY_MULTIPLE = "returns.equity_multiple"
_ROOT_YEAR_ONE_COC = "returns.year_one_coc"
_ROOT_MIN_DSCR = "debt.min_dscr"
_ROOT_YEAR_ONE_NOI = "expense.year_one_noi"
_ROOT_MAX_PRICE = "pricing.max_price"

# Simple roots: (kpi name, engine, output field, traced path, label, unit).
_SIMPLE_ROOTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (_ROOT_LEVERED_IRR, "returns", "levered_irr", "levered_irr", "Levered IRR", "pct"),
    (
        _ROOT_UNLEVERED_IRR,
        "returns",
        "unlevered_irr",
        "unlevered_irr",
        "Unlevered IRR",
        "pct",
    ),
    (
        _ROOT_EQUITY_MULTIPLE,
        "returns",
        "equity_multiple",
        "equity_multiple",
        "Equity multiple",
        "ratio",
    ),
    (
        _ROOT_YEAR_ONE_COC,
        "returns",
        "year_one_coc",
        "year_one_coc",
        "Year-1 cash-on-cash",
        "pct",
    ),
)


# ───────────────────── assumption ↔ concept ↔ input names ─────────────
#
# The canonical assumption keys (see ``engine_runner._kimpton_assumptions``)
# mapped onto the concept registry, so the fallback path can ask the registry
# "which field on this document carries this concept, and on what page?".
# Only keys with a real document-level concept appear; a purely modelled
# assumption (``hold_years``, ``exit_cap_rate`` …) has none and terminates at
# its seed / override node.
_ASSUMPTION_CONCEPTS: dict[str, str] = {
    "starting_occupancy": "occupancy",
    "starting_adr": "adr",
    "revpar_growth": "revpar",
    "keys": "keys",
    "purchase_price": "purchase_price",
    "fb_revenue_per_occupied_room": "fb_revenue",
    "rooms_dept_expense": "rooms_dept_expense",
    "fb_dept_expense": "fb_dept_expense",
    "other_dept_expense": "other_dept_expense",
    "administrative_general": "administrative_general",
    "information_telecom": "information_telecom",
    "sales_marketing": "sales_marketing",
    "property_operations": "property_operations",
    "utilities": "utilities",
    "property_taxes": "property_taxes",
    "insurance": "insurance",
    "mgmt_fee": "mgmt_fee",
    "mgmt_fee_pct": "mgmt_fee",
    "ffe_reserve": "ffe_reserve",
    "ffe_reserve_pct": "ffe_reserve",
    "exit_cap_rate": "exit_cap_rate",
    "ltv": "ltv",
    "interest_rate": "interest_rate",
    "amortization_years": "amortization_years",
    "term_years": "term_years",
    "renovation_budget": "renovation_budget",
    "working_capital": "working_capital",
}

# Where the SOURCE label changes WHICH concept the value actually is. The
# exit cap the OM grounds is the median of the broker's comparable-sales
# table — ``comp_cap_rate``, not the OM's own ``exit_cap_rate`` line — so
# the field the walk lands on is the one the value was really read from.
_LABEL_CONCEPTS: dict[tuple[str, str], str] = {
    ("exit_cap_rate", "om_comps"): "comp_cap_rate",
}

# A traced input's NAME → the canonical assumption it chains back to. Used
# only when the trace carries neither ``assumption_key`` nor ``traces_to``,
# and only when the key is actually present in ``__sources__`` for this deal.
# Every entry is grounded in the engine's own trace note or formula — see the
# module docstring.
_INPUT_ASSUMPTION_KEYS: dict[str, str] = {
    "occupied_rooms": "starting_occupancy",
    "occupancy": "starting_occupancy",
    "adr": "starting_adr",
    "keys": "keys",
    "purchase_price": "purchase_price",
    "exit_cap_rate": "exit_cap_rate",
    "selling_costs_pct": "selling_costs_pct",
    "transfer_tax_pct": "transfer_tax_pct",
    "ltv": "ltv",
    "interest_rate": "interest_rate",
    "amortization_years": "amortization_years",
    "hold_years": "hold_years",
    "management_fee": "mgmt_fee_pct",
    "mgmt_fee": "mgmt_fee_pct",
    "ffe_reserve": "ffe_reserve_pct",
    "renovation_budget": "renovation_budget",
    "working_capital": "working_capital",
    "closing_costs_pct": "closing_costs_pct",
    "loan_costs_pct": "loan_costs_pct",
    "soft_costs": "soft_costs",
    "contingency": "contingency",
    "fb_revenue_per_occupied_room": "fb_revenue_per_occupied_room",
    "other_revenue_pct_of_rooms": "other_revenue_pct_of_rooms",
    "expense_growth": "expense_growth",
    "revpar_growth": "revpar_growth",
    "adr_growth": "adr_growth",
    "occupancy_growth": "occupancy_growth",
    "pref_rate": "pref_rate",
    "gp_equity_pct": "gp_equity_pct",
    "lp_equity_pct": "lp_equity_pct",
    "fb_ratio": "fb_ratio",
    "other_ratio": "other_ratio",
}


# Source labels, grouped by what they mean for the walk. Kept as literals
# (not imports of engine_runner's SOURCE_* constants) so this module has no
# import-time dependency on the runner the parallel builder owns; the values
# are the wire format the web app already badges and cannot drift silently.
_OVERRIDE_LABELS: frozenset[str] = frozenset({"analyst_override", "pip_user", "roi_user"})
_BENCHMARK_LABELS: frozenset[str] = frozenset(
    {
        "cbre_horizons",
        "pnl_benchmark",
        "portfolio_pnl",
        "str_forecast",
        "str_segmentation_default",
        "capex_ffe_default",
    }
)
# Labels that point at a document the analyst uploaded about THIS asset.
_DOCUMENT_LABELS: frozenset[str] = frozenset(
    {
        "t12_actual",
        "om_comps",
        "om_broker",
        "pip_om",
        "partnership_doc",
        "portfolio_pnl",
        "pnl_benchmark",
        "cbre_horizons",
    }
)
# A label that IS a refusal — the assumption could not be grounded.
_REFUSAL_LABELS: dict[str, ReasonCode] = {
    "str_forecast_unavailable": ReasonCode.STR_UNAVAILABLE,
}


# ───────────────────────── cross-engine bridges ───────────────────────

_CASH_FLOW_YEAR = re.compile(r"^cash_flow_year_(\d+)$")
_YEARS_INDEX = re.compile(r"years\[(\d+)\]")
_SCHEDULE_INDEX = re.compile(r"schedule\[(\d+)\]")

# A prefix ``traces_to`` (``"expense.years"``) can match many traced keys.
# Cap the fan-out so one loose reference cannot explode the graph.
_MAX_PREFIX_FANOUT = 24


def _bridge_targets(
    engine: str, trace_path: str, input_name: str, *, last_year_index: int
) -> list[tuple[str, str]]:
    """Cross-engine links the engines do not (yet) assert themselves.

    Returns ``[(target_engine, target_path)]``. Every entry restates a
    relationship the engine chain already implements — the returns engine
    consumes the cash-flow view built from expense NOI and debt service; the
    expense engine's GOP consumes revenue's total revenue; the debt engine's
    DSCR consumes the same year's NOI. Nothing here invents a number: the
    bridge only says which *traced value* the input came from.
    """
    name = input_name.strip().lower()

    if engine == "returns":
        m = _CASH_FLOW_YEAR.match(name)
        if m is not None:
            year = int(m.group(1))
            if year == 0:
                # Year 0 is the equity outlay (levered) / purchase price
                # (unlevered) — both live on the capital stack.
                return [("capital", "equity_amount"), ("capital", "purchase_price")]
            idx = year - 1
            targets = [
                ("expense", f"years[{idx}].noi"),
                ("debt", f"schedule[{idx}].debt_service"),
            ]
            if idx >= last_year_index:
                # The final flow carries the sale: "Years 1…N = cash flow
                # after debt service, plus net sale proceeds at exit"
                # (returns.py's own IRR trace note).
                targets.append(("returns", "net_proceeds"))
            return targets
        if name == "total_distributions":
            # "total_distributions = Σ annual cash-flow-after-debt + net_proceeds
            # at exit" — the equity-multiple trace's own note.
            return [("returns", "net_proceeds")]
        if name in ("terminal_noi",):
            return [("expense", f"years[{last_year_index}].noi")]
        if name in ("equity",):
            return [("capital", "equity_amount")]
        if name in ("year_1_cash_flow_after_debt", "year_one_cfad"):
            return [("expense", "years[0].noi"), ("debt", "schedule[0].debt_service")]
        return []

    if engine == "expense":
        if name == "total_revenue":
            m = _YEARS_INDEX.search(trace_path)
            idx = m.group(1) if m else "0"
            return [("revenue", f"years[{idx}].total_revenue")]
        return []

    if engine == "debt":
        if name in ("noi", "year_noi"):
            m = _SCHEDULE_INDEX.search(trace_path)
            idx = m.group(1) if m else "0"
            return [("expense", f"years[{idx}].noi")]
        if name == "year_1_noi":
            return [("expense", "years[0].noi")]
        if name == "annual_debt_service":
            return [("debt", "schedule[0].debt_service")]
        return []

    return []


_BRIDGE_NOTE = "(lineage bridge — derived from the engine dependency graph)"


# ───────────────────────────── timestamps ─────────────────────────────


def _parse_ts(value: Any) -> datetime | None:
    """Parse a timestamp from either dialect into an aware UTC datetime.

    Postgres hands back ``datetime`` (aware or naive); SQLite hands back TEXT
    in two shapes — the ISO-8601 the engine runner writes
    (``2026-09-10T12:00:00+00:00``) and the ``CURRENT_TIMESTAMP`` default
    (``2026-09-10 12:00:00``, always UTC). String comparison across those two
    is wrong, which is why every staleness check goes through here.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    raw = str(value).strip()
    if not raw:
        return None
    candidate = raw.replace(" ", "T", 1) if " " in raw and "T" not in raw else raw
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ─────────────────────────────── the builder ──────────────────────────


class _Builder:
    """Accumulates nodes / edges / refusals for one deal's lineage."""

    def __init__(self) -> None:
        self.nodes: dict[str, LineageNode] = {}
        self.edges: list[LineageEdge] = []
        self._edge_keys: set[tuple[str, str, str]] = set()
        self.unresolved: list[Refusal] = []
        self._refusal_keys: set[tuple[str, str | None, str | None]] = set()

    def node(self, node: LineageNode) -> str:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
        elif existing.value is None and node.value is not None:
            # A stub added by a referrer gets filled in when the real node
            # arrives (a page node created from a citation, say, later
            # gaining its extracted value).
            self.nodes[node.id] = node
        return node.id

    def edge(self, src: str, dst: str, rel: str, formula: str | None = None) -> None:
        key = (src, dst, rel)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(LineageEdge(src=src, dst=dst, rel=rel, formula=formula))

    def refuse(
        self,
        code: ReasonCode,
        *,
        detail: str | None = None,
        concept: str | None = None,
        document_id: UUID | None = None,
    ) -> None:
        key = (code.value, detail, concept)
        if key in self._refusal_keys:
            return
        self._refusal_keys.add(key)
        self.unresolved.append(
            Refusal(code=code, detail=detail, concept=concept, document_id=document_id)
        )

    def reachable_kinds(self, root: str) -> set[str]:
        """Every node kind reachable from ``root`` by following edges."""
        adjacency: dict[str, list[str]] = {}
        for e in self.edges:
            adjacency.setdefault(e.src, []).append(e.dst)
        seen: set[str] = set()
        stack = [root]
        kinds: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self.nodes.get(current)
            if node is not None:
                kinds.add(node.kind)
            stack.extend(adjacency.get(current, ()))
        return kinds


# ─────────────────────────────── loaders ──────────────────────────────


async def _load_documents(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> dict[str, dict[str, Any]]:
    """``{document_id: {filename, doc_type, status, uploaded_at, page_count}}``."""
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT id, filename, doc_type, status, uploaded_at, page_count
                  FROM documents
                 WHERE deal_id = :deal AND tenant_id = :tenant
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:  # pragma: no cover - schema not migrated in a bare test DB
        logger.debug("lineage: documents lookup failed for deal %s", deal_id)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows.fetchall():
        m = r._mapping
        out[str(m["id"])] = {
            "filename": m["filename"],
            "doc_type": (m["doc_type"] or "") or None,
            "status": m["status"],
            "uploaded_at": m["uploaded_at"],
            "page_count": m["page_count"],
        }
    return out


async def _load_extraction_for_document(
    session: AsyncSession, *, document_id: str, tenant_id: str
) -> tuple[str, list[dict[str, Any]]] | None:
    """Newest extraction row for ``document_id`` as ``(id, long-form fields)``."""
    try:
        row = (
            await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    """
                    SELECT id, fields, catalog_version
                      FROM extraction_results
                     WHERE document_id = :doc AND tenant_id = :tenant
                     ORDER BY created_at DESC
                     LIMIT 1
                    """
                ),
                {"doc": document_id, "tenant": tenant_id},
            )
        ).first()
    except Exception:  # pragma: no cover - schema not migrated
        return None
    if row is None:
        return None
    m = row._mapping
    raw = m["fields"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list):
        return None
    from ..extraction.terse_schema import read_extraction_fields

    try:
        fields = read_extraction_fields(raw, m["catalog_version"])
    except Exception:  # pragma: no cover - defensive
        fields = [f for f in raw if isinstance(f, dict)]
    return str(m["id"]), fields


async def _deal_updated_at(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> datetime | None:
    try:
        row = (
            await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    "SELECT updated_at FROM deals WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": deal_id, "tenant": tenant_id},
            )
        ).first()
    except Exception:  # pragma: no cover - column always present post-migration
        return None
    if row is None:
        return None
    return _parse_ts(row._mapping.get("updated_at"))


def _versions() -> tuple[int, str]:
    """``(registry_version, pipeline_version)`` — best-effort, never raises."""
    registry_version = 0
    pipeline_version = "unknown"
    try:
        from ..ontology.registry import registry_version as _rv

        registry_version = int(_rv())
    except Exception:  # pragma: no cover - a broken registry fails /health first
        logger.debug("lineage: registry version unavailable")
    try:
        from ..api.documents import EXTRACTION_PIPELINE_VERSION

        pipeline_version = str(EXTRACTION_PIPELINE_VERSION)
    except Exception:  # pragma: no cover - defensive
        logger.debug("lineage: pipeline version unavailable")
    return registry_version, pipeline_version


# ────────────────────────────── the walk ──────────────────────────────


async def build_lineage(
    session: AsyncSession,
    deal_id: UUID | str,
    tenant_id: UUID | str,
    run_id: UUID | str | None = None,
) -> LineageRecord:
    """Build the deal's evidence graph. Read-only; never raises on bad data.

    ``run_id`` pins the record to one engine run; omitted, the deal's
    canonical (deal-wide, base-case) run is used — the same run
    ``GET /deals/{id}/provenance`` and every deal-wide tab read, so the
    lineage always describes the numbers on screen.
    """
    deal_str = str(deal_id)
    tenant_str = str(tenant_id)

    from .engine_runner import (
        ENGINE_NAMES,
        _coerce_uuid,
        _load_engine_inputs,
        _load_source_documents,
        get_canonical_run_id,
        get_latest_outputs,
        get_run_status,
    )

    deal_uuid = _coerce_uuid(deal_str)

    # ── 1. the run + its engine envelopes ────────────────────────────
    resolved_run = str(run_id) if run_id is not None else None
    if resolved_run is None:
        resolved_run = await get_canonical_run_id(
            session, deal_id=deal_str, tenant_id=tenant_str
        )
    if resolved_run is not None:
        rows = await get_run_status(
            session, deal_id=deal_str, run_id=resolved_run, tenant_id=tenant_str
        )
        envelopes = {r["engine"]: r for r in rows if r.get("engine") in ENGINE_NAMES}
    else:
        envelopes = await get_latest_outputs(
            session, deal_id=deal_str, tenant_id=tenant_str
        )

    outputs: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    run_started: datetime | None = None
    for name, env in envelopes.items():
        if not isinstance(env, dict):
            continue
        started = _parse_ts(env.get("started_at"))
        if started is not None and (run_started is None or started < run_started):
            run_started = started
        out = env.get("outputs")
        if not isinstance(out, dict):
            continue
        outputs[name] = out
        prov = out.get("provenance")
        if isinstance(prov, dict):
            provenance[name] = {k: v for k, v in prov.items() if isinstance(v, dict)}

    # ── 2. the assumption spine ──────────────────────────────────────
    try:
        base = await _load_engine_inputs(session, deal_str, tenant_id=tenant_str)
    except Exception:  # pragma: no cover - defensive; a bad deal must not 500
        logger.exception("lineage: engine inputs failed for deal %s", deal_str)
        base = {}
    sources_raw = base.get("__sources__")
    sources: dict[str, str] = sources_raw if isinstance(sources_raw, dict) else {}
    # Feature-detected sidecars — present only once the engine runner supplies
    # them. Absent, the walk falls back to _load_source_documents + registry.
    sf_raw = base.get("__source_fields__")
    source_fields: dict[str, dict[str, Any]] = (
        {k: v for k, v in sf_raw.items() if isinstance(v, dict)}
        if isinstance(sf_raw, dict)
        else {}
    )
    reasons_raw = base.get("__reasons__")
    assumption_reasons: dict[str, Any] = (
        reasons_raw if isinstance(reasons_raw, dict) else {}
    )

    try:
        source_documents = await _load_source_documents(
            session, deal_id=deal_str, sources=sources, tenant_id=tenant_str
        )
    except Exception:
        source_documents = {}

    try:
        from .engine_runner import _load_deal_overrides_raw

        overrides_raw = await _load_deal_overrides_raw(
            session, deal_id=deal_str, tenant_id=tenant_str
        )
    except Exception:
        overrides_raw = {}

    documents = await _load_documents(
        session, deal_id=deal_str, tenant_id=tenant_str
    )

    builder = _Builder()
    ctx = _WalkContext(
        session=session,
        builder=builder,
        deal_id=deal_str,
        tenant_id=tenant_str,
        provenance=provenance,
        outputs=outputs,
        base=base,
        sources=sources,
        source_fields=source_fields,
        assumption_reasons=assumption_reasons,
        source_documents=source_documents,
        overrides_raw=overrides_raw,
        documents=documents,
    )

    # ── 3. roots → engine traces → assumptions → evidence ────────────
    roots = await ctx.build_roots()
    await ctx.drain()

    # ── 4. the IC memo's citations ───────────────────────────────────
    await ctx.attach_memo()

    # ── 5. what did not reach a page ─────────────────────────────────
    ctx.record_unreached(roots)

    # ── 6. staleness ─────────────────────────────────────────────────
    deal_updated = await _deal_updated_at(
        session, deal_id=deal_str, tenant_id=tenant_str
    )
    stale = _is_stale(
        run_started=run_started, documents=documents, deal_updated=deal_updated
    )

    registry_version, pipeline_version = _versions()
    return LineageRecord(
        deal_id=deal_uuid,
        run_id=UUID(resolved_run) if resolved_run else None,
        registry_version=registry_version,
        pipeline_version=pipeline_version,
        generated_at=datetime.now(UTC),
        roots=roots,
        nodes=list(builder.nodes.values()),
        edges=builder.edges,
        unresolved=builder.unresolved,
        stale=stale,
    )


def _is_stale(
    *,
    run_started: datetime | None,
    documents: Mapping[str, Mapping[str, Any]],
    deal_updated: datetime | None,
) -> bool:
    """True when an input moved after the run started.

    A record with no run to be measured against is not stale — it is
    ungrounded, which ``run_id is None`` already says.
    """
    if run_started is None:
        return False
    if deal_updated is not None and deal_updated > run_started:
        return True
    for meta in documents.values():
        uploaded = _parse_ts(meta.get("uploaded_at"))
        if uploaded is not None and uploaded > run_started:
            return True
    return False


class _WalkContext:
    """The stateful half of :func:`build_lineage` — kept out of the flow above."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        builder: _Builder,
        deal_id: str,
        tenant_id: str,
        provenance: dict[str, dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
        base: dict[str, Any],
        sources: dict[str, str],
        source_fields: dict[str, dict[str, Any]],
        assumption_reasons: dict[str, Any],
        source_documents: Mapping[str, str],
        overrides_raw: Mapping[str, Any],
        documents: dict[str, dict[str, Any]],
    ) -> None:
        self.session = session
        self.b = builder
        self.deal_id = deal_id
        self.tenant_id = tenant_id
        self.provenance = provenance
        self.outputs = outputs
        self.base = base
        self.sources = sources
        self.source_fields = source_fields
        self.assumption_reasons = assumption_reasons
        self.source_documents = source_documents
        self.overrides_raw = overrides_raw
        self.documents = documents
        self._queue: list[tuple[str, str]] = []
        self._expanded: set[tuple[str, str]] = set()
        self._assumptions_done: set[str] = set()
        self._extraction_cache: dict[str, tuple[str, list[dict[str, Any]]] | None] = {}
        expense_years = (self.outputs.get("expense") or {}).get("years")
        self.last_year_index = (
            len(expense_years) - 1 if isinstance(expense_years, list) and expense_years else 0
        )

    # ── roots ────────────────────────────────────────────────────────

    async def build_roots(self) -> list[str]:
        roots: list[str] = []
        for kpi, engine, field, path, label, unit in _SIMPLE_ROOTS:
            value = (self.outputs.get(engine) or {}).get(field)
            if value is None:
                continue
            roots.append(self._add_root(kpi, label, value, unit, engine, path))

        # Year-1 NOI — positional inside the expense engine's year list.
        years = (self.outputs.get("expense") or {}).get("years")
        if isinstance(years, list) and years and isinstance(years[0], dict):
            noi = years[0].get("noi")
            if noi is not None:
                roots.append(
                    self._add_root(
                        _ROOT_YEAR_ONE_NOI,
                        "Year-1 NOI",
                        noi,
                        "usd",
                        "expense",
                        "years[0].noi",
                        concept="noi",
                    )
                )

        # Minimum DSCR — the binding year across the debt schedule.
        min_dscr = self._min_dscr()
        if min_dscr is not None:
            value, path = min_dscr
            roots.append(
                self._add_root(
                    _ROOT_MIN_DSCR, "Minimum DSCR", value, "ratio", "debt", path
                )
            )

        # Max price — only when a run actually persisted one. The Max Price
        # Solver is an on-demand POST today (app/api/analysis.py), so on most
        # deals this root is simply absent rather than refused.
        for engine in ("returns", "sensitivity"):
            value = (self.outputs.get(engine) or {}).get("max_price")
            if value is not None:
                roots.append(
                    self._add_root(
                        _ROOT_MAX_PRICE, "Max price", value, "usd", engine, "max_price"
                    )
                )
                break
        return roots

    def _min_dscr(self) -> tuple[float, str] | None:
        schedule = (self.outputs.get("debt") or {}).get("schedule")
        best: tuple[float, str] | None = None
        if isinstance(schedule, list):
            for idx, year in enumerate(schedule):
                if not isinstance(year, dict):
                    continue
                dscr = year.get("dscr")
                if not isinstance(dscr, (int, float)):
                    continue
                if best is None or float(dscr) < best[0]:
                    best = (float(dscr), f"schedule[{idx}].dscr")
        if best is not None:
            return best
        year_one = (self.outputs.get("debt") or {}).get("year_one_dscr")
        if isinstance(year_one, (int, float)):
            return float(year_one), "year_one_dscr"
        return None

    def _add_root(
        self,
        kpi: str,
        label: str,
        value: Any,
        unit: str,
        engine: str,
        path: str,
        *,
        concept: str | None = None,
    ) -> str:
        root_id = self.b.node(
            LineageNode(
                id=_kpi_node(kpi),
                kind="kpi",
                label=label,
                value=_as_value(value),
                unit=unit,
                concept=concept,
                meta={"engine": engine, "output_path": path},
            )
        )
        trace = (self.provenance.get(engine) or {}).get(path)
        if trace is None:
            self.b.refuse(
                ReasonCode.NO_SOURCE,
                detail=(
                    f"{label}: the {engine} engine's persisted run carries no "
                    f"provenance trace for {path!r}, so the value cannot be walked."
                ),
                concept=concept or root_id,
            )
            return root_id
        target = self._ensure_engine_node(engine, path)
        self.b.edge(root_id, target, "computed_from", trace.get("formula"))
        return root_id

    # ── engine-value walk ────────────────────────────────────────────

    def _ensure_engine_node(self, engine: str, path: str) -> str:
        node_id = _engine_node(engine, path)
        if node_id not in self.b.nodes:
            trace = (self.provenance.get(engine) or {}).get(path) or {}
            self.b.node(
                LineageNode(
                    id=node_id,
                    kind="engine_value",
                    label=f"{engine}.{path}",
                    value=_as_value(trace.get("value")),
                    source=_coerce_str(trace.get("source")),
                    state=_coerce_state(trace.get("state")),
                    reason=_coerce_reason(trace.get("reason")),
                    meta={
                        k: v
                        for k, v in (
                            ("engine", engine),
                            ("output_path", path),
                            ("formula", trace.get("formula")),
                            ("note", trace.get("note")),
                        )
                        if v is not None
                    },
                )
            )
        if (engine, path) not in self._expanded:
            self._queue.append((engine, path))
        return node_id

    async def drain(self) -> None:
        while self._queue:
            engine, path = self._queue.pop(0)
            if (engine, path) in self._expanded:
                continue
            self._expanded.add((engine, path))
            await self._expand(engine, path)

    async def _expand(self, engine: str, path: str) -> None:
        trace = (self.provenance.get(engine) or {}).get(path)
        node_id = _engine_node(engine, path)
        if trace is None:
            self.b.refuse(
                ReasonCode.NO_SOURCE,
                detail=(
                    f"{engine}.{path} is referenced by another traced value but "
                    "the run carries no trace for it."
                ),
                concept=node_id,
            )
            return
        formula = trace.get("formula")
        inputs = trace.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            # A pure leaf (no formula, no inputs) is a value read straight off
            # an input — ``capital.purchase_price`` is the canonical case. When
            # its output path names a canonical assumption, that is the link.
            if not formula:
                await self._link_leaf(engine, path, node_id)
            return
        for raw in inputs:
            if not isinstance(raw, dict):
                continue
            await self._link_input(engine, path, node_id, raw, formula)

    async def _link_leaf(self, engine: str, path: str, node_id: str) -> None:
        """Link a leaf traced value to the assumption its path names."""
        tail = path.rsplit(".", 1)[-1].split("[", 1)[0].strip().lower()
        mapped = _INPUT_ASSUMPTION_KEYS.get(tail)
        if mapped and (mapped in self.sources or mapped in self.base):
            await self._link_assumption(node_id, mapped, None)

    async def _link_input(
        self,
        engine: str,
        path: str,
        node_id: str,
        inp: Mapping[str, Any],
        formula: str | None,
    ) -> None:
        name = str(inp.get("name") or "").strip()
        linked = False

        # 1. an assumption the engine named outright.
        key = inp.get("assumption_key")
        if isinstance(key, str) and key:
            await self._link_assumption(node_id, key, formula)
            linked = True

        # 2. an explicit pointer at another traced value.
        ref = inp.get("traces_to")
        if isinstance(ref, str) and ref:
            targets = self._resolve_ref(engine, ref)
            if targets:
                for target_engine, target_path in targets:
                    self.b.edge(
                        node_id,
                        self._ensure_engine_node(target_engine, target_path),
                        "computed_from",
                        formula,
                    )
                linked = True
            else:
                self.b.refuse(
                    ReasonCode.NO_SOURCE,
                    detail=(
                        f"{engine}.{path} traces to {ref!r}, which is not a traced "
                        "value on this run."
                    ),
                    concept=node_id,
                )

        if linked or not name:
            return

        # 3. the input's name IS a canonical assumption for this deal.
        mapped = _INPUT_ASSUMPTION_KEYS.get(name.lower())
        if mapped and (mapped in self.sources or mapped in self.base):
            await self._link_assumption(node_id, mapped, formula)
            return

        # 4. a bridge the engine chain implements but does not assert.
        for target_engine, target_path in _bridge_targets(
            engine, path, name, last_year_index=self.last_year_index
        ):
            if target_path in (self.provenance.get(target_engine) or {}):
                self.b.edge(
                    node_id,
                    self._ensure_engine_node(target_engine, target_path),
                    "computed_from",
                    f"{name} {_BRIDGE_NOTE}",
                )

    def _resolve_ref(self, engine: str, ref: str) -> list[tuple[str, str]]:
        """Resolve a ``traces_to`` string to concrete traced values.

        ``"years[0].noi"`` is same-engine; ``"expense.years[0].noi"`` is
        cross-engine; ``"expense.years"`` is a PREFIX and expands to every
        traced key under it (the cash-flow view emits these).
        """
        from .engine_runner import ENGINE_NAMES

        head = ref.split(".", 1)[0].split("[", 1)[0]
        if head in ENGINE_NAMES and "." in ref:
            target_engine, target_path = head, ref.split(".", 1)[1]
        elif head in ENGINE_NAMES and head != engine:
            # A bare engine name ("returns") — the whole map is the target.
            target_engine, target_path = head, ""
        else:
            target_engine, target_path = engine, ref
        keys = self.provenance.get(target_engine) or {}
        if target_path and target_path in keys:
            return [(target_engine, target_path)]
        prefix_dot = f"{target_path}." if target_path else ""
        prefix_idx = f"{target_path}[" if target_path else ""
        matches = sorted(
            k
            for k in keys
            if not target_path
            or k.startswith(prefix_dot)
            or k.startswith(prefix_idx)
        )
        return [(target_engine, k) for k in matches[:_MAX_PREFIX_FANOUT]]

    # ── assumptions → evidence ───────────────────────────────────────

    async def _link_assumption(
        self, node_id: str, key: str, formula: str | None
    ) -> None:
        assumption_id = await self._ensure_assumption(key)
        self.b.edge(node_id, assumption_id, "seeded_from", formula)

    async def _ensure_assumption(self, key: str) -> str:
        assumption_id = _assumption_node(key)
        if key in self._assumptions_done:
            return assumption_id
        self._assumptions_done.add(key)

        label = self.sources.get(key)
        concept = _LABEL_CONCEPTS.get((key, label or "")) or _ASSUMPTION_CONCEPTS.get(key)
        reason = _coerce_reason(self.assumption_reasons.get(key))
        if reason is None and label in _REFUSAL_LABELS:
            reason = _REFUSAL_LABELS[label]
        self.b.node(
            LineageNode(
                id=assumption_id,
                kind="assumption",
                label=key,
                value=_as_value(self.base.get(key)),
                concept=concept,
                source=label,
                reason=reason,
                meta={"assumption_key": key},
            )
        )
        await self._ground_assumption(assumption_id, key, label, concept)
        return assumption_id

    async def _ground_assumption(
        self, assumption_id: str, key: str, label: str | None, concept: str | None
    ) -> None:
        """Walk one assumption down to an override, a seed, or a document page."""
        # An analyst override is terminal and carries the note it was saved with.
        override_entry = self.overrides_raw.get(key)
        if label in _OVERRIDE_LABELS or override_entry is not None:
            note = None
            actor = None
            at = None
            if isinstance(override_entry, dict):
                note = override_entry.get("note")
                actor = override_entry.get("overridden_by")
                at = override_entry.get("overridden_at")
            override_id = self.b.node(
                LineageNode(
                    id=_override_node(key),
                    kind="override",
                    label=f"Analyst override — {key}",
                    value=_as_value(self.base.get(key)),
                    concept=concept,
                    source=label or "analyst_override",
                    meta={
                        k: v
                        for k, v in (
                            ("note", note),
                            ("overridden_by", actor),
                            ("overridden_at", at),
                        )
                        if v is not None
                    },
                )
            )
            self.b.edge(assumption_id, override_id, "overridden_by", None)
            return

        # The runner told us exactly which extraction row produced the value.
        sf = self.source_fields.get(key)
        if isinstance(sf, dict) and sf.get("document_id"):
            self._attach_source_field(assumption_id, key, sf, concept)
            return

        # A benchmark / market feed gets its own terminal node; when the feed
        # arrived as an uploaded document we keep walking to that document.
        anchor = assumption_id
        if label in _BENCHMARK_LABELS:
            anchor = self.b.node(
                LineageNode(
                    id=_benchmark_node(key),
                    kind="benchmark",
                    label=f"{label} — {key}",
                    value=_as_value(self.base.get(key)),
                    concept=concept,
                    source=label,
                    reason=self._terminal_reason(),
                    meta={"assumption_key": key},
                )
            )
            self.b.edge(assumption_id, anchor, "seeded_from", None)

        document_id = self.source_documents.get(key)
        if document_id and document_id in self.documents:
            await self._attach_document(anchor, key, document_id, concept)
            return

        if label in _DOCUMENT_LABELS:
            # The label claims a document but none resolved — say so.
            self.b.refuse(
                self._terminal_reason(),
                detail=(
                    f"assumption {key!r} is labelled {label!r} but no uploaded "
                    "document on this deal resolves to it."
                ),
                concept=concept or _assumption_node(key),
            )
            return

        if anchor != assumption_id:
            return  # benchmark node already terminates the chain

        # Everything else — a seed, the deal record, a derived default — is a
        # terminal node carrying the reason it never reaches a page.
        seed_id = self.b.node(
            LineageNode(
                id=_seed_node(key),
                kind="seed",
                label=f"{label or 'seed'} — {key}",
                value=_as_value(self.base.get(key)),
                concept=concept,
                source=label or "seed",
                reason=self._terminal_reason(),
                meta={"assumption_key": key},
            )
        )
        self.b.edge(assumption_id, seed_id, "seeded_from", None)

    def _terminal_reason(self) -> ReasonCode:
        """Why a terminal node has no page: nothing uploaded, or nothing matched."""
        return ReasonCode.NO_DOCUMENT if not self.documents else ReasonCode.NO_SOURCE

    def _attach_source_field(
        self,
        assumption_id: str,
        key: str,
        sf: Mapping[str, Any],
        concept: str | None,
    ) -> None:
        """Consume the runner's ``__source_fields__`` entry for ``key``."""
        document_id = str(sf.get("document_id"))
        extraction_result_id = str(sf.get("extraction_result_id") or "unknown")
        field_name = str(sf.get("field_name") or "")
        page = sf.get("source_page")
        doc_meta = self.documents.get(document_id, {})
        field_id = self.b.node(
            LineageNode(
                id=_field_node(extraction_result_id, field_name or key),
                kind="extracted_field",
                label=field_name or key,
                value=_as_value(self.base.get(key)),
                concept=sf.get("concept") or concept,
                source=self.sources.get(key),
                meta={
                    k: v
                    for k, v in (
                        ("extraction_result_id", extraction_result_id),
                        ("document_id", document_id),
                        ("field_name", field_name),
                        ("scope", sf.get("scope")),
                        ("basis", sf.get("basis")),
                        ("doc_type", sf.get("doc_type") or doc_meta.get("doc_type")),
                        ("as_of", sf.get("as_of")),
                        ("via", "__source_fields__"),
                    )
                    if v is not None
                },
            )
        )
        self.b.edge(assumption_id, field_id, "extracted_from", None)
        doc_id = self._ensure_document(document_id)
        self.b.edge(field_id, doc_id, "extracted_from", None)
        if isinstance(page, int) and page >= 1:
            self.b.edge(doc_id, self._ensure_page(document_id, page), "located_on", None)
        else:
            self.b.refuse(
                ReasonCode.NO_SOURCE,
                detail=(
                    f"assumption {key!r} resolves to field {field_name!r} on document "
                    f"{document_id} but the extraction carries no page number."
                ),
                concept=concept or _assumption_node(key),
                document_id=_maybe_uuid(document_id),
            )

    async def _attach_document(
        self, anchor_id: str, key: str, document_id: str, concept: str | None
    ) -> None:
        """Fallback path — resolve the field + page ourselves via the registry."""
        doc_meta = self.documents.get(document_id, {})
        extraction = await self._extraction(document_id)
        if extraction is None:
            doc_id = self._ensure_document(document_id)
            self.b.edge(anchor_id, doc_id, "extracted_from", None)
            self.b.refuse(
                ReasonCode.NO_SOURCE,
                detail=(
                    f"assumption {key!r} points at document {document_id} but that "
                    "document has no readable extraction."
                ),
                concept=concept or _assumption_node(key),
                document_id=_maybe_uuid(document_id),
            )
            return
        extraction_result_id, fields = extraction
        resolution = _resolve_concept(
            fields, concept, doc_type=doc_meta.get("doc_type")
        )
        doc_id = self._ensure_document(document_id)
        if resolution is None or not resolution.get("field_name"):
            self.b.edge(anchor_id, doc_id, "extracted_from", None)
            code = ReasonCode.NO_SOURCE
            if resolution is not None:
                code = _coerce_reason(resolution.get("reason")) or ReasonCode.NO_SOURCE
            self.b.refuse(
                code,
                detail=(
                    f"assumption {key!r} points at document {document_id} but no "
                    f"field on it resolves to concept {concept or '(none declared)'}."
                ),
                concept=concept or _assumption_node(key),
                document_id=_maybe_uuid(document_id),
            )
            return

        field_name = str(resolution["field_name"])
        field_id = self.b.node(
            LineageNode(
                id=_field_node(extraction_result_id, field_name),
                kind="extracted_field",
                label=field_name,
                value=_as_value(resolution.get("value")),
                unit=resolution.get("unit"),
                concept=concept,
                source=self.sources.get(key),
                meta={
                    k: v
                    for k, v in (
                        ("extraction_result_id", extraction_result_id),
                        ("document_id", document_id),
                        ("field_name", field_name),
                        ("scope", resolution.get("scope")),
                        ("basis", resolution.get("basis")),
                        ("doc_type", doc_meta.get("doc_type")),
                        ("confidence", resolution.get("confidence")),
                        ("via", "concept_registry"),
                    )
                    if v is not None
                },
            )
        )
        self.b.edge(anchor_id, field_id, "extracted_from", None)
        self.b.edge(field_id, doc_id, "extracted_from", None)
        page = resolution.get("source_page")
        if isinstance(page, int) and page >= 1:
            self.b.edge(doc_id, self._ensure_page(document_id, page), "located_on", None)
        else:
            self.b.refuse(
                ReasonCode.NO_SOURCE,
                detail=(
                    f"assumption {key!r} resolves to field {field_name!r} on document "
                    f"{document_id} but the extraction carries no page number."
                ),
                concept=concept or _assumption_node(key),
                document_id=_maybe_uuid(document_id),
            )

    async def _extraction(
        self, document_id: str
    ) -> tuple[str, list[dict[str, Any]]] | None:
        if document_id not in self._extraction_cache:
            self._extraction_cache[document_id] = await _load_extraction_for_document(
                self.session, document_id=document_id, tenant_id=self.tenant_id
            )
        return self._extraction_cache[document_id]

    def _ensure_document(self, document_id: str) -> str:
        meta = self.documents.get(document_id, {})
        return self.b.node(
            LineageNode(
                id=_document_node(document_id),
                kind="document",
                label=str(meta.get("filename") or document_id),
                meta={
                    k: v
                    for k, v in (
                        ("document_id", document_id),
                        ("doc_type", meta.get("doc_type")),
                        ("status", meta.get("status")),
                        ("page_count", meta.get("page_count")),
                        ("uploaded_at", _iso(meta.get("uploaded_at"))),
                    )
                    if v is not None
                },
            )
        )

    def _ensure_page(self, document_id: str, page: int) -> str:
        meta = self.documents.get(document_id, {})
        filename = str(meta.get("filename") or document_id)
        return self.b.node(
            LineageNode(
                id=_page_node(document_id, page),
                kind="page",
                label=f"{filename} · p.{page}",
                meta={"document_id": document_id, "page": page},
            )
        )

    # ── memo ─────────────────────────────────────────────────────────

    async def attach_memo(self) -> None:
        """Link the latest memo's sections to the evidence they cite.

        The memo lives in the process-local :class:`MemoCache` (there is no
        ``memo_sections`` table yet), so this is best-effort: no memo drafted
        in this process simply means no ``memo:`` nodes.
        """
        try:
            from ..streaming.broadcast import get_memo_cache

            snapshot = await get_memo_cache().get(self.deal_id)
        except Exception:  # pragma: no cover - defensive
            return
        if not isinstance(snapshot, dict):
            return
        sections = snapshot.get("sections")
        if not isinstance(sections, list):
            return
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("section_id") or "").strip()
            if not section_id:
                continue
            citations = section.get("citations")
            if not isinstance(citations, list) or not citations:
                continue
            memo_id = self.b.node(
                LineageNode(
                    id=_memo_node(section_id),
                    kind="memo_section",
                    label=str(section.get("title") or section_id),
                    meta={"section_id": section_id},
                )
            )
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                document_id = str(citation.get("document_id") or "")
                # A citation must point at a document ON THIS DEAL — never
                # trust a section body to widen the tenant scope.
                if document_id not in self.documents:
                    self.b.refuse(
                        ReasonCode.NO_DOCUMENT,
                        detail=(
                            f"memo section {section_id!r} cites document "
                            f"{document_id or '(none)'}, which is not on this deal."
                        ),
                        concept=memo_id,
                        document_id=_maybe_uuid(document_id),
                    )
                    continue
                doc_id = self._ensure_document(document_id)
                self.b.edge(doc_id, memo_id, "cited_in", None)
                page = citation.get("page")
                if isinstance(page, int) and page >= 1:
                    page_id = self._ensure_page(document_id, page)
                    self.b.edge(doc_id, page_id, "located_on", None)
                    self.b.edge(page_id, memo_id, "cited_in", None)

    # ── refusals ─────────────────────────────────────────────────────

    def record_unreached(self, roots: Sequence[str]) -> None:
        """Every root / assumption that does not reach a page states why."""
        for root in roots:
            node = self.b.nodes.get(root)
            if node is None:
                continue
            if "page" in self.b.reachable_kinds(root):
                continue
            self.b.refuse(
                self._terminal_reason(),
                detail=(
                    f"{node.label} does not reach a document page — every path from "
                    "it ends at a seed, a benchmark, an analyst override or an "
                    "untraced value."
                ),
                # The node id, not the concept: a refusal is about ONE step, and
                # a consumer attaches it by matching either.
                concept=node.id,
            )
        for key in sorted(self._assumptions_done):
            assumption_id = _assumption_node(key)
            node = self.b.nodes.get(assumption_id)
            if node is None:
                continue
            if "page" in self.b.reachable_kinds(assumption_id):
                continue
            source = node.source or "seed"
            self.b.refuse(
                node.reason or self._terminal_reason(),
                detail=(
                    f"assumption {key!r} ({source}) does not reach a document page."
                ),
                concept=node.id,
            )


# ───────────────────────────── small helpers ──────────────────────────


def _as_value(value: Any) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return value
    return None


def _coerce_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _iso(value: Any) -> str | None:
    parsed = _parse_ts(value)
    return parsed.isoformat() if parsed else None


def _maybe_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


_STATES: frozenset[str] = frozenset(
    {
        "document_sourced",
        "linked",
        "assumption",
        "calculated",
        "awaiting_data",
        "needs_review",
    }
)


def _coerce_state(value: Any) -> str | None:
    """Only a known FON-65 state reaches a node — an unknown string is dropped
    rather than failing the whole record's validation."""
    return value if isinstance(value, str) and value in _STATES else None


def _coerce_reason(value: Any) -> ReasonCode | None:
    if isinstance(value, ReasonCode):
        return value
    if isinstance(value, str) and value:
        try:
            return ReasonCode(value)
        except ValueError:
            return None
    return None


def _resolve_concept(
    fields: list[dict[str, Any]], concept: str | None, *, doc_type: str | None
) -> dict[str, Any] | None:
    """Ask the concept registry which field carries ``concept``, and where.

    Returns a plain dict (never a registry type) so this module stays the only
    place that knows the registry's shape. ``None`` when no concept is
    declared for the assumption or the registry does not know it.
    """
    if not concept:
        return None
    try:
        from ..ontology.registry import resolve as registry_resolve
    except Exception:  # pragma: no cover - a broken registry fails /health first
        return None
    last: dict[str, Any] | None = None
    for want in ("annual", "ttm", "unknown"):
        try:
            res = registry_resolve(fields, concept, doc_type=doc_type, want=want)
        except KeyError:
            return None
        except Exception:  # pragma: no cover - defensive
            return None
        if res.field_name:
            return {
                "field_name": res.field_name,
                "value": res.value,
                "unit": res.unit,
                "source_page": res.source_page,
                "scope": res.scope,
                "basis": res.basis,
                "confidence": res.confidence,
                "reason": res.reason,
            }
        last = {"field_name": None, "reason": res.reason}
    return last


# ──────────────────────────── persistence ─────────────────────────────


async def persist_for_run(
    session: AsyncSession,
    deal_id: UUID | str,
    tenant_id: UUID | str,
    run_id: UUID | str,
) -> LineageRecord | None:
    """Build the lineage for ``run_id`` and store it in ``lineage_records``.

    Called by the engine runner at the end of a full chain, inside its own
    ``try/except`` — so this function never raises: a failure here must not
    fail a model run. Returns the record it stored, or ``None``.

    Re-persisting the same ``(deal_id, run_id)`` replaces the stored row, so a
    re-run is idempotent rather than accumulating snapshots.
    """
    try:
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)
    except Exception:
        logger.exception(
            "lineage: build failed for deal=%s run=%s", deal_id, run_id
        )
        return None
    try:
        await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                DELETE FROM lineage_records
                 WHERE deal_id = :deal AND tenant_id = :tenant AND run_id = :run
                """
            ),
            {"deal": str(deal_id), "tenant": str(tenant_id), "run": str(run_id)},
        )
        await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                INSERT INTO lineage_records (
                    id, deal_id, tenant_id, run_id, registry_version, record, stale
                ) VALUES (
                    :id, :deal, :tenant, :run, :registry_version, :record, :stale
                )
                """
            ),
            {
                "id": str(uuid4()),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
                "run": str(run_id),
                "registry_version": record.registry_version,
                "record": record.model_dump_json(),
                "stale": record.stale,
            },
        )
        await session.commit()
    except Exception:
        logger.exception(
            "lineage: persist failed for deal=%s run=%s", deal_id, run_id
        )
        with contextlib.suppress(Exception):  # pragma: no cover - defensive
            await session.rollback()
        return None
    return record


async def load_persisted(
    session: AsyncSession,
    *,
    deal_id: UUID | str,
    tenant_id: UUID | str,
    run_id: UUID | str,
) -> LineageRecord | None:
    """Return the stored record for ``(deal_id, run_id)``, or ``None``.

    ``stale`` is recomputed on read: a document uploaded after the record was
    persisted makes it stale, and an analyst reading the endpoint has to see
    that even though the stored row still says otherwise.
    """
    try:
        row = (
            await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    """
                    SELECT record FROM lineage_records
                     WHERE deal_id = :deal AND tenant_id = :tenant AND run_id = :run
                     ORDER BY created_at DESC
                     LIMIT 1
                    """
                ),
                {"deal": str(deal_id), "tenant": str(tenant_id), "run": str(run_id)},
            )
        ).first()
    except Exception:
        return None
    if row is None:
        return None
    raw = row._mapping.get("record")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        record = LineageRecord.model_validate(raw)
    except Exception:
        return None

    from .engine_runner import get_run_status

    try:
        rows = await get_run_status(
            session,
            deal_id=str(deal_id),
            run_id=str(run_id),
            tenant_id=str(tenant_id),
        )
    except Exception:
        return record
    run_started: datetime | None = None
    for r in rows:
        started = _parse_ts(r.get("started_at"))
        if started is not None and (run_started is None or started < run_started):
            run_started = started
    documents = await _load_documents(
        session, deal_id=str(deal_id), tenant_id=str(tenant_id)
    )
    deal_updated = await _deal_updated_at(
        session, deal_id=str(deal_id), tenant_id=str(tenant_id)
    )
    record.stale = _is_stale(
        run_started=run_started, documents=documents, deal_updated=deal_updated
    )
    return record


__all__ = ["build_lineage", "load_persisted", "persist_for_run"]
