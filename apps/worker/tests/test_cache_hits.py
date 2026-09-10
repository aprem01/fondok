"""Prompt-cache hit-rate regression tests.

Runs the Extractor twice in quick succession against the same input and
asserts that:

  1. The first call writes to cache (``cache_creation_input_tokens > 0``).
  2. The second call reads from cache (``cache_read_input_tokens > 0``)
     within the 5-minute Anthropic ephemeral cache TTL.
  3. The second call's cache_read_input_tokens are significantly
     greater than the first call's — evidence the cached prefix
     (USALI rules + brand catalog + schema addendum) is hitting cache
     and not being rebuilt.

LIVE-MODEL TEST, OPT-IN: marked ``pytest.mark.live_llm`` and skipped
unless ``FONDOK_LIVE_LLM=1`` AND a real ``ANTHROPIC_API_KEY`` are both
in the shell environment (no ``.env`` hydration). CI deselects the
marker. The full module costs ~$0.10 of Sonnet input on a successful
run::

    cd apps/worker
    FONDOK_LIVE_LLM=1 ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/test_cache_hits.py -v -m live_llm
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# Force the SQLite dev DSN before app modules import.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_DIR = _REPO_ROOT / "evals" / "golden-set" / "kimpton-angler" / "input"


_PLACEHOLDER_KEY_PREFIXES = ("sk-ant-test-", "sk-test-")


def _live_llm_skip_reason() -> str | None:
    """Same opt-in gate as test_agents.py (kept inline: pytest modules
    should not import each other). Reads ONLY the process environment."""
    if os.environ.get("FONDOK_LIVE_LLM", "").strip() != "1":
        return (
            "live-model test: opt in with FONDOK_LIVE_LLM=1 (plus a real "
            "ANTHROPIC_API_KEY); skipped by default so no run burns tokens."
        )
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.startswith(_PLACEHOLDER_KEY_PREFIXES) or "dummy" in key:
        return (
            "FONDOK_LIVE_LLM=1 but ANTHROPIC_API_KEY is unset or a test "
            "placeholder: export a real key to run live-model tests."
        )
    return None


_SKIP_REASON = _live_llm_skip_reason()

# Live cache-hit probe -- marked ``live_llm`` (deselected in CI) and
# skipped unless explicitly opted in, so a bare ``pytest`` reports it as
# skipped, never failed.
# NOTE (2026-07-10): the '0 cache_creation_tokens' symptom this used to
# track was a telemetry bug (langchain_anthropic 1.4 splits cache
# creation into ephemeral_5m/1h keys), fixed in app/usage.py -- prompt
# caching itself was always working. Opt in to spot-check live cache
# behavior.
pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((_GOLDEN_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def om_fixture() -> dict[str, Any]:
    return _load_json("om_extracted.json")


@pytest.mark.asyncio
async def test_extractor_warms_then_hits_cache(om_fixture: dict[str, Any]) -> None:
    """Run the Extractor twice in quick succession and confirm the
    second call serves the system-prompt prefix from cache."""
    from fondok_schemas import DocType

    from app.agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
        serialize_json_doc,
    )

    deal_id = "11111111-2222-3333-4444-cccccccccccc"
    doc = ExtractorDocument(
        document_id=om_fixture.get("document_id"),
        filename=om_fixture.get("filename") or "Offering_Memorandum.pdf",
        doc_type=DocType.OM,
        content=serialize_json_doc(om_fixture),
        source_pages=list(map(int, om_fixture.get("raw_text_by_page", {}).keys() or [1])),
    )
    payload = ExtractorInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=deal_id,
        documents=[doc],
    )

    out_first = await run_extractor(payload)
    assert out_first.success, f"first extractor call failed: {out_first.error}"
    assert out_first.model_calls, "first call recorded no ModelCall"
    first = out_first.model_calls[0]

    # The first call should write to cache (creation > 0). When
    # nothing is written, the breakpoint config is broken.
    assert first.cache_creation_input_tokens > 0, (
        f"first call wrote 0 cache tokens — breakpoints not effective\n"
        f"call={first.model_dump()}"
    )

    # Same payload, immediately. Second call should HIT cache (read > 0).
    out_second = await run_extractor(payload)
    assert out_second.success, f"second extractor call failed: {out_second.error}"
    assert out_second.model_calls, "second call recorded no ModelCall"
    second = out_second.model_calls[0]

    assert second.cache_read_input_tokens > 0, (
        f"second call had 0 cache_read — cache prefix didn't hit\n"
        f"call={second.model_dump()}"
    )
    # The second call's cache reads should be at least the first
    # call's writes (we cached at least that much). Allow some slack
    # for Anthropic's prefix-match heuristics (cached blocks are
    # contiguous from the front).
    assert second.cache_read_input_tokens >= first.cache_creation_input_tokens * 0.8, (
        f"second call cache_read {second.cache_read_input_tokens} < 80% of "
        f"first call cache_create {first.cache_creation_input_tokens}"
    )
