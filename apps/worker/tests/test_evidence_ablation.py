"""Phase 5.3 — evidence ablation: remove the document, remove the number.

Fondok's promise is that every displayed number traces to a document, an
analyst entry, or a calculation. The Kimpton golden test proves the chain
*exists*. Nothing proved the negative: that pulling a source document
actually pulls everything derived from it, with nothing surviving from a
cache, a stale run, or a default.

These tests seed a real deal from the production extraction payloads
(``tests/fixtures/real_payloads/anglers_t12_real.json`` +
``anglers_annual_pnl_real.json`` and the Kimpton Angler's OM at
``tests/fixtures/usali_v4/live_extraction_anglers_om.json``), run the full
engine chain, delete documents through the real ``DELETE /deals/{id}/
documents/{doc_id}`` endpoint, and re-assert.

Two contracts are deliberately different, and conflating them is how a
regression hides:

* **Seeded assumptions must NOT go blank.** ``engine_runner`` falls back to
  the Kimpton seed by contract (``_kimpton_assumptions``, label ``seed``;
  ``tests/test_engine_inputs_loader.py`` pins it). What removal must change
  is the *label* and the *reason*: source becomes ``seed`` and
  ``base["__reasons__"][key]`` carries ``no_document``.
* **Grounded actuals must genuinely vanish.** Historicals, variance actuals,
  the critic's inputs and the OM-anchored figures have no seed behind them,
  so they must return nothing at all.

Two of these started life as ``xfail(strict=True)`` — they encoded honest
behaviour the app did not yet have, so they would flip green (and fail the
suite, demanding attention) the moment someone fixed the finding. That is
exactly what happened: the lineage work now marks a run stale when a cited
document is deleted, and rebuilds on read rather than serving a chain to a
page that no longer exists. Both are plain assertions again; their FINDING
notes are kept as the record of what was wrong.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN (pattern: test_fon73_run_scoped_readers).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-evidence-ablation.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

_FIXTURES = Path(__file__).parent / "fixtures"
_T12_PAYLOAD = _FIXTURES / "real_payloads" / "anglers_t12_real.json"
_PNL_PAYLOAD = _FIXTURES / "real_payloads" / "anglers_annual_pnl_real.json"
_OM_PAYLOAD = _FIXTURES / "usali_v4" / "live_extraction_anglers_om.json"

# Values the Kimpton seed carries for the two anchors the T-12 grounds, and
# for the exit cap the OM's comp set grounds. Asserted literally so a seed
# change surfaces here instead of silently weakening the test.
_SEED_OCCUPANCY = 0.762
_SEED_ADR = 385.0
_SEED_EXIT_CAP = 0.07
_SEED_PURCHASE_PRICE = 36_400_000

# What the real Anglers T-12 payload grounds (the numbers that must vanish).
_T12_OCCUPANCY = 0.828892
_T12_ADR = 233.446

# The OM's own figures. ``entry_cap_rate`` / ``price_per_key`` / ``year_built``
# come straight off ``broker_proforma.*`` / ``property_overview.*``; the exit
# cap comes from the comp-set cap rates appended below.
_OM_ENTRY_CAP = 0.069
_OM_PRICE_PER_KEY = 919_540.0
_OM_YEAR_BUILT = 2009.0
_OM_COMP_EXIT_CAP = 0.065  # median of (0.062, 0.065, 0.068)

#: The extractor emits ``transaction_comps.<n>.cap_rate_pct``; three or more
#: comps make their median the deal's exit-cap anchor. The captured OM payload
#: carries no comps table, so the same three rows ``test_lineage_walk`` uses
#: are appended here — this is what makes ``exit_cap_rate`` genuinely
#: OM-grounded, and therefore ablatable.
_OM_COMPS = [
    {"field_name": f"transaction_comps.{n}.cap_rate_pct", "value": rate, "source_page": 7}
    for n, rate in ((1, 0.062), (2, 0.065), (3, 0.068))
]


def _fields(path: Path) -> list[dict[str, Any]]:
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
            "audit_log",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


# ──────────────────────────── seeding ─────────────────────────────────


async def _seed_deal(session, *, deal_id: str, tenant_id: str) -> None:
    """A deal row with keys but NO purchase_price.

    Deliberate: with a ``purchase_price`` on the row the key would resolve to
    ``deal_row`` and carry no seed reason at all, which would make the OM
    ablation assertion vacuous.
    """
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, keys, field_overrides) "
            "VALUES (:id, :tenant, :name, :keys, '{}')"
        ),
        {"id": deal_id, "tenant": tenant_id, "name": "Anglers Ablation", "keys": 132},
    )


async def _seed_document(
    session,
    *,
    deal_id: str,
    tenant_id: str,
    filename: str,
    doc_type: str,
    fields: list[dict[str, Any]],
    fiscal_year: int | None = None,
    content_hash: str,
    status: str = "EXTRACTED",
) -> str:
    """One EXTRACTED document + its extraction_results row.

    ``status`` defaults to ``'EXTRACTED'`` (upper) so the cache positive
    control cannot pass vacuously. It is overridable so the case-insensitivity
    regression below can seed the mixed-case spelling that finding 6 hit.
    ``agent_version`` carries the ``;pv=<version>`` suffix the cache filters on.
    """
    from app.api.documents import EXTRACTION_PIPELINE_VERSION

    doc_id = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
            "status, fiscal_year, content_hash, page_count) "
            "VALUES (:id, :deal, :tenant, :f, :dt, :st, :fy, :h, 10)"
        ),
        {
            "st": status,
            "id": doc_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "f": filename,
            "dt": doc_type,
            "fy": fiscal_year,
            "h": content_hash,
        },
    )
    await session.execute(
        text(
            "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, "
            "fields, agent_version) VALUES (:id, :doc, :deal, :tenant, :fl, :av)"
        ),
        {
            "id": str(uuid4()),
            "doc": doc_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "fl": json.dumps(fields),
            "av": (
                f"router:financials;dt:{doc_type};extractor;ps=deadbeef;"
                f"reg=1;pv={EXTRACTION_PIPELINE_VERSION}"
            ),
        },
    )
    return doc_id


#: content hashes, one per seeded document — the cache is keyed on these.
_HASH = {"t12": "a" * 64, "pnl": "b" * 64, "om": "c" * 64}


async def _seed_and_run(
    *, deal_id: str, tenant_id: str, run_id: str
) -> dict[str, str]:
    """T-12 + annual P&L + OM uploaded, then the full engine chain.

    Returns ``{"t12": id, "pnl": id, "om": id}``.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    factory = get_session_factory()
    ids: dict[str, str] = {}
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        ids["t12"] = await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_T12.xlsx",
            doc_type="T12",
            fields=_fields(_T12_PAYLOAD),
            fiscal_year=2025,
            content_hash=_HASH["t12"],
        )
        ids["pnl"] = await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_2024_PnL.xlsx",
            doc_type="PNL",
            fields=_fields(_PNL_PAYLOAD),
            fiscal_year=2024,
            content_hash=_HASH["pnl"],
        )
        ids["om"] = await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="Anglers_OM.pdf",
            doc_type="OM",
            fields=_fields(_OM_PAYLOAD) + _OM_COMPS,
            content_hash=_HASH["om"],
        )
        await session.commit()
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        await session.commit()
    return ids


