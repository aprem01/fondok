"""OpenAI embeddings client + embedding repair (2026-10-19 provider swap).

The Voyage account behind chunk embeddings was capped at 3 RPM / 10K TPM,
so every full batch 429'd and 98% of production chunks were stored with no
vector — silently. These tests pin the replacement's contract:

* the client asks for ``text-embedding-3-small`` at 1024 dims (the
  existing ``vector(1024)`` column), retries transient failures, honours
  ``Retry-After``, and gives up after a bounded number of attempts;
* ``embed_iter_partial`` isolates a failed batch to its own chunks;
* ``embed_pending_chunks`` re-embeds NULL and older-model rows only,
  only for the tenant (and deal) asked, within its cap, and leaves rows
  untouched when the provider fails;
* the loop is a no-op without a key.

No network: ``httpx.AsyncClient`` is replaced with a MockTransport client.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.extraction import embeddings
from app.services import embedding_repair

DIM = embeddings.EMBEDDING_DIM


def _install_transport(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", factory)


async def _no_sleep(_seconds):
    return None


def _ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    data = [
        {"index": i, "embedding": [float(i)] * DIM}
        for i in range(len(body["input"]))
    ]
    return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 1}})


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)


async def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert embeddings.is_enabled() is False
    with pytest.raises(embeddings.EmbeddingsUnavailable):
        await embeddings.embed_batch(["x"])


async def test_request_shape_and_order(monkeypatch, key):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        body = seen["body"]
        # reversed on purpose: the client must re-order by ``index``
        data = [
            {"index": i, "embedding": [float(i)] * DIM}
            for i in reversed(range(len(body["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    _install_transport(monkeypatch, handler)
    out = await embeddings.embed_batch(["a", "", "c"])
    assert seen["url"] == embeddings.EMBEDDINGS_ENDPOINT
    assert seen["auth"] == "Bearer sk-test-not-real"
    assert seen["body"]["model"] == "text-embedding-3-small"
    assert seen["body"]["dimensions"] == 1024
    assert seen["body"]["input"] == ["a", " ", "c"]  # empty input never sent
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]


async def test_retries_429_then_succeeds(monkeypatch, key):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={})
        return _ok(request)

    _install_transport(monkeypatch, handler)
    out = await embeddings.embed_batch(["a"])
    assert calls["n"] == 3
    assert len(out) == 1 and len(out[0]) == DIM


async def test_gives_up_after_bounded_attempts(monkeypatch, key):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={})

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await embeddings.embed_batch(["a"])
    assert calls["n"] == 4


async def test_non_retryable_error_is_not_retried(monkeypatch, key):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await embeddings.embed_batch(["a"])
    assert calls["n"] == 1


async def test_wrong_dimension_is_rejected(monkeypatch, key):
    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 1536}]}),
    )
    with pytest.raises(ValueError):
        await embeddings.embed_batch(["a"])


async def test_partial_isolates_a_failed_batch(monkeypatch, key):
    batch_no = {"n": 0}

    def handler(request):
        batch_no["n"] += 1
        # batch 2 fails on every attempt; batches 1 and 3 succeed
        if 2 <= batch_no["n"] <= 5:
            return httpx.Response(500, json={})
        return _ok(request)

    _install_transport(monkeypatch, handler)
    size = embeddings.EMBEDDING_MAX_BATCH
    out = await embeddings.embed_iter_partial(["t"] * (size * 2 + 3))
    assert len(out) == size * 2 + 3
    assert all(v is not None for v in out[:size])
    assert all(v is None for v in out[size : size * 2])
    assert all(v is not None for v in out[size * 2 :])


# ── embedding repair ────────────────────────────────────────────────────

T_A = str(uuid.uuid4())
T_B = str(uuid.uuid4())
D_1 = str(uuid.uuid4())
D_2 = str(uuid.uuid4())


@pytest.fixture
async def chunk_db(tmp_path):
    """A standalone SQLite ``document_chunks`` (the shared test schema has
    none — pgvector is Postgres-only). ``CAST(… AS vector)`` is legal SQLite
    (NUMERIC affinity), so the repair SQL runs verbatim."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chunks.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE document_chunks (
                    id TEXT PRIMARY KEY, deal_id TEXT, document_id TEXT,
                    tenant_id TEXT, chunk_index INTEGER, chunk_text TEXT,
                    embedding TEXT, embedding_model TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        rows = [
            # tenant A, deal 1: two NULL, one old (Voyage, NULL tag + vector),
            # one already current
            ("a1", D_1, T_A, None, None),
            ("a2", D_1, T_A, None, None),
            ("a3", D_1, T_A, "[0.5]", None),
            ("a4", D_1, T_A, "[0.5]", embeddings.EMBEDDING_MODEL),
            # tenant A, deal 2: one NULL
            ("a5", D_2, T_A, None, None),
            # tenant B: one NULL — must never be touched by a tenant-A call
            ("b1", D_1, T_B, None, None),
        ]
        for i, (cid, deal, tenant, emb, model) in enumerate(rows):
            await conn.execute(
                text(
                    "INSERT INTO document_chunks (id, deal_id, document_id, tenant_id, "
                    "chunk_index, chunk_text, embedding, embedding_model) VALUES "
                    "(:id, :deal, :doc, :tenant, :idx, :txt, :emb, :model)"
                ),
                {"id": cid, "deal": deal, "doc": "doc", "tenant": tenant, "idx": i,
                 "txt": f"chunk {cid}", "emb": emb, "model": model},
            )
    from sqlalchemy.ext.asyncio import async_sessionmaker

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _models(factory) -> dict[str, str | None]:
    async with factory() as s:
        rows = (await s.execute(text("SELECT id, embedding_model FROM document_chunks"))).fetchall()
    return {r[0]: r[1] for r in rows}


