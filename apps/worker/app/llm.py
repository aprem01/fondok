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
Role = Literal[
    "router",
    "extractor",
    "normalizer",
    "analyst",
    "variance",
    "critic",
    "qa_resolver",
    "due_diligence",
]


def _default_model(provider: Provider, role: Role) -> str:
    settings = get_settings()
    if provider == "anthropic":
        if role == "router":
            return settings.ANTHROPIC_ROUTER_MODEL
        if role == "extractor":
            return settings.ANTHROPIC_EXTRACTOR_MODEL
        if role == "normalizer":
            return settings.ANTHROPIC_NORMALIZER_MODEL
        if role == "qa_resolver":
            return settings.ANTHROPIC_QA_RESOLVER_MODEL
        if role == "due_diligence":
            return settings.ANTHROPIC_DUE_DILIGENCE_MODEL
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


def _resolve_max_retries(role: Role, override: int | None) -> int:
    """Resolve the SDK-level ``max_retries`` for ``role``.

    Priority:
        1. Explicit ``override`` passed by the caller (used by tests).
        2. ``<ROLE>_LLM_MAX_RETRIES`` (e.g. ``EXTRACTOR_LLM_MAX_RETRIES``).
        3. ``LLM_MAX_RETRIES``.
        4. Per-role hardcoded default: ``extractor`` → 6 (Sam QA 2026-06-30:
           burst uploads of 8 docs triggered Anthropic ``overloaded_error``
           (HTTP 529) bursts; the default 2 retries with exp-backoff was
           insufficient and 3/8 docs landed FAILED with ``empty_envelope``).
           All other roles inherit langchain_anthropic's default of 2.

    The Anthropic SDK already retries 408/409/429 + every 5xx with an
    exponential backoff + jitter (~0.5s, ~1s, ~2s, ~4s …), so bumping
    this is essentially "wait longer before giving up under overload";
    it does NOT cause retries on hard 4xx (validation, auth, etc.).
    """
    if override is not None:
        return max(0, int(override))
    role_key = f"{role.upper()}_LLM_MAX_RETRIES"
    raw = os.environ.get(role_key) or os.environ.get("LLM_MAX_RETRIES")
    if raw is not None and raw.strip() != "":
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            logger.warning(
                "llm: %s=%r is not an int — falling back to per-role default",
                role_key, raw,
            )
    # Per-role defaults: extractor pays the price of burst-load
    # overload errors more than any other agent (Sam QA 2026-06-30
    # empty_envelope investigation), so it gets the most aggressive
    # retry budget. Other roles keep langchain_anthropic's default 2.
    if role == "extractor":
        return 6
    return 2


