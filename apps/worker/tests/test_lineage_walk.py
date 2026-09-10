"""Phase 2.3 — the evidence chain is walkable end to end.

Seeds a real deal (the Anglers T-12 + OM extraction payloads that shipped
from production), runs the full engine chain, then walks the lineage the
way an analyst — or an auditor — would:

    kpi:returns.levered_irr → … → assumption:starting_occupancy
        → field:<extraction_result_id>:<field_name> → doc:<id> → page:<id>:4

The four contracts these tests pin:

1. **The chain closes.** A headline KPI reaches a page of an uploaded
   document, through the engine traces and the assumption that grounds them.
2. **Nothing is silently dropped.** Every entry in ``unresolved`` carries a
   ``ReasonCode`` and says which link broke — a seed, a benchmark, an
   analyst override or an untraced value.
3. **Staleness is honest.** A document uploaded after the run flips
   ``stale``; the record still serves (so an analyst can see what the
   numbers *were* grounded in) but never claims to describe today.
4. **Tenancy holds.** A foreign tenant 404s on the endpoint, exactly like
   ``/provenance``.
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

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-lineage-walk.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_FIXTURES = Path(__file__).parent / "fixtures"
_T12_PAYLOAD = _FIXTURES / "real_payloads" / "anglers_t12_real.json"
_OM_PAYLOAD = _FIXTURES / "usali_v4" / "live_extraction_anglers_om.json"


def _fields(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    fields = payload.get("fields") if isinstance(payload, dict) else payload
    return [f for f in (fields or []) if isinstance(f, dict)]


@pytest.fixture(autouse=True)
async def _reset_db():
    """Recreate + clear the schema before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "lineage_records",
            "engine_outputs",
            "extraction_results",
            "documents",
            "scenarios",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


async def _seed_deal(session, *, deal_id: str, tenant_id: str) -> None:
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, keys, field_overrides) "
            "VALUES (:id, :tenant, :name, :keys, :fo)"
        ),
        {
            "id": deal_id,
            "tenant": tenant_id,
            "name": "Anglers Lineage Test",
            "keys": 132,
            "fo": "{}",
        },
    )


async def _seed_document(
    session,
    *,
    deal_id: str,
    tenant_id: str,
    filename: str,
    doc_type: str,
    fields: list[dict],
    uploaded_at: str | None = None,
) -> str:
    doc_id = str(uuid4())
    if uploaded_at is None:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                "doc_type, status) VALUES (:id, :deal, :tenant, :f, :dt, 'Extracted')"
            ),
            {"id": doc_id, "deal": deal_id, "tenant": tenant_id, "f": filename, "dt": doc_type},
        )
    else:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                "doc_type, status, uploaded_at) "
                "VALUES (:id, :deal, :tenant, :f, :dt, 'Extracted', :up)"
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "f": filename,
                "dt": doc_type,
                "up": uploaded_at,
            },
        )
    await session.execute(
        text(
            "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields) "
            "VALUES (:id, :doc, :deal, :tenant, :fields)"
        ),
        {
            "id": str(uuid4()),
            "doc": doc_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "fields": json.dumps(fields),
        },
    )
    return doc_id


async def _seed_and_run(session, *, deal_id: str, tenant_id: str, run_id: str) -> None:
    """A real deal: T-12 + OM uploaded, then the full engine chain."""
    from app.services.engine_runner import run_all_engines

    await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
    await _seed_document(
        session,
        deal_id=deal_id,
        tenant_id=tenant_id,
        filename="Anglers_T12.xlsx",
        doc_type="T12",
        fields=_fields(_T12_PAYLOAD),
    )
    await _seed_document(
        session,
        deal_id=deal_id,
        tenant_id=tenant_id,
        filename="Anglers_OM.pdf",
        doc_type="OM",
        fields=_fields(_OM_PAYLOAD),
    )
    await session.commit()
    await run_all_engines(
        session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
    )
    await session.commit()


def _index(record):
    return {n.id: n for n in record.nodes}


def _walk(record, start: str) -> dict[str, list[str]]:
    """Shortest path (as a node-id list) from ``start`` to every reachable node."""
    adjacency: dict[str, list[str]] = {}
    for e in record.edges:
        adjacency.setdefault(e.src, []).append(e.dst)
    paths = {start: [start]}
    queue = [start]
    while queue:
        current = queue.pop(0)
        for nxt in adjacency.get(current, ()):
            if nxt in paths:
                continue
            paths[nxt] = paths[current] + [nxt]
            queue.append(nxt)
    return paths


