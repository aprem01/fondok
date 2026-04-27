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

import logging
import os
from typing import Any, Literal

from langchain_core.messages import SystemMessage

from .config import get_settings

logger = logging.getLogger(__name__)

Provider = Literal["anthropic"]
Role = Literal["router", "extractor", "normalizer", "analyst", "variance"]


def _default_model(provider: Provider, role: Role) -> str:
    settings = get_settings()
    if provider == "anthropic":
        if role == "router":
            return settings.ANTHROPIC_ROUTER_MODEL
        if role == "extractor":
            return settings.ANTHROPIC_EXTRACTOR_MODEL
        if role == "normalizer":
            return settings.ANTHROPIC_NORMALIZER_MODEL
        if role in ("analyst", "variance"):
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
    blocks: list[str], *, role: Role
) -> SystemMessage:
    """SystemMessage with one Anthropic prompt-cache breakpoint per block.

    Anthropic allows up to 4 ``cache_control`` breakpoints per request.
    Splitting role-level system prompts (tenant-agnostic, ~500 tokens)
    from per-tenant catalogues (USALI rules, brand catalog, market
    data — often 2-20k tokens) means switching tenants only invalidates
    the trailing block while the role prompt continues to hit cache.

    On a cache hit Anthropic charges 10% of normal input cost on the
    cached prefix, so this materially reduces both latency and spend
    once the same agent runs more than once in the 5-minute TTL.

    For non-Anthropic providers we collapse to a plain joined string —
    ``cache_control`` is silently ignored on the OpenAI-compatible path.
    """
    provider = _provider_for(role)
    if provider != "anthropic":
        return SystemMessage(content="\n\n".join(blocks))
    return SystemMessage(
        content=[
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
            for text in blocks
        ]
    )


def cached_system_message(content: str, *, role: Role) -> SystemMessage:
    """One-block convenience wrapper around ``cached_system_message_blocks``."""
    return cached_system_message_blocks([content], role=role)


__all__ = [
    "Provider",
    "Role",
    "build_llm",
    "build_structured_llm",
    "cached_system_message",
    "cached_system_message_blocks",
]
