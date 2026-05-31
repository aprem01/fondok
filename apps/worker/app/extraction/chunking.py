"""Document chunking — splits parsed pages into searchable chunks.

Phase 3 of the dynamic-extensibility refactor. Every parsed document
is broken into ~500-token chunks (with overlap so semantic boundaries
don't slice mid-sentence) and persisted to ``document_chunks``. The
chunks become the substrate for:

  * Full-text search across all uploaded docs on a deal (always on).
  * Vector similarity / semantic search (on when Voyage embeddings
    have been backfilled — gated by ``VOYAGE_API_KEY``).
  * Future agent tools that need cited evidence from arbitrary docs
    (the IC memo agent already does this for structured fields; the
    chunk store is the unstructured-text counterpart).

Token counting is approximate — we use a fast char-based heuristic
(~4 chars/token for English business prose) instead of pulling a real
tokenizer dep. The chunker's job is to keep chunks within the
embedding model's context window; off-by-20% is fine.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple

# voyage-3 maxes out at 32K tokens per input. We target 500 tokens per
# chunk to keep latency low + maximize the number of distinct semantic
# units a search query can match against. Overlap = 50 tokens.
CHUNK_TARGET_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50

# Approximate chars-per-token for English business prose.
_CHARS_PER_TOKEN = 4


def _approx_tokens(text: str) -> int:
    """Char-based heuristic — fast, no tokenizer dep, ±20% accurate."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


class Chunk(NamedTuple):
    """One emission from the chunker. Persisted as a document_chunks row.

    source_page is the parser's page number when the chunk came from a
    single page; None when the chunker had to span page boundaries to
    hit the target token count.
    """

    text: str
    tokens: int
    source_page: int | None
    chunk_index: int


def chunk_pages(pages: Iterable[tuple[int, str]]) -> list[Chunk]:
    """Split (page_num, text) pairs into ~500-token chunks with overlap.

    Strategy:
        1. Walk each page, splitting on paragraph boundaries first
           (double-newline). Single-page paragraphs that fit under the
           target stay intact — preserves the natural unit of a
           USALI subtotal block or a slide bullet group.
        2. When a paragraph exceeds the target, fall back to sentence
           splits (period / question / exclamation).
        3. When a sentence still exceeds the target (rare; usually a
           giant table cell), hard-split on character count.
        4. Concatenate small paragraphs into the same chunk until the
           target is reached.
        5. Apply a small token overlap between chunks so a query
           landing on a boundary still hits the relevant context.

    Returns chunks in document order. Empty input → empty list.
    """
    raw_chunks: list[Chunk] = []
    chunk_idx = 0
    current_text: list[str] = []
    current_tokens = 0
    current_page: int | None = None

    def flush(spans_page: int | None) -> None:
        nonlocal chunk_idx, current_text, current_tokens, current_page
        if not current_text:
            return
        text = "\n\n".join(current_text).strip()
        if not text:
            current_text = []
            current_tokens = 0
            current_page = None
            return
        raw_chunks.append(
            Chunk(
                text=text,
                tokens=_approx_tokens(text),
                source_page=spans_page,
                chunk_index=chunk_idx,
            )
        )
        chunk_idx += 1
        # Seed the next chunk with a tail of the previous one for
        # overlap. We slice at character count = overlap_tokens * 4.
        tail_chars = CHUNK_OVERLAP_TOKENS * _CHARS_PER_TOKEN
        tail = text[-tail_chars:] if len(text) > tail_chars else ""
        current_text = [tail] if tail else []
        current_tokens = _approx_tokens(tail) if tail else 0
        current_page = None  # overlap straddles, so flag mixed-source.

    for page_num, page_text in pages:
        if not page_text or not page_text.strip():
            continue
        # Track whether everything in the current chunk came from one page.
        if current_page is None and not current_text:
            current_page = page_num
        elif current_page != page_num:
            current_page = None  # multi-page chunk

        # Paragraph-level split.
        paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]
        for para in paragraphs:
            para_tokens = _approx_tokens(para)

            # Oversize paragraph — sentence-split inside it.
            if para_tokens > CHUNK_TARGET_TOKENS:
                sentences = _split_into_sentences(para)
                for sent in sentences:
                    sent_tokens = _approx_tokens(sent)
                    if sent_tokens > CHUNK_TARGET_TOKENS:
                        # Hard-split a too-long sentence on char count.
                        max_chars = CHUNK_TARGET_TOKENS * _CHARS_PER_TOKEN
                        for i in range(0, len(sent), max_chars):
                            piece = sent[i : i + max_chars]
                            if current_tokens + _approx_tokens(piece) > CHUNK_TARGET_TOKENS:
                                flush(current_page)
                                current_page = page_num
                            current_text.append(piece)
                            current_tokens += _approx_tokens(piece)
                    else:
                        if current_tokens + sent_tokens > CHUNK_TARGET_TOKENS:
                            flush(current_page)
                            current_page = page_num
                        current_text.append(sent)
                        current_tokens += sent_tokens
                continue

            # Normal-size paragraph.
            if current_tokens + para_tokens > CHUNK_TARGET_TOKENS:
                flush(current_page)
                current_page = page_num
            current_text.append(para)
            current_tokens += para_tokens

    flush(current_page)
    return raw_chunks


def _split_into_sentences(text: str) -> list[str]:
    """Cheap sentence split — period / question / exclamation followed
    by whitespace. Doesn't try to handle abbreviations correctly
    (Mr., Inc., etc.) because the worst case is over-splitting, which
    is fine for our token budget; under-splitting would leave giant
    pseudo-sentences the chunker then has to hard-split on chars.
    """
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in ".!?" and len(buf) > 0:
            # Peek-ahead is fiddly; just split on terminator + whitespace.
            out.append("".join(buf).strip())
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


__all__ = ["Chunk", "chunk_pages", "CHUNK_TARGET_TOKENS", "CHUNK_OVERLAP_TOKENS"]