async def test_repair_embeds_null_and_old_model_rows_for_one_deal(monkeypatch, key, chunk_db):
    _install_transport(monkeypatch, _ok)
    async with chunk_db() as s:
        total, missing = await embedding_repair.count_chunks(s, tenant_id=T_A, deal_id=D_1)
        assert (total, missing) == (4, 3)
        r = await embedding_repair.embed_pending_chunks(
            s, tenant_id=T_A, deal_id=D_1, max_chunks=missing
        )
    assert (r.embedded, r.failed) == (3, 0)
    m = await _models(chunk_db)
    cur = embeddings.EMBEDDING_MODEL
    assert m["a1"] == m["a2"] == m["a3"] == m["a4"] == cur
    assert m["a5"] is None  # other deal untouched
    assert m["b1"] is None  # other tenant untouched


async def test_repair_respects_the_cap_and_tenant(monkeypatch, key, chunk_db):
    _install_transport(monkeypatch, _ok)
    async with chunk_db() as s:
        r = await embedding_repair.embed_pending_chunks(s, tenant_id=T_A, max_chunks=2)
    assert r.embedded == 2
    m = await _models(chunk_db)
    assert sum(1 for k, v in m.items() if k.startswith("a") and v) == 3  # a4 + 2 new
    assert m["b1"] is None


async def test_repair_leaves_rows_untouched_when_provider_fails(monkeypatch, key, chunk_db):
    _install_transport(monkeypatch, lambda r: httpx.Response(500, json={}))
    async with chunk_db() as s:
        r = await embedding_repair.embed_pending_chunks(s, tenant_id=T_A, max_chunks=10)
    assert r.embedded == 0 and r.failed > 0
    m = await _models(chunk_db)
    assert m["a1"] is None and m["a3"] is None  # nothing half-written


async def test_tick_is_a_noop_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = await embedding_repair.tick_once()
    assert (r.embedded, r.failed) == (0, 0)


def test_vector_search_only_scores_current_model_rows():
    """Vectors from different models are not comparable — the search SQL
    must filter on the model tag."""
    import inspect

    from app.extraction import context_store

    src = inspect.getsource(context_store.search_chunks)
    assert "embedding_model = :model" in src
    assert '"model": embeddings.EMBEDDING_MODEL' in src