# ─────────────────────── 1. the chain closes ──────────────────────────


@pytest.mark.asyncio
async def test_levered_irr_walks_to_a_document_page() -> None:
    """The headline KPI reaches a page of the uploaded T-12, via the
    starting_occupancy assumption the T-12 grounded."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    assert str(record.run_id) == run_id
    assert record.registry_version > 0
    nodes = _index(record)

    root = "kpi:returns.levered_irr"
    assert root in record.roots, f"roots were {record.roots}"
    paths = _walk(record, root)

    assumption = "assumption:starting_occupancy"
    assert assumption in paths, (
        "levered IRR does not reach starting_occupancy; reachable assumptions: "
        f"{sorted(n for n in paths if n.startswith('assumption:'))}"
    )
    # The assumption IS the T-12's — not a seed that happens to be named the same.
    assert nodes[assumption].source == "t12_actual"
    assert nodes[assumption].concept == "occupancy"

    # … → field: → doc: → page:, in that order, all downstream of the assumption.
    from_assumption = _walk(record, assumption)
    field_ids = [n for n in from_assumption if n.startswith("field:")]
    assert field_ids, "the assumption never reaches an extracted field"
    field_id = field_ids[0]
    assert nodes[field_id].meta["field_name"] == "ttm_summary_per_om.occupancy_pct"

    from_field = _walk(record, field_id)
    doc_ids = [n for n in from_field if n.startswith("doc:")]
    assert doc_ids, "the extracted field never reaches its document"
    doc_id = doc_ids[0]
    assert nodes[doc_id].label == "Anglers_T12.xlsx"

    from_doc = _walk(record, doc_id)
    page_ids = [n for n in from_doc if n.startswith("page:")]
    assert page_ids, "the document never reaches the page the value sits on"
    assert nodes[page_ids[0]].meta["page"] == 4

    # The whole chain in one ordered walk, which is the promise of the phase.
    full = paths[page_ids[0]]
    order = [full.index(x) for x in (root, assumption, field_id, doc_id, page_ids[0])]
    assert order == sorted(order), f"chain out of order: {full}"


@pytest.mark.asyncio
async def test_every_kpi_root_is_present_and_typed() -> None:
    """Every headline number the deal is judged on is a root, and each root
    node carries its value + unit rather than a bare id."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    expected = {
        "kpi:returns.levered_irr",
        "kpi:returns.unlevered_irr",
        "kpi:returns.equity_multiple",
        "kpi:returns.year_one_coc",
        "kpi:debt.min_dscr",
        "kpi:expense.year_one_noi",
    }
    assert expected <= set(record.roots), f"missing roots: {expected - set(record.roots)}"
    nodes = _index(record)
    for root in expected:
        node = nodes[root]
        assert node.kind == "kpi"
        assert node.value is not None, f"{root} has no value"
        assert node.unit, f"{root} has no unit"


@pytest.mark.asyncio
async def test_year_one_noi_walks_to_the_revenue_assumptions() -> None:
    """Year-1 NOI reaches BOTH revenue anchors — the T-12 occupancy and the
    T-12 ADR — through the revenue chain the engines now assert.

    The hop the expense engine asserts is ``fb.years[0].total_revenue``, NOT
    ``revenue.years[0].total_revenue``: ``ExpenseEngineInput.revenue`` is the
    F&B engine's output, and the F&B engine re-composes total revenue (resort
    fees, ratio fall-backs) on top of the revenue projection. The lineage
    bridge this replaces had been pointing one engine too far up — a
    plausible-looking inference that skipped a real step in the chain.
    """
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    paths = _walk(record, "kpi:expense.year_one_noi")
    assert "engine:fb.years[0].total_revenue" in paths
    assert "engine:revenue.years[0].rooms_revenue" in paths
    assert "assumption:starting_occupancy" in paths
    assert "assumption:starting_adr" in paths
    # And the hop onto the revenue side is the engine's own claim, not an
    # inference — it used to be a bridge.
    gop_to_revenue = [
        e
        for e in record.edges
        if e.src == "engine:expense.years[0].gop"
        and e.dst == "engine:fb.years[0].total_revenue"
    ]
    assert gop_to_revenue, "expense GOP no longer links to the F&B total"
    assert gop_to_revenue[0].meta["link"] == "traces_to"