async def _delete_documents(*, deal_id: str, tenant_id: str, doc_ids: list[str]) -> None:
    """Delete through the REAL endpoint — the production path an analyst hits.

    (It removes ``extraction_results`` explicitly; see the note on
    ``test_raw_document_delete_leaves_orphans_that_still_never_leak``.)
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for doc_id in doc_ids:
            r = await client.delete(
                f"/deals/{deal_id}/documents/{doc_id}",
                headers={"X-Tenant-Id": tenant_id},
            )
            assert r.status_code == 204, r.text


def _reason_code(entry: Any) -> str | None:
    """``__reasons__`` carries ``{"code": ReasonCode, "detail": str}``; the
    wire carries the bare code. Normalise both to a string."""
    if isinstance(entry, dict):
        entry = entry.get("code")
    if entry is None:
        return None
    return str(getattr(entry, "value", entry))


async def _inputs(session, *, deal_id: str, tenant_id: str) -> dict[str, Any]:
    from app.services.engine_runner import _load_engine_inputs

    return await _load_engine_inputs(session, deal_id, tenant_id=tenant_id)


async def _engine_row(session, *, deal_id: str, run_id: str, engine: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT inputs, outputs FROM engine_outputs "
                "WHERE deal_id = :d AND run_id = :r AND engine_name = :e"
            ),
            {"d": deal_id, "r": run_id, "e": engine},
        )
    ).first()
    assert row is not None, f"no {engine} row for run {run_id}"
    out: dict[str, Any] = {}
    for key in ("inputs", "outputs"):
        raw = row._mapping[key]
        if isinstance(raw, str):
            raw = json.loads(raw)
        out[key] = raw or {}
    return out


def _prov_input(provenance: dict[str, Any], node: str, name: str) -> Any:
    """Pull one named input off an engine provenance node."""
    entry = provenance.get(node) or {}
    for item in entry.get("inputs") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item.get("value")
    return None


# ══════════════ 1. T-12 + P&L removed — the actuals must vanish ══════════════


@pytest.mark.asyncio
async def test_removing_the_pnl_family_drops_every_t12_actual_source() -> None:
    """No ``t12_actual`` source survives, and each affected key falls back to
    the SEED with reason ``no_document`` — not to a dash, and not to a value
    that still claims to be grounded."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    factory = get_session_factory()
    async with factory() as session:
        before = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)

    # Positive control — the T-12 really did ground these before the ablation.
    assert before["__sources__"]["starting_occupancy"] == "t12_actual"
    assert before["__sources__"]["starting_adr"] == "t12_actual"
    assert before["starting_occupancy"] == pytest.approx(_T12_OCCUPANCY)
    assert before["starting_adr"] == pytest.approx(_T12_ADR)

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    async with factory() as session:
        after = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)

    sources = after["__sources__"]
    assert "t12_actual" not in set(sources.values()), (
        "a t12_actual source label survived the deletion of every P&L-family "
        f"document: {sorted(k for k, v in sources.items() if v == 't12_actual')}"
    )
    # The seed is the contract — not a dash.
    assert sources["starting_occupancy"] == "seed"
    assert sources["starting_adr"] == "seed"
    assert after["starting_occupancy"] == pytest.approx(_SEED_OCCUPANCY)
    assert after["starting_adr"] == pytest.approx(_SEED_ADR)
    # …and the reason says WHY it is a seed.
    reasons = after.get("__reasons__") or {}
    for key in ("starting_occupancy", "starting_adr"):
        assert _reason_code(reasons.get(key)) == "no_document", (
            f"{key} fell back to the seed with reason "
            f"{_reason_code(reasons.get(key))!r}, not no_document"
        )
    # Nothing still names a deleted extraction row.
    dead = {ids["t12"], ids["pnl"]}
    for key, meta in (after.get("__source_fields__") or {}).items():
        assert meta.get("document_id") not in dead, (
            f"{key} still names deleted document {meta.get('document_id')}"
        )
    assert after["t12_expense_actuals"] == {}

    # The analyst-facing endpoint tells the same story.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/assumption_sources", headers={"X-Tenant-Id": tenant_id}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "t12_actual" not in set(body["sources"].values())
    assert body["reasons"]["starting_occupancy"] == "no_document"
    for key, doc in (body.get("source_documents") or {}).items():
        assert doc not in dead, f"{key} still deep-links to deleted document {doc}"


