"""Prompt-caching coverage regression tests (batch O, 2026-06-27).

Anthropic's ephemeral prompt cache charges 10% of normal input cost on
the cached prefix, so every long static block in an agent's system
prompt should carry a ``cache_control: {"type": "ephemeral"}`` marker
(or sit inside a prefix that ends in one). These tests are STRUCTURAL:
they assert every agent's canonical system-block assembly produces a
SystemMessage whose content list marks the right blocks for caching.

No API key required — the tests never call Anthropic. They exercise
:func:`app.llm.build_agent_system_blocks` and
:func:`app.llm.cached_system_message_blocks` against the same inputs
each agent uses in production, and check the resulting content-block
list.

Coverage matrix (block → cached?):

    * Agent instructions (per role SYSTEM_PROMPT) → cached
    * USALI rules catalog                         → cached
    * Brand catalog                               → cached
    * Schema addendum (per role)                  → cached

The Extractor's SYSTEM_PROMPT is the biggest single win — ~5.8k tokens,
static per doc_type, called once per chunked ExtractorDocument (a
45-page OM fans out to ~9 chunks). Regression here silently doubles
Extractor spend, so the test asserts a minimum instructions-block
size in addition to the cache marker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Test isolation — mirror the pattern used by test_extractor_throttle.py
# and test_extractor_empty_envelope_retry.py so a developer's shell-level
# DATABASE_URL doesn't bleed in.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-prompt-caching.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
# The cache-block helpers gate on the resolved LLM provider; keep them
# on the anthropic path so ``cache_control`` markers actually emit
# (non-anthropic providers collapse to plain text).
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")


# ─────────────────────── helpers ───────────────────────


def _cache_control_flags(content: list[dict]) -> list[bool]:
    """Return the per-block ``bool(cache_control present)`` list."""
    return [bool(b.get("cache_control")) for b in content]


def _texts(content: list[dict]) -> list[str]:
    return [b.get("text", "") for b in content]


# ─────────────────────── build_agent_system_blocks ───────────────────────


def test_build_agent_system_blocks_marks_every_block_cache_eligible() -> None:
    """The canonical 4-block layout is entirely cache-eligible.

    Regression guard: agent instructions were previously ``cache=False``
    which forfeited a cache breakpoint slot on the largest static block
    (Extractor SYSTEM_PROMPT, ~5.8k tokens).
    """
    from app.llm import build_agent_system_blocks

    blocks = build_agent_system_blocks(
        role="extractor",
        agent_instructions="You are the extractor. Do the thing.",
    )
    # 1 instructions + 1 rules + 1 brand + 1 schema addendum
    assert len(blocks) == 4, f"unexpected block count: {blocks}"
    # Every block must be cache-eligible so the underlying helper can
    # tag it with cache_control (up to Anthropic's 4-breakpoint cap).
    cache_flags = [c for (_, c) in blocks]
    assert all(cache_flags), (
        f"cache flags should all be True; got {cache_flags}. "
        "This means an agent-system block is silently NOT eligible "
        "for the ephemeral cache — Extractor at 5.8k tokens/call is "
        "the primary regression risk."
    )


def test_build_agent_system_blocks_toggles_respected() -> None:
    """When a caller opts out of a section, that section doesn't appear
    (so the cache prefix stays stable — no dangling empty block)."""
    from app.llm import build_agent_system_blocks

    blocks = build_agent_system_blocks(
        role="router",
        agent_instructions="router instructions",
        include_rules=False,
        include_brand=False,
        include_schema=False,
    )
    assert len(blocks) == 1
    assert blocks[0][0] == "router instructions"
    assert blocks[0][1] is True


# ─────────────────────── cached_system_message_blocks ───────────────────────


def test_cached_system_message_blocks_emits_cache_control_on_anthropic() -> None:
    """The Anthropic path emits typed text blocks + cache_control markers.

    Non-Anthropic providers collapse to a joined string (tested below).
    """
    from app.llm import cached_system_message_blocks

    blocks = [
        ("instructions", True),
        ("static catalog", True),
    ]
    msg = cached_system_message_blocks(blocks, role="extractor")
    assert isinstance(msg.content, list), (
        f"anthropic path should emit typed content blocks; got "
        f"{type(msg.content).__name__}"
    )
    flags = _cache_control_flags(msg.content)
    assert flags == [True, True], f"expected both blocks cached; got {flags}"
    # Every cache_control marker is ephemeral (5-min TTL).
    for b in msg.content:
        cc = b.get("cache_control")
        assert cc == {"type": "ephemeral"}, (
            f"expected ephemeral cache_control; got {cc}"
        )


def test_cached_system_message_blocks_honors_four_breakpoint_cap() -> None:
    """Anthropic caps ``cache_control`` at 4 per request. When a caller
    passes >4 cache-eligible blocks the earliest extras are demoted to
    plain text so the LAST 4 keep their markers (Anthropic prefers a
    cache prefix rooted at the request start; the demoted early blocks
    still live inside the cached prefix implicitly)."""
    from app.llm import cached_system_message_blocks

    blocks = [(f"block {i}", True) for i in range(6)]
    msg = cached_system_message_blocks(blocks, role="extractor")
    flags = _cache_control_flags(msg.content)
    # Exactly 4 breakpoints — the last 4 blocks.
    assert flags == [False, False, True, True, True, True], flags


def test_cached_system_message_blocks_drops_empty_blocks() -> None:
    """Whitespace-only blocks waste tool-schema tokens and shift the
    cache prefix — they must be dropped BEFORE the cache flags are
    assigned."""
    from app.llm import cached_system_message_blocks

    blocks = [("   ", True), ("real block", True), ("", True)]
    msg = cached_system_message_blocks(blocks, role="extractor")
    assert len(msg.content) == 1
    assert _texts(msg.content) == ["real block"]


def test_cached_system_message_blocks_non_anthropic_collapses_to_string() -> None:
    """OpenAI-compatible path can't consume ``cache_control`` — the
    helper joins to a plain string so downstream providers don't error
    on the typed-block format."""
    from app.llm import cached_system_message_blocks

    # _normalize logs a warning + falls back to anthropic when the
    # provider isn't recognized; force a real non-anthropic provider by
    # patching the role-scoped override, then restore.
    os.environ["ROUTER_LLM_PROVIDER"] = "openai"
    try:
        # Patch the resolver so we exercise the openai branch even
        # though _normalize would clamp it back to anthropic. The
        # branch is the observable contract — keep it exercised.
        from app.llm import cached_system_message_blocks as _fn
        import app.llm as _llm

        original = _llm._provider_for

        def _fake(role):  # type: ignore[no-untyped-def]
            return "openai"  # type: ignore[return-value]

        _llm._provider_for = _fake  # type: ignore[assignment]
        try:
            msg = _fn([("a", True), ("b", True)], role="router")
        finally:
            _llm._provider_for = original  # type: ignore[assignment]
    finally:
        os.environ.pop("ROUTER_LLM_PROVIDER", None)

    assert isinstance(msg.content, str), (
        f"non-anthropic path should collapse to str; got "
        f"{type(msg.content).__name__}"
    )
    assert msg.content == "a\n\nb"


# ─────────────────────── per-agent coverage ───────────────────────


# (role, agent-instructions constant name, importable module path,
#  min instruction-token estimate — safety net so a future prompt
#  edit that accidentally deletes 90% of the block still fails here.)
_AGENT_SPECS: list[tuple[str, str, str, int]] = [
    ("router", "SYSTEM_PROMPT", "app.agents.router", 500),
    # Extractor is the big-ticket item: ~5.8k tokens today, ~5.5k floor.
    ("extractor", "SYSTEM_PROMPT", "app.agents.extractor", 5500),
    ("normalizer", "SYSTEM_PROMPT", "app.agents.normalizer", 500),
    ("analyst", "SYSTEM_PROMPT", "app.agents.analyst", 400),
    ("critic", "CRITIC_SYSTEM_PROMPT", "app.agents.critic", 300),
    ("variance", "SYSTEM_PROMPT", "app.agents.variance", 300),
    ("analyst", "SYSTEM_PROMPT", "app.agents.qa_resolver", 500),
    # Researcher rides on the analyst role in the system-blocks call.
    ("analyst", "SYSTEM_PROMPT", "app.agents.researcher", 300),
    (
        "analyst",
        "SYSTEM_PROMPT",
        "app.agents.due_diligence",
        # Due-diligence loader path calls build_agent_system_blocks
        # with role="due_diligence" (not a real Role literal — see the
        # loader). Guard with the analyst role for token budgeting.
        700,
    ),
]


@pytest.mark.parametrize(
    "role,const_name,module_path,min_tokens", _AGENT_SPECS
)
def test_every_agent_system_prompt_is_cached(
    role: str, const_name: str, module_path: str, min_tokens: int
) -> None:
    """For every agent, assembling the system blocks with its real
    module-level SYSTEM_PROMPT produces a SystemMessage where every
    content block carries a ``cache_control`` marker."""
    import importlib

    from app.llm import build_agent_system_blocks, cached_system_message_blocks

    mod = importlib.import_module(module_path)
    instructions = getattr(mod, const_name)
    assert isinstance(instructions, str), (
        f"{module_path}.{const_name} should be a string; got "
        f"{type(instructions).__name__}"
    )
    # Belt-and-braces: the instructions block must be substantial. A
    # regression that accidentally deletes the prompt body would still
    # emit a (tiny) cached block; this guard catches that.
    approx_tokens = len(instructions) // 4
    assert approx_tokens >= min_tokens, (
        f"{module_path}.{const_name} looks truncated: "
        f"~{approx_tokens} tokens, want >= {min_tokens}."
    )

    blocks = build_agent_system_blocks(
        role=role,  # type: ignore[arg-type]
        agent_instructions=instructions,
    )
    msg = cached_system_message_blocks(blocks, role=role)  # type: ignore[arg-type]
    assert isinstance(msg.content, list), (
        f"anthropic path emitted a plain string for {module_path}; "
        "cache markers cannot fire."
    )
    flags = _cache_control_flags(msg.content)
    assert all(flags), (
        f"{module_path}: some system-blocks are NOT cached: {flags}. "
        "This regresses the ephemeral-cache coverage for that agent."
    )


def test_extractor_dynamic_schema_prompt_still_cached() -> None:
    """When ``EXTRACTOR_USE_DYNAMIC_SCHEMAS=1`` swaps in a per-doc-type
    schema (base preamble + doc-type Markdown) as the agent-instructions
    block, that combined block must ALSO be cached — otherwise the
    dynamic-schema path silently regresses cache coverage vs the legacy
    embedded SYSTEM_PROMPT path."""
    os.environ["EXTRACTOR_USE_DYNAMIC_SCHEMAS"] = "1"
    try:
        from app.agents.extraction_schemas.loader import build_system_prompt
        from app.llm import build_agent_system_blocks, cached_system_message_blocks

        prompt = build_system_prompt("T12")
        assert prompt is not None, "dynamic-schema path returned None with flag=1"
        # _base.md + t12.md concatenated — should be substantial.
        assert len(prompt) > 3000, (
            f"dynamic prompt is only {len(prompt)} chars — schema loader "
            "may have dropped a file."
        )
        blocks = build_agent_system_blocks(
            role="extractor", agent_instructions=prompt
        )
        msg = cached_system_message_blocks(blocks, role="extractor")
        assert isinstance(msg.content, list)
        assert all(_cache_control_flags(msg.content)), (
            "dynamic-schema extractor prompt lost cache coverage: "
            f"{_cache_control_flags(msg.content)}"
        )
    finally:
        os.environ.pop("EXTRACTOR_USE_DYNAMIC_SCHEMAS", None)


# ─────────────────────── static block sanity ───────────────────────


def test_usali_rules_and_brand_catalog_are_substantial() -> None:
    """Both catalog helpers should return non-trivial content — if
    either becomes empty (missing file / parse error) the cache markers
    still fire but cache the wrong bytes. Assert a floor so a corrupted
    catalog file surfaces here rather than silently."""
    from app.llm import brand_catalog_as_prompt_block
    from app.usali_rules import rules_as_prompt_block

    rules = rules_as_prompt_block()
    brand = brand_catalog_as_prompt_block()
    assert len(rules) > 500, f"USALI rules block looks empty: {len(rules)} chars"
    assert len(brand) > 200, f"brand catalog block looks empty: {len(brand)} chars"
