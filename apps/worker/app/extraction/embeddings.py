"""OpenAI embeddings client — lazy, optional, HTTP-only.

Backs the chunk store's semantic half (``context_store``): each ~500-token
chunk of a parsed document gets a vector, and ``GET /deals/{id}/search``
blends cosine similarity with Postgres full-text rank.

  * Provider: OpenAI ``text-embedding-3-small`` requested at 1024
    dimensions, so it fills the existing ``document_chunks.embedding
    vector(1024)`` column and its HNSW cosine index unchanged.
    (2026-10-19: replaced Voyage ``voyage-3``. The Voyage account had no
    payment method — 3 RPM / 10K TPM — so every full 64-chunk batch was
    a 429 and 98% of chunks were stored without a vector, silently.)
  * Every stored vector is tagged with ``EMBEDDING_MODEL`` in
    ``document_chunks.embedding_model``. Vectors from different models
    are NOT comparable, so search only scores rows whose tag equals the
    current model, and the repair job re-embeds anything else.
  * No SDK dep — POST to api.openai.com via httpx (already a worker dep).
  * Gated by ``OPENAI_API_KEY``. Absent → ``is_enabled() is False`` and
    ``embed_batch()`` raises ``EmbeddingsUnavailable``; callers fall back
    to full-text search.
  * Transient failures (429, 5xx, timeouts, connection errors) are
    retried with backoff (``Retry-After`` honoured). ``embed_iter_partial``
    isolates failures per batch: one bad batch leaves only ITS chunks
    unembedded (the repair job fills them later) instead of the whole
    document.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

EMBEDDING_PROVIDER = "openai"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1024
EMBEDDINGS_ENDPOINT = "https://api.openai.com/v1/embeddings"

# OpenAI accepts up to 2048 inputs per request; 64 × ~500-token chunks
# (~32K tokens) keeps one request small enough to retry cheaply.
EMBEDDING_MAX_BATCH = 64

# Attempts per batch (1 initial + retries) and the base backoff.
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 20.0
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class EmbeddingsUnavailable(RuntimeError):
    """Raised when callers try to embed without OPENAI_API_KEY set."""


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def is_enabled() -> bool:
    """True when OPENAI_API_KEY is configured. Read on every call so a key
    added to the environment needs no code path change."""
    return bool(_api_key())


def _retry_after_seconds(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        raw = resp.headers.get("retry-after")
        if raw:
            try:
                return min(_BACKOFF_CAP_SECONDS, max(0.0, float(raw)))
            except ValueError:
                pass
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt))


async def embed_batch(
    texts: list[str],
    *,
    input_type: str = "document",
    timeout_seconds: float = 60.0,
) -> list[list[float]]:
    """Embed up to ``EMBEDDING_MAX_BATCH`` texts; vectors in input order.

    ``input_type`` ("document" | "query") is validated for API stability
    with callers; OpenAI embeds both sides with the same projection.

    Raises:
        EmbeddingsUnavailable if OPENAI_API_KEY isn't set.
        httpx.HTTPError after the retries are exhausted.
    """
    key = _api_key()
    if not key:
        raise EmbeddingsUnavailable(
            "OPENAI_API_KEY not set — cannot embed. Callers should check "
            "is_enabled() before embed_batch()."
        )
    if not texts:
        return []
    if len(texts) > EMBEDDING_MAX_BATCH:
        raise ValueError(
            f"embed_batch: {len(texts)} inputs > EMBEDDING_MAX_BATCH "
            f"({EMBEDDING_MAX_BATCH}); split before calling."
        )
    if input_type not in ("document", "query"):
        raise ValueError(
            f"embed_batch: input_type must be 'document' or 'query'; "
            f"got {input_type!r}"
        )
    # OpenAI rejects empty strings; a whitespace-only chunk embeds as a space.
    inputs = [t if t and t.strip() else " " for t in texts]

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for attempt in range(_MAX_ATTEMPTS):
            resp: httpx.Response | None = None
            try:
                resp = await client.post(
                    EMBEDDINGS_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "input": inputs,
                        "model": EMBEDDING_MODEL,
                        "dimensions": EMBEDDING_DIM,
                    },
                )
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    delay = _retry_after_seconds(resp, attempt)
                    logger.info(
                        "embeddings: HTTP %d on a %d-input batch — retry %d in %.1fs",
                        resp.status_code, len(inputs), attempt + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                body = resp.json()
                data = body.get("data", [])
                by_index = {
                    item["index"]: item["embedding"]
                    for item in data
                    if "embedding" in item
                }
                vectors = [by_index[i] for i in range(len(inputs))]
                for v in vectors:
                    if len(v) != EMBEDDING_DIM:
                        raise ValueError(
                            f"embeddings: got {len(v)} dims, expected {EMBEDDING_DIM}"
                        )
                return vectors
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt >= _MAX_ATTEMPTS - 1:
                    break
                delay = _retry_after_seconds(None, attempt)
                logger.info(
                    "embeddings: %s on a %d-input batch — retry %d in %.1fs",
                    type(exc).__name__, len(inputs), attempt + 1, delay,
                )
                await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("embeddings: retries exhausted")  # pragma: no cover


async def embed_iter(
    texts: Iterable[str],
    *,
    input_type: str = "document",
) -> list[list[float]]:
    """All-or-nothing: embed any number of texts in batches; raises on the
    first batch that still fails after retries."""
    inputs = list(texts)
    out: list[list[float]] = []
    for i in range(0, len(inputs), EMBEDDING_MAX_BATCH):
        batch = inputs[i : i + EMBEDDING_MAX_BATCH]
        out.extend(await embed_batch(batch, input_type=input_type))
    return out


async def embed_iter_partial(
    texts: Iterable[str],
    *,
    input_type: str = "document",
) -> list[list[float] | None]:
    """Like ``embed_iter`` but a batch that still fails after retries
    yields ``None`` for each of ITS inputs; later batches still run.
    Returns one entry per input, in order."""
    inputs = list(texts)
    out: list[list[float] | None] = []
    for i in range(0, len(inputs), EMBEDDING_MAX_BATCH):
        batch = inputs[i : i + EMBEDDING_MAX_BATCH]
        try:
            out.extend(await embed_batch(batch, input_type=input_type))
        except EmbeddingsUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — isolate the batch
            logger.warning(
                "embeddings: batch %d–%d failed after retries (%s: %s); "
                "those chunks stay unembedded for the repair job",
                i, i + len(batch) - 1, type(exc).__name__, exc,
            )
            out.extend([None] * len(batch))
    return out


__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MAX_BATCH",
    "EMBEDDING_MODEL",
    "EMBEDDING_PROVIDER",
    "EmbeddingsUnavailable",
    "embed_batch",
    "embed_iter",
    "embed_iter_partial",
    "is_enabled",
]