@pytest.mark.asyncio
async def test_removing_the_pnl_family_empties_the_critic_inputs() -> None:
    """``_load_critic_inputs`` reconstructs the subject's actuals from the
    extractions. With no P&L-family document left there is nothing to
    reconstruct — it must return ``None``, not an empty-but-present shell."""
    from app.api.documents import _load_critic_inputs
    from app.database import get_session_factory

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    factory = get_session_factory()
    async with factory() as session:
        _broker, actuals, _market, keys = await _load_critic_inputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
    assert actuals is not None, "positive control: the T-12 should ground actuals"
    assert keys == 132

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    async with factory() as session:
        broker, actuals, _market, _keys = await _load_critic_inputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
    assert actuals is None, f"the critic still holds actuals after ablation: {actuals}"
    assert broker is None


@pytest.mark.asyncio
async def test_variance_reports_the_missing_side_instead_of_inventing_one() -> None:
    """``GET /analysis/{id}/variance`` must return zero flags and a note that
    names the side it is missing — never a comparison built from one side."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/analysis/{deal_id}/variance", headers={"X-Tenant-Id": tenant_id}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flags"] == []
    assert body["critical_count"] == 0 and body["warn_count"] == 0
    note = body.get("note") or ""
    assert "T-12 actuals" in note, f"variance did not name the missing side: {note!r}"


@pytest.mark.asyncio
async def test_removing_the_pnl_family_produces_no_historical_baseline() -> None:
    """The historicals grid is grounded values only — no seed behind it. With
    the statements gone it must produce nothing, not a padded skeleton with
    numbers in it."""
    from app.database import get_session_factory
    from app.engines.historical_baseline import build_historical_baseline

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    factory = get_session_factory()
    async with factory() as session:
        before = await build_historical_baseline(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
    assert before.coverage_pct > 0 and before.years, "positive control: history existed"

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    async with factory() as session:
        after = await build_historical_baseline(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
    assert after.years == [], f"historical years survived the ablation: {after.years}"
    assert after.coverage_pct == 0.0


# ══════════════════ 2. OM removed — nothing OM-grounded survives ══════════════


@pytest.mark.asyncio
async def test_removing_the_om_falls_back_with_a_no_document_reason() -> None:
    """``exit_cap_rate`` and ``purchase_price`` land on the Kimpton seed and
    say ``no_document`` — the label must be ``seed`` (or ``deal_row``), never a
    stale ``om_*``."""
    from app.database import get_session_factory

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    factory = get_session_factory()
    async with factory() as session:
        before = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)

    # Positive control: the OM's comp set really is the exit-cap anchor.
    assert before["__sources__"]["exit_cap_rate"] == "om_comps"
    assert before["exit_cap_rate"] == pytest.approx(_OM_COMP_EXIT_CAP)

    await _delete_documents(deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["om"]])

    async with factory() as session:
        after = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)

    sources, reasons = after["__sources__"], (after.get("__reasons__") or {})
    for key, seeded in (
        ("exit_cap_rate", _SEED_EXIT_CAP),
        ("purchase_price", _SEED_PURCHASE_PRICE),
    ):
        assert sources[key] in {"seed", "deal_row"}, (
            f"{key} kept a document-grounded label {sources[key]!r} after the "
            "OM was deleted"
        )
        assert after[key] == pytest.approx(seeded)
        assert _reason_code(reasons.get(key)) == "no_document", (
            f"{key} reason was {_reason_code(reasons.get(key))!r}, not no_document"
        )
    assert not {v for v in sources.values() if str(v).startswith("om_")}, (
        "an om_* source label survived the OM deletion: "
        f"{sorted(k for k, v in sources.items() if str(v).startswith('om_'))}"
    )


@pytest.mark.asyncio
async def test_no_om_grounded_figure_survives_anywhere_in_the_run() -> None:
    """Every figure the OM grounded — broker price-per-key, the broker's entry
    cap, the year built, the comp-set exit cap — is gone from the assumption
    map, from the per-field sidecar, from the doc deep-links, and from the
    engine output that consumed it."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_source_documents, run_all_engines

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    run_before, run_after = str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_before)

    factory = get_session_factory()
    async with factory() as session:
        before = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)
        om_keys_before = {
            k
            for k, v in (before.get("__source_fields__") or {}).items()
            if v.get("document_id") == ids["om"]
        }
        returns_before = await _engine_row(
            session, deal_id=deal_id, run_id=run_before, engine="returns"
        )

    # Positive control — the OM grounded real figures, and one of them reached
    # the exit valuation.
    assert {"entry_cap_rate", "price_per_key", "year_built", "exit_cap_rate"} <= om_keys_before
    assert before["entry_cap_rate"] == pytest.approx(_OM_ENTRY_CAP)
    assert before["price_per_key"] == pytest.approx(_OM_PRICE_PER_KEY)
    assert before["year_built"] == pytest.approx(_OM_YEAR_BUILT)
    assert _prov_input(
        returns_before["outputs"].get("provenance") or {},
        "gross_sale_price",
        "exit_cap_rate",
    ) == pytest.approx(_OM_COMP_EXIT_CAP)

    await _delete_documents(deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["om"]])

    async with factory() as session:
        after = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)
        source_docs = await _load_source_documents(
            session, deal_id=deal_id, sources=after["__sources__"], tenant_id=tenant_id
        )
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_after
        )
        await session.commit()
        returns_after = await _engine_row(
            session, deal_id=deal_id, run_id=run_after, engine="returns"
        )

    for key in ("entry_cap_rate", "price_per_key", "year_built"):
        assert after.get(key) is None, (
            f"{key} survived the OM deletion with value {after.get(key)!r}"
        )
    assert not {
        k
        for k, v in (after.get("__source_fields__") or {}).items()
        if v.get("document_id") == ids["om"]
    }
    assert ids["om"] not in set(source_docs.values())
    # The exit valuation now runs off the seed, not the deleted comp set.
    assert _prov_input(
        returns_after["outputs"].get("provenance") or {},
        "gross_sale_price",
        "exit_cap_rate",
    ) == pytest.approx(_SEED_EXIT_CAP)