@pytest.mark.asyncio
async def test_om_grounded_exit_cap_walks_to_the_offering_memorandum() -> None:
    """When the OM's comparable-sales table anchors the exit cap, the walk
    lands on THAT table's page — not on the OM's own pro-forma line."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())
    # The extractor emits transaction_comps.<n>.cap_rate_pct; three or more
    # comps make the median the deal's exit-cap anchor.
    comps = _fields(_OM_PAYLOAD) + [
        {"field_name": f"transaction_comps.{n}.cap_rate_pct", "value": rate, "source_page": 7}
        for n, rate in ((1, 0.062), (2, 0.065), (3, 0.068))
    ]

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_T12.xlsx",
            doc_type="T12",
            fields=_fields(_T12_PAYLOAD),
        )
        await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_OM.pdf",
            doc_type="OM",
            fields=comps,
        )
        await session.commit()
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        await session.commit()
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    nodes = _index(record)
    exit_cap = nodes["assumption:exit_cap_rate"]
    assert exit_cap.source == "om_comps"
    assert exit_cap.concept == "comp_cap_rate", (
        "the OM-anchored exit cap is the comps table's cap rate, not the OM's "
        "own exit-cap line"
    )
    downstream = _walk(record, "assumption:exit_cap_rate")
    docs = [nodes[n] for n in downstream if n.startswith("doc:")]
    assert [d.label for d in docs] == ["Anglers_OM.pdf"]
    pages = [nodes[n] for n in downstream if n.startswith("page:")]
    assert [p.meta["page"] for p in pages] == [7]


# ────────────────── 2. nothing is silently dropped ────────────────────


@pytest.mark.asyncio
async def test_every_unresolved_entry_carries_a_reason() -> None:
    """A link that cannot be walked to a page is reported, with its code and
    enough detail to act on — never dropped."""
    from fondok_schemas.reasons import ReasonCode

    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    assert record.unresolved, (
        "a deal underwritten from two documents still leans on seeds "
        "(exit cap, LTV, hold) — those must be reported, not hidden"
    )
    for refusal in record.unresolved:
        assert isinstance(refusal.code, ReasonCode)
        assert refusal.detail, f"{refusal.code} was reported with no detail"

    # The seeds ARE named: an ungrounded assumption says so by key.
    details = " ".join(r.detail or "" for r in record.unresolved)
    assert "exit_cap_rate" in details


@pytest.mark.asyncio
async def test_seed_assumptions_terminate_at_a_seed_node_with_a_reason() -> None:
    """An assumption no document grounds ends at a ``seed:`` node carrying the
    reason it never reaches a page."""
    from fondok_schemas.reasons import ReasonCode

    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    nodes = _index(record)
    seed_id = "seed:exit_cap_rate"
    assert seed_id in nodes, f"seed nodes present: {[n for n in nodes if n.startswith('seed:')]}"
    seed = nodes[seed_id]
    assert seed.kind == "seed"
    assert seed.reason in (ReasonCode.NO_SOURCE, ReasonCode.NO_DOCUMENT)
    assert seed.value is not None


@pytest.mark.asyncio
async def test_analyst_override_is_a_terminal_node_carrying_the_note() -> None:
    """An overridden assumption stops at ``override:<key>`` and the analyst's
    justification note travels with it."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())
    overrides = {
        "exit_cap_rate": {
            "value": 0.065,
            "note": "Broker guidance + two 2025 Keys trades at 6.4-6.6%.",
            "overridden_by": "analyst@fondok.app",
        }
    }

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        await session.execute(
            text("UPDATE deals SET field_overrides = :fo WHERE id = :id"),
            {"fo": json.dumps(overrides), "id": deal_id},
        )
        await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_T12.xlsx",
            doc_type="T12",
            fields=_fields(_T12_PAYLOAD),
        )
        await session.commit()
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        await session.commit()
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    nodes = _index(record)
    assert nodes["assumption:exit_cap_rate"].source == "analyst_override"
    override = nodes["override:exit_cap_rate"]
    assert override.kind == "override"
    assert override.meta["note"].startswith("Broker guidance")
    assert override.meta["overridden_by"] == "analyst@fondok.app"
    assert override.value == pytest.approx(0.065)


