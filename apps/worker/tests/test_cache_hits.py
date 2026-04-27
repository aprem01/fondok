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

Gated on ``ANTHROPIC_API_KEY`` so CI runs without burning tokens.
The full module costs ~$0.10 of Sonnet input on a successful run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# Force the SQLite dev DSN before app modules import.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


def _load_dotenv_if_unset() -> None:
    """Mirror the .env-loading hack from test_agents.py for the API key."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "ANTHROPIC_API_KEY" and value and not os.environ.get(key):
            os.environ[key] = value
            break


_load_dotenv_if_unset()


_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_DIR = _REPO_ROOT / "evals" / "golden-set" / "kimpton-angler" / "input"


pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY unset — skipping LLM cache-hit tests.",
)


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
