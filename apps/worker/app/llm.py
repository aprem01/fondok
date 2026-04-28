"""Provider-agnostic LLM factory.

Every agent calls ``build_llm(role=...)`` rather than constructing
``ChatAnthropic`` directly. The factory reads:

* ``LLM_PROVIDER`` — global default (currently only ``"anthropic"``)
* ``<ROLE>_LLM_PROVIDER`` — per-role override env var
* per-role model env vars (``ANTHROPIC_ROUTER_MODEL``,
  ``ANTHROPIC_EXTRACTOR_MODEL``, ``ANTHROPIC_NORMALIZER_MODEL``,
  ``ANTHROPIC_ANALYST_MODEL``)

Roles map to the four agent stages:

* ``router``     → cheap classification (Haiku)
* ``extractor``  → STR / P&L parsing (Sonnet)
* ``normalizer`` → unit + chart-of-accounts normalization (Sonnet)
* ``analyst``    → IC memo + variance reasoning (Opus)
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import SystemMessage

from .config import get_settings

logger = logging.getLogger(__name__)

Provider = Literal["anthropic"]
Role = Literal["router", "extractor", "normalizer", "analyst", "variance", "critic"]


def _default_model(provider: Provider, role: Role) -> str:
    settings = get_settings()
    if provider == "anthropic":
        if role == "router":
            return settings.ANTHROPIC_ROUTER_MODEL
        if role == "extractor":
            return settings.ANTHROPIC_EXTRACTOR_MODEL
        if role == "normalizer":
            return settings.ANTHROPIC_NORMALIZER_MODEL
        if role in ("analyst", "variance", "critic"):
            return settings.ANTHROPIC_ANALYST_MODEL
        return settings.ANTHROPIC_MODEL
    return settings.ANTHROPIC_MODEL


def _role_model(role: Role, provider: Provider) -> str:
    """Resolve the per-role model env var name.

    Priority:
        1. ``<PROVIDER>_<ROLE>_MODEL`` (e.g. ``ANTHROPIC_ANALYST_MODEL``)
        2. ``<PROVIDER>_MODEL`` (legacy single default)
        3. The hard-coded role default in ``_default_model``.
    """
    prefix = provider.upper()
    role_key = f"{prefix}_{role.upper()}_MODEL"
    fallback_key = f"{prefix}_MODEL"
    return (
        os.environ.get(role_key)
        or os.environ.get(fallback_key)
        or _default_model(provider, role)
    )


def _provider_for(role: Role) -> Provider:
    """Resolve the active provider for a given role."""
    role_override = os.environ.get(f"{role.upper()}_LLM_PROVIDER")
    if role_override:
        return _normalize(role_override)
    settings = get_settings()
    return _normalize(settings.LLM_PROVIDER)


def _normalize(raw: str) -> Provider:
    v = raw.strip().lower()
    if v != "anthropic":
        logger.warning(
            "LLM_PROVIDER=%r not yet supported in fondok worker — "
            "falling back to anthropic",
            raw,
        )
    return "anthropic"


def build_llm(
    *,
    role: Role,
    max_tokens: int,
    timeout: int,
    temperature: float | None = None,
) -> Any:
    """Construct a configured chat model for ``role``.

    Returns a LangChain chat runnable. Callers usually chain
    ``.with_structured_output(SchemaT)`` on top via
    ``build_structured_llm``.
    """
    # Imported lazily so test runs that never call build_llm don't
    # require langchain_anthropic to be installed.
    from langchain_anthropic import ChatAnthropic

    provider = _provider_for(role)
    settings = get_settings()
    if settings.ANTHROPIC_API_KEY is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — cannot construct LLM client"
        )

    model = _role_model(role, provider)
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": settings.ANTHROPIC_API_KEY,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    # Opus 4.7 rejects the temperature parameter; set only on others.
    if temperature is not None and "opus-4-7" not in model:
        kwargs["temperature"] = temperature

    logger.info(
        "llm: role=%s provider=%s model=%s max_tokens=%d",
        role,
        provider,
        model,
        max_tokens,
    )
    return ChatAnthropic(**kwargs)  # type: ignore[arg-type]


def build_structured_llm(
    *,
    role: Role,
    schema: Any,
    max_tokens: int,
    timeout: int,
    temperature: float | None = None,
) -> Any:
    """Build a chat model with structured output bound to ``schema``."""
    base = build_llm(
        role=role,
        max_tokens=max_tokens,
        timeout=timeout,
        temperature=temperature,
    )
    return base.with_structured_output(schema)


def cached_system_message_blocks(
    blocks: list[str | tuple[str, bool] | dict],
    *,
    role: Role,
) -> SystemMessage:
    """SystemMessage with selectively cached Anthropic prompt-cache breakpoints.

    Anthropic allows up to 4 ``cache_control`` breakpoints per request.
    Each block may be either:
      * ``str`` — text block, cached by default
      * ``(text, cache_bool)`` tuple — text + explicit cache flag
      * ``{"text": str, "cache": bool}`` dict — same as above

    Block-tuning recipe (the layout the agents pass in):
      1. Agent-specific instructions    — small (~500 tok), changes per
         agent, NO cache (would invalidate downstream blocks anyway).
      2. USALI rules catalog            — stable across tenants → CACHE.
      3. Brand catalog                  — stable across tenants → CACHE.
      4. Per-agent extraction schema /
         hotel-specific addendum        — stable per role → CACHE.

    The Anthropic cap is 4 breakpoints; we tag the LAST contiguous run
    of cache-eligible blocks (up to 4) with ``cache_control`` and emit
    the rest as plain text. This lets a 5-block prompt still fit the
    breakpoint budget without dropping cache hits.

    On a cache hit Anthropic charges 10% of normal input cost on the
    cached prefix, so this materially reduces both latency and spend
    once the same agent runs more than once in the 5-minute TTL.

    For non-Anthropic providers we collapse to a plain joined string —
    ``cache_control`` is silently ignored on the OpenAI-compatible path.
    """
    # Normalize all inputs to (text, cache_eligible) tuples.
    norm: list[tuple[str, bool]] = []
    for b in blocks:
        if isinstance(b, str):
            norm.append((b, True))
        elif isinstance(b, tuple) and len(b) == 2:
            norm.append((str(b[0]), bool(b[1])))
        elif isinstance(b, dict):
            norm.append((str(b.get("text", "")), bool(b.get("cache", True))))
        else:
            norm.append((str(b), True))

    # Drop empty blocks — they only waste tool-schema tokens.
    norm = [(t, c) for (t, c) in norm if t and t.strip()]

    provider = _provider_for(role)
    if provider != "anthropic":
        return SystemMessage(content="\n\n".join(t for t, _ in norm))

    # Honor Anthropic's 4-breakpoint cap by reserving the LAST 4
    # cache-eligible blocks. This matters when a caller passes >4 blocks.
    cache_eligible_idx = [i for i, (_, c) in enumerate(norm) if c]
    if len(cache_eligible_idx) > 4:
        # Demote the earliest extras to non-cache so we stay under the cap.
        for i in cache_eligible_idx[:-4]:
            t, _ = norm[i]
            norm[i] = (t, False)

    content: list[dict] = []
    for text, cache in norm:
        block: dict = {"type": "text", "text": text}
        if cache:
            block["cache_control"] = {"type": "ephemeral"}
        content.append(block)
    return SystemMessage(content=content)


def cached_system_message(content: str, *, role: Role) -> SystemMessage:
    """One-block convenience wrapper around ``cached_system_message_blocks``."""
    return cached_system_message_blocks([content], role=role)


# ─────────────────────── shared catalog loaders ───────────────────────


_DEFAULT_BRAND_CATALOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "golden-set"
    / "brand-catalog.json"
)


@lru_cache(maxsize=1)
def brand_catalog_as_prompt_block() -> str:
    """Load the brand catalog and render it as a stable prompt block.

    Cached for the process lifetime so cache key hashing on the
    Anthropic side can find the block on every call.
    """
    override = os.environ.get("FONDOK_BRAND_CATALOG_PATH")
    path = Path(override).expanduser().resolve() if override else _DEFAULT_BRAND_CATALOG_PATH
    if not path.exists():
        logger.info("brand-catalog: not found at %s — empty block", path)
        return "=== BRAND CATALOG ===\n(unavailable)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("brand-catalog: failed to read %s: %s", path, exc)
        return "=== BRAND CATALOG ===\n(parse error)"
    # Pretty-print with stable key ordering so the block hashes the
    # same way on every call.
    body = json.dumps(data, indent=2, sort_keys=True)
    return f"=== BRAND CATALOG ===\n{body}"


# Per-agent extraction-schema addendum. These are short, stable hints
# the LLM uses to pick the right field paths and units; splitting them
# into their own cached block (instead of inlining into the agent
# prompt) keeps the agent-specific block small and lets the schema
# addendum live in the cache prefix shared across tenants.
_EXTRACTION_SCHEMA_BLOCKS: dict[str, str] = {
    "router": (
        "=== ROUTER SCHEMA ADDENDUM ===\n"
        "DocType tokens: OM | T12 | STR | RENT_ROLL | PNL | "
        "MARKET_STUDY | CONTRACT | UNKNOWN.\n"
        "Confidence is in [0, 1]; <0.7 implies UNKNOWN.\n"
        "Reasoning is one short sentence (<=80 words)."
    ),
    "extractor": (
        "=== EXTRACTOR SCHEMA ADDENDUM ===\n"
        "Field paths use dotted notation rooted at the source document:\n"
        "  asking_price.headline_price_usd\n"
        "  property_overview.{name,keys,year_built,address,brand}\n"
        "  broker_proforma.{rooms_revenue_usd,fb_revenue_usd,noi_usd,...}\n"
        "  ttm_summary_per_om.{occupancy_pct,adr_usd,revpar_usd}\n"
        "  in_place_debt.{loan_balance_usd,rate_pct,maturity_date}\n"
        "  p_and_l_usali.<bucket>.<line> for T-12.\n"
        "  ttm_performance.{subject,comp_set,indices}.* for STR.\n"
        "Units: USD (no symbols), pct (decimal 0..1), keys, ratio, count, date."
    ),
    "normalizer": (
        "=== NORMALIZER SCHEMA ADDENDUM ===\n"
        "Output schema USALINormalized has fields:\n"
        "  rooms_revenue, fb_revenue, other_revenue, total_revenue\n"
        "  dept_expenses{rooms,food_beverage,other_operated,total}\n"
        "  undistributed{administrative_general,information_telecom,"
        "sales_marketing,property_operations,utilities,total}\n"
        "  mgmt_fee, ffe_reserve\n"
        "  fixed_charges{property_taxes,insurance,rent,other_fixed,total}\n"
        "  gop, noi, opex_ratio\n"
        "  occupancy, adr, revpar (optional)\n"
        "All amounts USD, occupancy in [0,1]."
    ),
    "variance": (
        "=== VARIANCE SCHEMA ADDENDUM ===\n"
        "Each note entry is {field, rule_id, note}. Match (field, rule_id)\n"
        "to one of the deterministic flags exactly — order preserved."
    ),
    "analyst": (
        "=== ANALYST SCHEMA ADDENDUM ===\n"
        "Section ids: investment_thesis | market_analysis | deal_overview |\n"
        "  financial_analysis | risk_factors | recommendation.\n"
        "Each section emits {section_id, title, body, citations[>=1]}.\n"
        "Citations: {document_id, page, field?, excerpt?} pointing at the\n"
        "Source Documents the orchestrator surfaces — never invent ids."
    ),
    "critic": (
        "=== CRITIC SCHEMA ADDENDUM ===\n"
        "Each finding emits {rule_id, title, narrative, severity,\n"
        "  cited_fields[], cited_pages[], impact_estimate_usd?}.\n"
        "rule_id MUST come from the USALI catalog OR be a MULTI_FIELD_*\n"
        "  rule from the Cross-Field Rules block — unknown ids are dropped.\n"
        "severity is one of CRITICAL | WARN | INFO.\n"
        "cited_fields enumerate canonical USALI field names involved\n"
        "  (e.g. ['noi', 'opex_ratio', 'mgmt_fee']).\n"
        "narrative is plain hotel-underwriting English, <=400 words,\n"
        "  reads like a senior IC reviewer wrote it."
    ),
}


def extraction_schema_block(role: Role) -> str:
    """Per-agent schema reminder, kept stable so it lives in the cache prefix."""
    return _EXTRACTION_SCHEMA_BLOCKS.get(
        role, "=== SCHEMA ADDENDUM ===\n(none registered for this role)"
    )


def build_agent_system_blocks(
    *,
    role: Role,
    agent_instructions: str,
    include_rules: bool = True,
    include_brand: bool = True,
    include_schema: bool = True,
) -> list[tuple[str, bool]]:
    """Assemble the canonical 4-block system prompt for an agent.

    Block order (matches the cache-tuning recipe in the docstring):
      1. Agent instructions  — uncached (per-agent, small).
      2. USALI rules catalog — cached.
      3. Brand catalog       — cached.
      4. Schema addendum     — cached.

    Callers should pass the result straight to
    ``cached_system_message_blocks(blocks, role=role)``.
    """
    # Imported lazily to avoid a hard import cycle at module load.
    from .usali_rules import rules_as_prompt_block

    blocks: list[tuple[str, bool]] = []
    blocks.append((agent_instructions, False))
    if include_rules:
        blocks.append((rules_as_prompt_block(), True))
    if include_brand:
        blocks.append((brand_catalog_as_prompt_block(), True))
    if include_schema:
        blocks.append((extraction_schema_block(role), True))
    return blocks


__all__ = [
    "Provider",
    "Role",
    "brand_catalog_as_prompt_block",
    "build_agent_system_blocks",
    "build_llm",
    "build_structured_llm",
    "cached_system_message",
    "cached_system_message_blocks",
    "extraction_schema_block",
]