# ───────────────────────── 3. staleness ───────────────────────────────


@pytest.mark.asyncio
async def test_stale_flips_when_a_document_lands_after_the_run() -> None:
    """A fresh record is not stale; upload a document after the run and the
    same walk says so."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        fresh = await build_lineage(session, deal_id, tenant_id, run_id=run_id)
        assert fresh.stale is False, "nothing moved — the record cannot be stale"

        later = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
        await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_Q3_update.pdf",
            doc_type="PNL_YTD",
            fields=[{"field_name": "p_and_l_usali.period_type", "value": "ytd"}],
            uploaded_at=later,
        )
        await session.commit()
        after = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    assert after.stale is True, "a document uploaded after the run must flip stale"


# ─────────────────── 4. persistence + the endpoint ────────────────────


@pytest.mark.asyncio
async def test_persist_for_run_round_trips_and_is_idempotent() -> None:
    """``persist_for_run`` is what the engine runner calls; it stores exactly
    one row per (deal, run) and the stored record reads back intact."""
    from app.database import get_session_factory
    from app.services.lineage import load_persisted, persist_for_run

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        stored = await persist_for_run(session, deal_id, tenant_id, run_id)
        assert stored is not None
        # A re-run must replace, not accumulate.
        await persist_for_run(session, deal_id, tenant_id, run_id)
        count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM lineage_records "
                    "WHERE deal_id = :d AND run_id = :r"
                ),
                {"d": deal_id, "r": run_id},
            )
        ).scalar_one()
        assert count == 1

        loaded = await load_persisted(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )

    assert loaded is not None
    assert {n.id for n in loaded.nodes} == {n.id for n in stored.nodes}
    assert "kpi:returns.levered_irr" in loaded.roots


# ────────── 5. the contracts the Phase 2.4 web drawer reads ───────────


@pytest.mark.asyncio
async def test_edges_descend_from_the_root_src_to_dst() -> None:
    """Edges read "src ⟨rel⟩ dst" and descend: following edges whose ``src``
    is the current node walks a root DOWN to its evidence, one step per level.
    Inverting them would collapse the drawer to a single childless step."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    root = "kpi:returns.levered_irr"
    # A root is never a destination, and always has at least one child.
    assert all(e.dst != root for e in record.edges)
    assert any(e.src == root for e in record.edges)
    # A page is a leaf of the descent — nothing downstream of it.
    pages = {n.id for n in record.nodes if n.kind == "page"}
    assert pages
    assert all(e.src not in pages for e in record.edges if e.rel != "cited_in")
    # And the descent really is nested — the walk from the root is deeper
    # than one hop.
    depth = max(len(p) for p in _walk(record, root).values())
    assert depth >= 5, f"chain is only {depth} nodes deep"