# ══════════════════════════════ 3. lineage ═══════════════════════════════════


@pytest.mark.asyncio
async def test_rebuilt_lineage_names_no_deleted_document_after_a_rerun() -> None:
    """After the documents are deleted and the model re-run, no ``field:`` /
    ``doc:`` / ``page:`` node may reference a deleted document id."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines
    from app.services.lineage import build_lineage

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    run_before, run_after = str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_before)

    factory = get_session_factory()
    async with factory() as session:
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_before)
    doc_nodes = {n.id for n in record.nodes if n.id.startswith("doc:")}
    assert f"doc:{ids['t12']}" in doc_nodes, "positive control: the T-12 was a lineage node"

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"], ids["om"]]
    )

    async with factory() as session:
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_after
        )
        await session.commit()
        after = await build_lineage(session, deal_id, tenant_id, run_id=run_after)

    dead = set(ids.values())
    offenders = [
        n.id
        for n in after.nodes
        if n.id.startswith(("field:", "doc:", "page:"))
        and any(d in n.id for d in dead)
    ]
    assert offenders == [], f"lineage still cites deleted documents: {offenders}"
    # Every remaining assumption is a seed, so the record must say so.
    assert after.unresolved, "an all-seed run reported nothing unresolved"


@pytest.mark.asyncio
async def test_lineage_is_stale_once_a_source_document_is_deleted() -> None:
    from app.database import get_session_factory
    from app.services.lineage import build_lineage, load_persisted

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    factory = get_session_factory()
    async with factory() as session:
        rebuilt = await build_lineage(session, deal_id, tenant_id, run_id=run_id)
        stored = await load_persisted(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
    assert stored is not None
    assert rebuilt.stale is True, "build_lineage did not mark the run stale"
    assert stored.stale is True, "the persisted record did not mark the run stale"


@pytest.mark.asyncio
async def test_persisted_lineage_stops_citing_a_deleted_document() -> None:
    from app.database import get_session_factory
    from app.services.lineage import load_persisted

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    factory = get_session_factory()
    async with factory() as session:
        stored = await load_persisted(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
    assert stored is not None
    dead = {ids["t12"], ids["pnl"]}
    offenders = [n.id for n in stored.nodes if any(d in n.id for d in dead)]
    assert offenders == [], (
        f"the persisted lineage still cites deleted documents: {offenders}"
    )


# ════════════════════════ 4. no cache resurrection ═══════════════════════════


@pytest.mark.asyncio
async def test_a_deleted_documents_extraction_is_not_a_cache_hit() -> None:
    """The extraction cache is keyed on content hash + ``;pv=vN``. A deleted
    document's cached extraction must not be servable to a later upload —
    that is how a removed number would walk back into a run."""
    from app.api.documents import _lookup_extraction_cache
    from app.database import get_session_factory

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    factory = get_session_factory()
    async with factory() as session:
        hit = await _lookup_extraction_cache(
            session, tenant_id=tenant_id, content_hash=_HASH["t12"]
        )
    assert hit is not None, (
        "positive control failed: the T-12's content hash must be a cache hit "
        "while the document exists, or the negative below proves nothing"
    )

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"]]
    )

    async with factory() as session:
        for label in ("t12", "pnl"):
            miss = await _lookup_extraction_cache(
                session, tenant_id=tenant_id, content_hash=_HASH[label]
            )
            assert miss is None, (
                f"the deleted {label} document's extraction is still a cache "
                f"hit: {miss}"
            )
        # The OM is untouched — the miss above is deletion, not a broken cache.
        assert (
            await _lookup_extraction_cache(
                session, tenant_id=tenant_id, content_hash=_HASH["om"]
            )
            is not None
        )


@pytest.mark.asyncio
async def test_raw_document_delete_leaves_orphans_that_still_never_leak() -> None:
    """A raw ``DELETE FROM documents`` does NOT cascade on SQLite.

    ``migrations.MIGRATIONS`` declares ``extraction_results.document_id
    REFERENCES documents(id) ON DELETE CASCADE``, but ``SQLITE_MIGRATIONS``
    creates the table with no foreign key at all, so ``PRAGMA
    foreign_keys=ON`` has nothing to enforce. Production (Postgres) cascades;
    the test DB does not. This test pins the divergence AND pins the defence
    that makes it harmless: every reader JOINs ``documents``, so an orphaned
    extraction row can never re-ground a value or serve a cache hit.
    """
    from app.api.documents import _lookup_extraction_cache
    from app.database import get_session_factory

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        for label in ("t12", "pnl"):
            await session.execute(
                text("DELETE FROM documents WHERE id = :i"), {"i": ids[label]}
            )
        await session.commit()

        orphans = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM extraction_results "
                    "WHERE document_id IN (:a, :b)"
                ),
                {"a": ids["t12"], "b": ids["pnl"]},
            )
        ).scalar()
        assert orphans == 2, (
            "SQLITE_MIGRATIONS grew a foreign key — if extraction_results now "
            "cascades on SQLite this test's premise is stale, delete it"
        )

        # …and the orphans leak nothing.
        for label in ("t12", "pnl"):
            assert (
                await _lookup_extraction_cache(
                    session, tenant_id=tenant_id, content_hash=_HASH[label]
                )
                is None
            )
        after = await _inputs(session, deal_id=deal_id, tenant_id=tenant_id)
    assert after["starting_occupancy"] == pytest.approx(_SEED_OCCUPANCY)
    assert after["starting_adr"] == pytest.approx(_SEED_ADR)
    assert after["__sources__"]["starting_occupancy"] == "seed"
    assert "t12_actual" not in set(after["__sources__"].values())


# ═══════════════ 5. nothing survives in the persisted re-run ═════════════════


@pytest.mark.asyncio
async def test_the_rerun_carries_no_value_and_no_id_from_the_deleted_documents() -> None:
    """Re-running after the deletion must leave no engine output whose inputs,
    outputs or provenance name the deleted documents or carry their values."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines
    from app.services.lineage import build_lineage

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    run_before, run_after = str(uuid4()), str(uuid4())
    ids = await _seed_and_run(deal_id=deal_id, tenant_id=tenant_id, run_id=run_before)

    factory = get_session_factory()
    async with factory() as session:
        rev_before = await _engine_row(
            session, deal_id=deal_id, run_id=run_before, engine="revenue"
        )
    # Positive control — the T-12's ADR/occupancy really did drive Year 1.
    assert rev_before["inputs"]["starting_occupancy"] == pytest.approx(_T12_OCCUPANCY)
    assert rev_before["inputs"]["starting_adr"] == pytest.approx(_T12_ADR)
    assert _prov_input(
        rev_before["outputs"].get("provenance") or {}, "years[0].rooms_revenue", "adr"
    ) == pytest.approx(_T12_ADR)

    await _delete_documents(
        deal_id=deal_id, tenant_id=tenant_id, doc_ids=[ids["t12"], ids["pnl"], ids["om"]]
    )

    async with factory() as session:
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_after
        )
        await session.commit()
        rev_after = await _engine_row(
            session, deal_id=deal_id, run_id=run_after, engine="revenue"
        )
        rows = (
            await session.execute(
                text(
                    "SELECT engine_name, inputs, outputs FROM engine_outputs "
                    "WHERE deal_id = :d AND run_id = :r"
                ),
                {"d": deal_id, "r": run_after},
            )
        ).fetchall()
        lineage = await build_lineage(session, deal_id, tenant_id, run_id=run_after)

    # The deleted document's values are gone from the new run…
    assert rev_after["inputs"]["starting_occupancy"] == pytest.approx(_SEED_OCCUPANCY)
    assert rev_after["inputs"]["starting_adr"] == pytest.approx(_SEED_ADR)
    assert _prov_input(
        rev_after["outputs"].get("provenance") or {}, "years[0].rooms_revenue", "adr"
    ) == pytest.approx(_SEED_ADR)

    # …and no persisted row anywhere in the run names a deleted document.
    dead = set(ids.values())
    for row in rows:
        m = row._mapping
        blob = json.dumps({"i": m["inputs"], "o": m["outputs"]}, default=str)
        for doc_id in dead:
            assert doc_id not in blob, (
                f"engine {m['engine_name']} still names deleted document {doc_id}"
            )
    assert lineage.run_id is not None and str(lineage.run_id) == run_after


