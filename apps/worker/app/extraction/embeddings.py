"""Voyage AI embeddings client — lazy, optional, HTTP-only.

Phase 3 of the dynamic-extensibility refactor. Wraps Voyage's REST
embeddings endpoint so the chunking pipeline can backfill vector
representations for semantic search. Intentionally lightweight:

  * No SDK dep — we POST to api.voyageai.com directly via httpx
    (already a worker dep). Removes a transitive surface that would
    otherwise need version-pinning + a Renovate / Dependabot bump.
  * Gated by VOYAGE_API_KEY. When the key is absent the module exposes
    ``is_enabled() == False`` and ``embed_batch()`` raises a typed
    ``EmbeddingsUnavailable`` error. Callers (chunk worker, search
    endpoint) check the flag and fall back to FTS-only.
  * Model: voyage-3 (1024-dim, cosine-normalized). voyage-3-lite (512)
    is half the cost / half the recall — would re-enable later if cost
    became a bottleneck.

Voyage rate limits: 3M tokens/min on the free tier, 300 RPM. We chunk
at ~500 tokens so a 30-page document (~30 chunks) costs ~15K tokens
and one request. No batching back-pressure needed at our scale.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

VOYAGE_MODEL = "voyage-3"
VOYAGE_EMBEDDING_DIM = 1024
VOYAGE_ENDPOINT = "https://api.voyageai.com/v1/embeddings"

# Voyage's documented per-request limit is 128 inputs; we cap at 64
# to stay comfortably under and keep timeouts manageable.
VOYAGE_MAX_BATCH = 64


class EmbeddingsUnavailable(RuntimeError):
    """Raised when callers try to embed without VOYAGE_API_KEY set."""


def is_enabled() -> bool:
    """True when VOYAGE_API_KEY is configured. The chunk worker
    backfills + the search endpoint switch to hybrid scoring on this
    being True; otherwise FTS is the only ranking signal.
    """
    return bool(os.environ.get("VOYAGE_API_KEY", "").strip())


async def embed_batch(
    texts: list[str],
    *,
    input_type: str = "document",
    timeout_seconds: float = 30.0,
) -> list[list[float]]:
    """Embed a batch of texts via Voyage's REST API.

    Args:
        texts: 1..VOYAGE_MAX_BATCH strings to embed.
        input_type: "document" for storage-side embeddings, "query"
            for the search-side. Voyage tunes the projection slightly
            differently for each — using the wrong type drops recall
            ~3-5%. Default is "document" since the chunk worker is
            the dominant caller.
        timeout_seconds: per-request timeout.

    Returns:
        List of 1024-float vectors, one per input, in input order.

    Raises:
        EmbeddingsUnavailable if VOYAGE_API_KEY isn't set.
        ValueError on invalid input shape.
        httpx.HTTPStatusError on non-2xx responses (rate limit, auth,
            content too long). Callers should treat these as transient
            and back off — the chunk-worker pipeline catches and retries.
    """
    key = os.environ.get("VOYAGE_API_KEY", "").strip()
    if not key:
        raise EmbeddingsUnavailable(
            "VOYAGE_API_KEY not set — cannot embed. The chunking "
            "pipeline still writes chunks (FTS-only); call site "
            "should check is_enabled() before embed_batch()."
        )
    if not texts:
        return []
    if len(texts) > VOYAGE_MAX_BATCH:
        raise ValueError(
            f"embed_batch: {len(texts)} inputs > VOYAGE_MAX_BATCH "
            f"({VOYAGE_MAX_BATCH}); split before calling."
        )
    if input_type not in ("document", "query"):
        raise ValueError(
            f"embed_batch: input_type must be 'document' or 'query'; "
            f"got {input_type!r}"
        )

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            VOYAGE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "input": texts,
                "model": VOYAGE_MODEL,
                "input_type": input_type,
            },
        )
        resp.raise_for_status()
        body = resp.json()

    data = body.get("data", [])
    # Voyage returns items in input order, but be defensive — sort by
    # ``index`` to handle any future reordering.
    by_index = {item["index"]: item["embedding"] for item in data if "embedding" in item}
    return [by_index[i] for i in range(len(texts))]


async def embed_iter(
    texts: Iterable[str],
    *,
    input_type: str = "document",
) -> list[list[float]]:
    """Embed an arbitrary-length iterable, splitting into batches of
    VOYAGE_MAX_BATCH under the hood. Concatenates results in input
    order.
    """
    inputs = list(texts)
    out: list[list[float]] = []
    for i in range(0, len(inputs), VOYAGE_MAX_BATCH):
        batch = inputs[i : i + VOYAGE_MAX_BATCH]
        out.extend(await embed_batch(batch, input_type=input_type))
    return out


__all__ = [
    "VOYAGE_MODEL",
    "VOYAGE_EMBEDDING_DIM",
    "VOYAGE_MAX_BATCH",
    "EmbeddingsUnavailable",
    "is_enabled",
    "embed_batch",
    "embed_iter",
]