@pytest.mark.asyncio
async def test_every_refusal_names_the_node_it_belongs_to() -> None:
    """A consumer attaches each refusal to a step by matching ``concept``
    against a node's concept or its id — so every refusal must carry one."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    ids = {n.id for n in record.nodes}
    concepts = {n.concept for n in record.nodes if n.concept}
    for refusal in record.unresolved:
        assert refusal.concept, f"{refusal.code} names no node"
        assert refusal.concept in ids or refusal.concept in concepts, (
            f"refusal concept {refusal.concept!r} matches no node id or concept"
        )


@pytest.mark.asyncio
async def test_assumption_sources_exposes_source_fields_aliases_and_bare_reasons() -> None:
    """The two additive blocks match what the web reads: ``source_fields``
    entries carry ``field`` / ``page`` / ``filename`` alongside the runner's
    own spellings, and ``reasons`` values are bare ``ReasonCode`` strings.

    The engine-runner side of ``__source_fields__`` is landing in parallel, so
    this pins the endpoint's contract by injecting the sidecar the runner will
    emit — the response shape cannot drift from the web fixture either way.
    """
    from fondok_schemas.reasons import ReasonCode

    from app.api.deals import get_assumption_sources
    from app.database import get_session_factory
    from app.services import engine_runner

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        doc_id = (
            await session.execute(
                text("SELECT id FROM documents WHERE deal_id = :d AND doc_type = 'T12'"),
                {"d": deal_id},
            )
        ).scalar_one()
        er_id = (
            await session.execute(
                text("SELECT id FROM extraction_results WHERE document_id = :d"),
                {"d": doc_id},
            )
        ).scalar_one()

        real_loader = engine_runner._load_engine_inputs

        async def _with_sidecars(*args, **kwargs):
            base = await real_loader(*args, **kwargs)
            base["__source_fields__"] = {
                "starting_occupancy": {
                    "document_id": str(doc_id),
                    "extraction_result_id": str(er_id),
                    "field_name": "ttm_summary_per_om.occupancy_pct",
                    "source_page": 4,
                    "concept": "occupancy",
                    "scope": "ttm",
                    "basis": "actual",
                    "doc_type": "T12",
                    "as_of": "2025-05-31",
                }
            }
            base["__reasons__"] = {"exit_cap_rate": ReasonCode.NO_SOURCE}
            return base

        engine_runner._load_engine_inputs = _with_sidecars
        try:
            response = await get_assumption_sources(
                UUID(deal_id), session, UUID(tenant_id)
            )
        finally:
            engine_runner._load_engine_inputs = real_loader

    entry = response.source_fields["starting_occupancy"]
    # Display aliases the web reads …
    assert entry["field"] == "ttm_summary_per_om.occupancy_pct"
    assert entry["page"] == 4
    assert entry["filename"] == "Anglers_T12.xlsx"
    assert entry["document_id"] == str(doc_id)
    # … alongside the runner's own spellings, untouched.
    assert entry["field_name"] == "ttm_summary_per_om.occupancy_pct"
    assert entry["source_page"] == 4
    assert entry["extraction_result_id"] == str(er_id)
    # Bare code strings, never a Refusal object.
    assert response.reasons == {"exit_cap_rate": "no_source"}
    # And nothing pre-existing moved.
    assert response.sources["starting_occupancy"] == "t12_actual"
    assert response.source_documents["starting_occupancy"] == str(doc_id)


@pytest.mark.asyncio
async def test_lineage_endpoint_serves_the_canonical_run_and_404s_a_foreign_tenant() -> None:
    """Tenant scoping matches ``/provenance`` exactly: a foreign tenant 404s
    rather than seeing another firm's evidence chain."""
    from fastapi import HTTPException

    from app.api.deals import get_deal_lineage
    from app.database import get_session_factory

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await get_deal_lineage(UUID(deal_id), session, UUID(tenant_id))
        assert str(record.run_id) == run_id
        assert "kpi:returns.levered_irr" in record.roots

        with pytest.raises(HTTPException) as exc:
            await get_deal_lineage(UUID(deal_id), session, uuid4())
    assert exc.value.status_code == 404


# ────── 6. asserted vs inferred, and where every chain ends ──────────
#
# The engines now name the assumption behind a value themselves. What is left
# for the lineage service to infer is measurable — and must keep shrinking.

#: Bridge (inferred, dependency-graph) links on this exact seeded deal BEFORE
#: the engines asserted their own links, measured on 6787202. The engines'
#: assertions retired the expense→revenue, debt→expense NOI and returns→
#: capital/expense bridges; what survives is only the multi-target links no
#: single ``traces_to`` can express (a cash flow is NOI *less* debt service).
_BRIDGE_LINKS_BEFORE = 38


def _link_counts(record) -> dict[str, int]:
    counts = {"asserted": 0, "traces_to": 0, "name_match": 0, "bridge": 0}
    for edge in record.edges:
        kind = (edge.meta or {}).get("link")
        if kind in counts:
            counts[kind] += 1
    return counts