@pytest.mark.asyncio
async def test_extraction_cache_matches_status_case_insensitively() -> None:
    """A document stored as ``'Extracted'`` must still serve a cache hit.

    Finding 6 of this sweep: ``_lookup_extraction_cache`` compared
    ``d.status = 'EXTRACTED'`` verbatim while every other status reader
    normalises (``engines/historical_baseline.py`` uses ``UPPER(...)``). Two
    test files and several docstrings use the mixed-case spelling, so the
    divergence was one careless write away from silently disabling the cache.
    """
    from app.api.documents import _lookup_extraction_cache
    from app.database import get_session_factory

    deal_id, tenant_id = str(uuid4()), str(uuid4())
    content_hash = "case-insensitive-cache-probe"
    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_id, tenant_id=tenant_id)
        await _seed_document(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            filename="MixedCaseStatus.xlsx",
            doc_type="T12",
            fields=[{"field_name": "adr_usd", "value": 233.446, "confidence": 0.98}],
            content_hash=content_hash,
            status="Extracted",
        )
        await session.commit()

        hit = await _lookup_extraction_cache(
            session, tenant_id=tenant_id, content_hash=content_hash
        )

    assert hit is not None, (
        "a document whose status is stored as 'Extracted' must still serve a "
        "cache hit — the comparison is normalised with UPPER() now"
    )