def build_llm(
    *,
    role: Role,
    max_tokens: int,
    timeout: int,
    temperature: float | None = None,
    max_retries: int | None = None,
) -> Any:
    """Construct a configured chat model for ``role``.

    Returns a LangChain chat runnable. Callers usually chain
    ``.with_structured_output(SchemaT)`` on top via
    ``build_structured_llm``.

    ``max_retries`` overrides the SDK-level retry count for transient
    failures (429 / 5xx / 529 overloaded). When ``None``, falls back to
    :func:`_resolve_max_retries` which reads
    ``<ROLE>_LLM_MAX_RETRIES`` / ``LLM_MAX_RETRIES`` from the env and
    defaults to 6 for ``extractor`` (Sam QA 2026-06-30 empty_envelope
    investigation — burst-load overload errors exceeded the previous
    default of 2).
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
    resolved_retries = _resolve_max_retries(role, max_retries)
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": settings.ANTHROPIC_API_KEY,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "max_retries": resolved_retries,
    }
    # Opus 4.7 rejects the temperature parameter; set only on others.
    if temperature is not None and "opus-4-7" not in model:
        kwargs["temperature"] = temperature

    logger.info(
        "llm: role=%s provider=%s model=%s max_tokens=%d max_retries=%d",
        role,
        provider,
        model,
        max_tokens,
        resolved_retries,
    )
    return ChatAnthropic(**kwargs)  # type: ignore[arg-type]


def build_structured_llm(
    *,
    role: Role,
    schema: Any,
    max_tokens: int,
    timeout: int,
    temperature: float | None = None,
    include_raw: bool = False,
    method: str = "function_calling",
    max_retries: int | None = None,
) -> Any:
    """Build a chat model with structured output bound to ``schema``.

    When ``include_raw=True`` the runnable returns a dict
    ``{"raw": AIMessage, "parsed": SchemaT|None, "parsing_error":
    Exception|None}`` instead of the parsed object directly. Useful when
    the structured-output path occasionally returns an empty envelope
    on large inputs (observed on 45-page OMs) — the caller can inspect
    the raw text response and salvage JSON manually.

    ``method`` selects langchain_anthropic's parser:
      * ``function_calling`` (default) — uses Anthropic tool-calling.
        Reliable for small outputs; on large structured envelopes it
        sometimes drops the tool args dict, leaving the parsed object
        empty (Sam QA 2026-05-13).
      * ``json_schema`` — instructs the model to emit JSON inline in
        the message body. More resilient on large outputs because the
        parser only requires valid JSON, not a tool call.
    """
    base = build_llm(
        role=role,
        max_tokens=max_tokens,
        timeout=timeout,
        temperature=temperature,
        max_retries=max_retries,
    )
    return base.with_structured_output(
        schema, include_raw=include_raw, method=method
    )


async def invoke_with_escalation(
    *,
    role: Role,
    schema: Any,
    messages: list[Any],
    max_tokens: int,
    timeout: int,
    temperature: float | None = None,
    method: str = "function_calling",
    usage: Any | None = None,
    escalation_threshold: int | None = None,
    llm: Any | None = None,
) -> tuple[Any, str]:
    """Invoke a structured-output LLM with parse-failure escalation.

    Cost-opt pass T (2026-07): several agents were downgraded from Sonnet
    to Haiku (Normalizer, QA Resolver). Haiku occasionally fumbles a
    JSON envelope on gnarly inputs — this helper wraps the invoke with
    an escalation lane so the *specific* call that fails N times in a
    row re-issues on Sonnet before surfacing the failure to the caller.

    Behavior:
      1. Build a structured LLM for ``role`` (whatever model the role's
         env var points at — Haiku for downgraded roles). Callers can
         pass a pre-built ``llm`` (used by agents that want to preserve
         their existing ``_build_llm`` mock seams for unit tests).
      2. Invoke it. If it returns a schema-typed object, return.
      3. On ValidationError / ValueError / structured-output parse
         failure, count the miss and retry on the same LLM up to
         ``escalation_threshold - 1`` times. Anthropic 5xx/429 are
         already handled inside the SDK layer's ``max_retries`` and
         do NOT count toward this budget.
      4. If misses reach ``escalation_threshold``, escalate: rebuild
         the LLM with ``ANTHROPIC_ESCALATION_MODEL`` (Sonnet) and
         invoke once more. Return that result (or raise the parse
         error if Sonnet also fails).

    Returns ``(parsed_result, model_used)`` — the caller can log or
    persist ``model_used`` so cost dashboards see when escalation fired.

    ``usage`` is the shared UsageCapture the caller uses to bookkeep
    tokens. Passed as ``config={"callbacks": [usage]}`` to every call
    so the token ledger is unbroken across escalation.
    """
    from pydantic import ValidationError as _ValidationError

    settings = get_settings()
    threshold = (
        escalation_threshold
        if escalation_threshold is not None
        else settings.LLM_ESCALATION_THRESHOLD
    )
    threshold = max(1, int(threshold))

    if llm is None:
        llm = build_structured_llm(
            role=role,
            schema=schema,
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
            method=method,
        )
    config = {"callbacks": [usage]} if usage is not None else None
    model_used = _role_model(role, _provider_for(role))
    misses = 0
    last_exc: Exception | None = None

    while misses < threshold:
        try:
            raw = await llm.ainvoke(messages, config=config)
        except (_ValidationError, ValueError) as exc:
            last_exc = exc
            misses += 1
            logger.warning(
                "llm: parse miss %d/%d on role=%s model=%s (%s)",
                misses,
                threshold,
                role,
                model_used,
                exc,
            )
            continue
        # Some LLM clients return a dict / BaseModel that needs coercion.
        # A non-None, non-error return is treated as success — the caller
        # is responsible for schema-shaping post-return.
        if raw is None:
            last_exc = ValueError("structured-output returned None")
            misses += 1
            logger.warning(
                "llm: empty structured envelope %d/%d on role=%s model=%s",
                misses,
                threshold,
                role,
                model_used,
            )
            continue
        return raw, model_used

    # Escalate to the fallback (Sonnet) — same schema + prompt, stronger model.
    escalation_model = settings.ANTHROPIC_ESCALATION_MODEL
    if escalation_model == model_used:
        # No escalation to make — the role is already on the escalation
        # model. Surface the last error verbatim.
        raise last_exc or ValueError(
            f"llm: role={role} failed {threshold} times, no escalation available"
        )

    logger.warning(
        "llm: escalating role=%s from %s → %s after %d parse misses",
        role,
        model_used,
        escalation_model,
        misses,
    )
    # Rebuild with an explicit env override so the escalation path uses
    # the fallback model without mutating the process-wide env var
    # (which would poison other agents' subsequent calls). We reach past
    # ``build_structured_llm`` and construct ChatAnthropic directly with
    # the escalation model id.
    from langchain_anthropic import ChatAnthropic  # imported lazily

    if settings.ANTHROPIC_API_KEY is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — cannot escalate LLM call"
        )
    kwargs: dict[str, Any] = {
        "model": escalation_model,
        "api_key": settings.ANTHROPIC_API_KEY,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "max_retries": _resolve_max_retries(role, None),
    }
    if temperature is not None and "opus-4-7" not in escalation_model:
        kwargs["temperature"] = temperature
    base = ChatAnthropic(**kwargs)  # type: ignore[arg-type]
    escalated = base.with_structured_output(schema, method=method)
    raw = await escalated.ainvoke(messages, config=config)
    if raw is None:
        raise last_exc or ValueError(
            f"llm: escalation to {escalation_model} also returned empty envelope"
        )
    return raw, escalation_model


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
      1. Agent-specific instructions    — static per role/doc-type
         (Extractor's is ~5.8k tokens) → CACHE.
      2. USALI rules catalog            — stable across tenants → CACHE.
      3. Brand catalog                  — stable across tenants → CACHE.
      4. Per-agent extraction schema /
         hotel-specific addendum        — stable per role → CACHE.

    All 4 blocks fit inside Anthropic's 4-breakpoint cap. Callers that
    add a 5th cache-eligible block will have the earliest cache marker
    demoted (see the cap-enforcement below).

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
    "qa_resolver": (
        "=== QA RESOLVER SCHEMA ADDENDUM ===\n"
        "Emit one ResolverEnvelope: {verdict, summary, proposed_overrides[],\n"
        "  audit_note}. Verdict is resolved|partially_resolved|still_concerning.\n"
        "field_path MUST come from the allow-list in the user prompt — any\n"
        "  off-catalog path is dropped downstream, so name a path exactly.\n"
        "value: 0..1 fraction for cap_rate/ltv/occupancy/interest_rate;\n"
        "  raw USD otherwise. confidence ∈ {high, medium, low}."
    ),
    "due_diligence": (
        "=== DUE DILIGENCE SCHEMA ADDENDUM ===\n"
        "Emit DueDiligenceEnvelope.questions[] — target 8-15 rows.\n"
        "Each row: {question, narrative, priority, category, source,\n"
        "  supporting_metric_key, supporting_metric_value}.\n"
        "priority ∈ {high, medium, low}; category ∈ {Revenue, Expenses,\n"
        "  Operations, Market, CapEx}. question ends with '?'."
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
      1. Agent instructions  — CACHED. Static per role/doc-type. The
         Extractor's SYSTEM_PROMPT alone is ~5.8k tokens and is
         re-issued once per chunked ExtractorDocument (a 45-page OM
         fans out to ~9 chunks). Explicitly marking a breakpoint here
         gives the instructions block its own cache slot so the prefix
         still hits when a downstream block (rules / brand / schema
         addendum) shifts.
      2. USALI rules catalog — CACHED.
      3. Brand catalog       — CACHED.
      4. Schema addendum     — CACHED.

    Anthropic caps ``cache_control`` at 4 breakpoints per request; the
    four blocks above land exactly at the cap. ``cached_system_message_blocks``
    also enforces the cap defensively (demoting oldest cache-eligible
    blocks to plain text if a caller passes more than 4).

    Cost-opt 2026-06-27 (batch O): the agent-instructions block was
    previously marked ``cache=False`` on the theory that it was
    "small" and "changes per agent". Both are wrong for the Extractor
    (largest agent instructions in the codebase, ~5.8k tokens, static
    per doc_type) and for streaming Analyst (6 sequential per-section
    calls with byte-identical system prompt). Marking a breakpoint on
    the instructions block gives 90% savings on the instructions
    tokens across the second and subsequent calls in the 5-minute
    ephemeral TTL — the dominant cost driver on multi-chunk extractions.

    Callers should pass the result straight to
    ``cached_system_message_blocks(blocks, role=role)``.
    """
    # Imported lazily to avoid a hard import cycle at module load.
    from .usali_rules import rules_as_prompt_block

    blocks: list[tuple[str, bool]] = []
    blocks.append((agent_instructions, True))
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
    "invoke_with_escalation",
]