@pytest.mark.asyncio
async def test_link_provenance_summary_matches_the_edges_it_describes() -> None:
    """``meta.link_provenance`` is the record's honesty tally, and it ties out
    to the edges — a summary that could drift from the graph is worthless."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    summary = record.meta["link_provenance"]
    assert set(summary) == {"asserted", "traces_to", "name_match", "bridge"}
    assert summary == _link_counts(record)
    assert record.meta["links_asserted"] == summary["asserted"] + summary["traces_to"]
    assert record.meta["links_inferred"] == summary["name_match"] + summary["bridge"]
    # The engines carry the chain; inference is the minority.
    assert summary["asserted"] > 0
    assert summary["traces_to"] > 0
    assert record.meta["links_asserted"] > record.meta["links_inferred"]
    # Every link edge says which of the four it is; no untagged provenance hop.
    for edge in record.edges:
        if edge.rel in ("computed_from", "seeded_from") and edge.src.startswith(
            "engine:"
        ):
            assert (edge.meta or {}).get("link") in summary, (
                f"{edge.src} -> {edge.dst} does not say how it was established"
            )


@pytest.mark.asyncio
async def test_inferred_links_strictly_decrease_on_the_seeded_deal() -> None:
    """The bridges the engines made unnecessary are gone, not left as dead
    fallback — and nothing is resolved by name-matching any more."""
    from app.database import get_session_factory
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    counts = _link_counts(record)
    assert counts["bridge"] < _BRIDGE_LINKS_BEFORE, (
        f"bridge links did not fall below the pre-assertion count "
        f"({counts['bridge']} vs {_BRIDGE_LINKS_BEFORE})"
    )
    assert counts["name_match"] == 0, (
        "an input is still being resolved by matching its NAME against the "
        "assumption vocabulary; the engine should assert the key instead"
    )
    # Every surviving bridge is labelled as an inference on the edge itself.
    for edge in record.edges:
        if (edge.meta or {}).get("link") == "bridge":
            assert "lineage bridge" in (edge.formula or "")


def _leaves_from(record, root: str) -> list[str]:
    adjacency: dict[str, list[str]] = {}
    for e in record.edges:
        adjacency.setdefault(e.src, []).append(e.dst)
    seen: set[str] = set()
    stack = [root]
    leaves: list[str] = []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        children = adjacency.get(current, ())
        if not children:
            leaves.append(current)
        stack.extend(children)
    return leaves


@pytest.mark.asyncio
async def test_wizard_entered_inputs_terminate_explicitly_and_no_root_is_refused() -> None:
    """The real-QA shape: purchase price, LTV and renovation budget come from
    the deal record / the analyst, not from an extraction.

    Two things must hold, and neither did. Every chain has to END somewhere a
    reader can recognise — a document page, or a node that says why there is no
    page — rather than simply running out of edges at ``capital.property_uses_usd``.
    And no refusal may name a KPI root: the root is never the thing that is
    missing, and six of them used to crowd out the entries that name a real gap.
    """
    from fondok_schemas.reasons import ReasonCode

    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines
    from app.services.lineage import build_lineage

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())
    # LTV + renovation budget entered by the analyst; price + keys on the deal
    # row. No documents at all — nothing here can reach a page, by design.
    overrides = {
        "ltv": {"value": 0.60, "note": "Term sheet from Ladder, 60% LTV."},
        "renovation_budget": {"value": 4_100_000, "note": "PIP scope, Nov 2025."},
    }

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, keys, purchase_price, "
                "field_overrides) VALUES (:id, :t, :n, :k, :p, :fo)"
            ),
            {
                "id": deal_id,
                "t": tenant_id,
                "n": "Wizard-only deal",
                "k": 132,
                "p": 36_400_000,
                "fo": json.dumps(overrides),
            },
        )
        await session.commit()
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        await session.commit()
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    nodes = _index(record)

    # 1. No refusal names a root.
    root_refusals = [r for r in record.unresolved if (r.concept or "") in set(record.roots)]
    assert not root_refusals, (
        "refusals must name the link that could not be walked, not the KPI: "
        f"{[(r.code.value, r.concept) for r in root_refusals]}"
    )

    # 2. The capital chain reaches the wizard's own inputs instead of stopping.
    paths = _walk(record, "kpi:returns.levered_irr")
    assert "engine:capital.property_uses_usd" in paths
    assert "assumption:purchase_price" in paths, (
        "levered IRR still does not reach the purchase price; reachable "
        f"assumptions: {sorted(n for n in paths if n.startswith('assumption:'))}"
    )
    assert "assumption:renovation_budget" in paths
    assert "assumption:ltv" in paths

    # 3. Those terminate EXPLICITLY: the deal-row value on a seed node that
    #    says "not applicable" (there is no document behind a typed number),
    #    the analyst's on an override node carrying the note.
    price_seed = nodes["seed:purchase_price"]
    assert price_seed.source == "deal_row"
    assert price_seed.reason == ReasonCode.NOT_APPLICABLE
    assert price_seed.meta["terminal"] == "analyst_input"
    assert nodes["override:ltv"].meta["note"].startswith("Term sheet")

    # 4. And nothing anywhere just runs out of edges: every leaf of every root
    #    is a page, a terminal node kind, or carries a reason.
    terminal_kinds = {"page", "seed", "benchmark", "override", "memo_section"}
    for root in record.roots:
        for leaf in _leaves_from(record, root):
            node = nodes.get(leaf)
            assert node is not None, leaf
            assert node.kind in terminal_kinds or node.reason is not None, (
                f"{leaf} ({node.kind}) ends the chain without saying why"
            )

    # 5. A deal-row / override terminal is not a failure, so it is not reported.
    reported = {r.concept for r in record.unresolved}
    assert "assumption:purchase_price" not in reported
    assert "assumption:ltv" not in reported


# ─────────── 7. evidence that is removed, not just moved ─────────────


@pytest.mark.asyncio
async def test_lineage_is_stale_once_a_source_document_is_deleted() -> None:
    """Deleting the evidence must age the run.

    ``_is_stale`` only ever watched inputs move FORWARD (uploaded_at /
    updated_at). ``delete_document`` hard-deletes the row and its extractions
    and never bumps ``deals.updated_at``, so a run whose T-12 had been removed
    read as perfectly fresh — the one case where "nothing moved" is the worst
    possible answer.
    """
    from app.database import get_session_factory
    from app.services.lineage import build_lineage, load_persisted, persist_for_run

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        await persist_for_run(session, deal_id, tenant_id, run_id)
        assert (
            await build_lineage(session, deal_id, tenant_id, run_id=run_id)
        ).stale is False

        t12_id = (
            await session.execute(
                text(
                    "SELECT id FROM documents WHERE deal_id = :d AND doc_type = 'T12'"
                ),
                {"d": deal_id},
            )
        ).scalar_one()
        await session.execute(
            text("DELETE FROM extraction_results WHERE document_id = :d"),
            {"d": str(t12_id)},
        )
        await session.execute(
            text("DELETE FROM documents WHERE id = :d"), {"d": str(t12_id)}
        )
        await session.commit()

        rebuilt = await build_lineage(session, deal_id, tenant_id, run_id=run_id)
        loaded = await load_persisted(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )

    assert rebuilt.stale is True, "a run whose evidence was deleted is not fresh"
    assert loaded is not None and loaded.stale is True
    assert str(t12_id) in rebuilt.meta["evidence_deleted"]
    assert any(
        (r.detail or "").find(str(t12_id)) >= 0 for r in rebuilt.unresolved
    ), "the deletion is not reported anywhere in unresolved"


@pytest.mark.asyncio
async def test_persisted_lineage_stops_citing_a_deleted_document() -> None:
    """The endpoint must not hand an analyst a page that cannot be opened.

    ``load_persisted`` replays the stored node list verbatim, so after the T-12
    was deleted ``GET /deals/{id}/lineage`` still served ``doc:<id>`` and
    ``page:<id>:4`` reachable from the levered IRR, with ``stale=False``. The
    stored row is a fine audit record; what the read path serves has to be
    walkable, so a cited-document deletion forces a rebuild.
    """
    from app.api.deals import get_deal_lineage
    from app.database import get_session_factory
    from app.services.lineage import persist_for_run

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_and_run(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        await persist_for_run(session, deal_id, tenant_id, run_id)
        before = await get_deal_lineage(UUID(deal_id), session, UUID(tenant_id))
        t12_id = str(
            (
                await session.execute(
                    text(
                        "SELECT id FROM documents WHERE deal_id = :d "
                        "AND doc_type = 'T12'"
                    ),
                    {"d": deal_id},
                )
            ).scalar_one()
        )
        assert f"doc:{t12_id}" in {n.id for n in before.nodes}

        await session.execute(
            text("DELETE FROM extraction_results WHERE document_id = :d"),
            {"d": t12_id},
        )
        await session.execute(
            text("DELETE FROM documents WHERE id = :d"), {"d": t12_id}
        )
        await session.commit()
        after = await get_deal_lineage(UUID(deal_id), session, UUID(tenant_id))

    ids = {n.id for n in after.nodes}
    assert f"doc:{t12_id}" not in ids, "the response still cites the deleted document"
    assert not any(n.startswith(f"page:{t12_id}:") for n in ids)
    assert after.stale is True
    assert t12_id in after.meta["evidence_deleted"]
