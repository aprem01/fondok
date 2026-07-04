"""Application configuration loaded from environment variables.

Validation runs at import time; a missing required variable raises
``pydantic.ValidationError`` and prevents the worker from booting with
half-configured state.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ── Data plane ──────────────────────────────────────────────────
    # Default to a local sqlite file so `uv run uvicorn ...` works
    # without provisioning Postgres. Production sets the asyncpg DSN.
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./fondok.db")

    # ── LLM providers ───────────────────────────────────────────────
    ANTHROPIC_API_KEY: SecretStr | None = Field(default=None)
    LLM_PROVIDER: str = Field(default="anthropic")

    # ── Anthropic models per agent role ─────────────────────────────
    # Router uses Haiku — cheap classification of incoming docs / requests.
    ANTHROPIC_ROUTER_MODEL: str = Field(default="claude-haiku-4-5-20251001")
    # Extractor uses Sonnet — primary field extraction, quality-critical.
    ANTHROPIC_EXTRACTOR_MODEL: str = Field(default="claude-sonnet-4-6")
    # Cost-opt pass T (2026-07): Normalizer downgraded from Sonnet 4.6
    # to Haiku 4.5. The Normalizer's job is synonym-mapping extracted
    # line items onto the ~30 canonical USALI buckets — a classification
    # task, not a reasoning task. Rollup identities (GOP, NOI, RevPAR)
    # are recomputed deterministically in ``_validate_and_recompute``,
    # so the LLM's numerical error surface is bounded to "picked the
    # wrong bucket for this line item", which Haiku handles well on
    # the golden set. On repeated JSON-parse or ValidationError the
    # ``escalate_on_parse_failure`` helper re-issues the call with
    # Sonnet 4.6 as an escape hatch.
    ANTHROPIC_NORMALIZER_MODEL: str = Field(default="claude-haiku-4-5-20251001")
    # QA Resolver — reads a broker's Q&A reply and proposes overrides
    # against a ~30-path allow-list. Downstream ``_filter_overrides``
    # drops any off-catalog path, so the LLM's degrees of freedom are
    # already fenced in. Classification-shaped → Haiku.
    ANTHROPIC_QA_RESOLVER_MODEL: str = Field(default="claude-haiku-4-5-20251001")
    # Due Diligence — generates 8-15 open-ended broker questions with
    # narratives. Reasoning-heavier than a pure classifier and shipped
    # to institutional buyers, so we keep it on Sonnet for now. Kept
    # as an explicit setting so we can flip it to Haiku after the next
    # golden-set sweep proves quality is preserved.
    ANTHROPIC_DUE_DILIGENCE_MODEL: str = Field(default="claude-sonnet-4-6")
    # Analyst uses Opus — IC memo writing + variance reasoning.
    ANTHROPIC_ANALYST_MODEL: str = Field(default="claude-opus-4-7")
    # Escalation model — when a Haiku-downgraded agent fails JSON parse
    # ``LLM_ESCALATION_THRESHOLD`` times in a row on a single call, the
    # ``escalate_on_parse_failure`` helper re-issues on this model.
    ANTHROPIC_ESCALATION_MODEL: str = Field(default="claude-sonnet-4-6")
    LLM_ESCALATION_THRESHOLD: int = Field(default=2, ge=1)
    # Catch-all fallback when a per-role var isn't set.
    ANTHROPIC_MODEL: str = Field(default="claude-sonnet-4-6")

    # ── Cost guardrails ─────────────────────────────────────────────
    # Hard cap on a single deal's total LLM spend. Zero disables.
    DEFAULT_DEAL_BUDGET_USD: float = Field(default=20.0, ge=0.0)
    DEAL_BUDGET_WARN_AT: float = Field(default=0.8, ge=0.0, le=1.0)

    # Cost-optimization pass R (2026-07): strip formatting noise (long
    # whitespace runs, decorative rules, duplicate page headers) from
    # parsed doc text before it hits the extractor prompt. Empirically
    # trims 15–25% of input tokens with no measurable quality change.
    # Flip to false to bypass compaction while debugging a lossy
    # extraction — the parser cache is lossless either way, so no data
    # is lost; only the LLM's view of the doc changes.
    PARSER_COMPACTION_ENABLED: bool = Field(default=True)

    # Cost-optimization pass N (2026-07): content-hash extraction cache.
    # When True (default), an upload whose bytes SHA-256 matches a prior
    # successful extraction on the SAME tenant + SAME pipeline version
    # skips the Router → Extractor → Normalizer → Verifier chain and
    # clones the prior result into a new extraction_results row. Zero
    # LLM cost. Cross-tenant lookups are hard-blocked. Flip to False
    # when debugging a suspected stale-cache issue so every doc runs
    # the full extractor for one deploy.
    EXTRACTION_CACHE_ENABLED: bool = Field(default=True)

    # Cost-optimization pass U (2026-07): chunk-size tuning for the
    # extractor. Fewer / larger chunks reduce per-call system-prompt
    # overhead (prompt caching mitigates but doesn't erase it) at the
    # cost of extraction fidelity — the extractor prompt is a fixed
    # size, so a bigger chunk shares less attention per field. More /
    # smaller chunks push fidelity up but pay the system-prompt token
    # cost N times. Empirical values below come from
    # ``apps/worker/scripts/bench_chunk_size.py`` — re-run when the
    # extractor prompt or model changes materially. Env-overridable via
    # ``EXTRACTOR_CHUNK_PAGES_DEFAULT`` for the fallback and (rarely)
    # ``EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE`` as JSON.
    EXTRACTOR_CHUNK_PAGES_DEFAULT: int = Field(default=5, gt=0, le=64)
    # Per doc-type overrides. Keys are ``DocType`` enum values (strings).
    # Docs whose type is not listed fall back to
    # ``EXTRACTOR_CHUNK_PAGES_DEFAULT``. Bench findings that motivated
    # the current defaults:
    #   * ``OM``           — long prose PDFs; larger chunks amortize the
    #     system-prompt cost with negligible fidelity loss.
    #   * ``T12`` / ``PNL`` — dense P&L grids; smaller chunks preserve
    #     line-item resolution and keep USALI compliance above the
    #     regression band.
    #   * ``STR_TREND``    — multi-sheet workbook; 4 sheets per chunk
    #     keeps each subject / comp-set section coherent.
    #   * ``PORTFOLIO_PNL`` — same shape as ``PNL``, benefits from the
    #     same tighter chunking.
    # Override via ``EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE`` env as JSON
    # (e.g. ``{"OM": 10}``) — pydantic-settings JSON-decodes dict envs
    # automatically. Unlisted / unknown DocType keys are ignored.
    EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE: dict[str, int] = Field(
        default_factory=lambda: {
            "OM": 8,
            # T12 + PNL family updated 2026-07-02 from live bench
            # (apps/worker/scripts/bench_chunk_size.py --case anglers_t12
            # against real Anthropic). Measured on Sam's 6-page T12 xlsx:
            #   k=3: $0.36, USALI 61.9  (was the previous default)
            #   k=5: $0.33, USALI 61.9
            #   k=8: $0.21, USALI 78.6  ← 42% cheaper + 27% higher score
            # Smaller-chunk mode over-extracted noisy fields that hurt
            # USALI compliance; larger chunks give the model whole-doc
            # context and it grounds better. Applied to the whole PNL
            # family since they share the annual-xlsx shape; still
            # measured only on T12 — re-bench PNL / PNL_MONTHLY /
            # PNL_YTD / PORTFOLIO_PNL / PNL_BENCHMARK when a fuller
            # sweep is budgeted.
            "T12": 8,
            "PNL": 8,
            "PNL_MONTHLY": 8,
            "PNL_YTD": 8,
            "PNL_BENCHMARK": 8,
            "PORTFOLIO_PNL": 8,
            # STR / CBRE / MARKET_STUDY not yet re-benched with real
            # Anthropic — Agent U's educated defaults stand until we
            # get real measurements on them.
            "STR_TREND": 4,
            "STR_SEGMENTATION": 4,
            "STR": 4,
            "CBRE_HORIZONS": 6,
            "MARKET_STUDY": 6,
        }
    )

    # Cost-optimization pass S (2026-07): structural pre-filter.
    # Drops chunks that carry no P&L / STR / currency signal before we
    # spend Sonnet tokens on them. A typical STR Trend xlsx workbook
    # ships 26 sheets — 20+ are "Cover", "Notes", "SetUp", "Help",
    # "Glossary" tabs the extractor confidently returns empty_envelope
    # on at ~$0.10-0.30 apiece. Filter is doc-type-aware: aggressive
    # for tabular reports (T12 / PNL / STR / CBRE), light for
    # PROPERTY_INFO / ROOM_MIX, disabled for prose-heavy docs (OM /
    # MARKET_STUDY / SURVEYS). Safety-clamped so we never drop the last
    # remaining chunk (there is always ≥1 doc sent to the extractor).
    # Flip to false to bypass the filter while debugging a suspected
    # missed-chunk regression — the parser cache is lossless, so all
    # pages are still on disk; only the extractor's view changes.
    STRUCTURAL_PREFILTER_ENABLED: bool = Field(default=True)

    # Cost-optimization pass W (2026-07): deterministic template
    # extraction for standardized report formats. STR / CoStar Trend
    # reports ship the same tab structure on every report, so a
    # label-anchored parser reads the exact fields the LLM extractor
    # would emit (subject TTM + monthly series, comp-set roster/keys,
    # MPI/ARI/RGI) directly from the ``ParsedPage.tables`` cell grids —
    # $0 per document instead of ~$0.30, and no hallucination/drift on
    # a file family that has caused repeated misclassification bugs.
    # Detection is conservative: any doubt returns None and the doc
    # falls through to the unchanged LLM path, so flipping this to
    # false only changes cost, never coverage.
    TEMPLATE_EXTRACTION_ENABLED: bool = Field(default=True)

    # ── Tenancy ─────────────────────────────────────────────────────
    # UUID-shaped string for dev. Real tenants are provisioned in DB.
    DEFAULT_TENANT_ID: str = Field(
        default="00000000-0000-0000-0000-000000000001"
    )

    # ── Clerk (auth + RBAC) ─────────────────────────────────────────
    # Wave RBAC 2026-07 — the worker verifies Clerk session JWTs on
    # every request that carries ``Authorization: Bearer <jwt>``. The
    # JWT is signed with Clerk's rotating RS256 keys; we fetch the
    # JWKS from ``CLERK_JWKS_URL`` and cache it in-process for
    # ``CLERK_JWKS_CACHE_TTL_S``. When both env vars are unset the
    # verifier fails-closed (returns None) and the caller falls back
    # to the ``X-Tenant-Id`` header path, so dev/tests/curl keep
    # working without a Clerk config.
    #
    # ``CLERK_SECRET_KEY`` is optional here — PyJWT verifies with the
    # JWKS public key, not the secret — but we accept it so the
    # Railway env can carry the same var name as the Vercel side and
    # so a future ``users.me`` REST call can authenticate with it.
    CLERK_JWKS_URL: str = Field(
        default="https://api.clerk.com/v1/jwks"
    )
    CLERK_SECRET_KEY: SecretStr | None = Field(default=None)
    # Clerk publishes rotated JWKS every ~24h; a 5-minute in-process
    # cache is short enough to catch a rotation within the SLA and
    # long enough that a hot path (one call per request) doesn't hit
    # Clerk's public JWKS endpoint on every mutation. On a signature
    # miss we bust the cache and re-fetch once — the standard
    # "key-not-found → refresh" pattern from PyJWKClient.
    CLERK_JWKS_CACHE_TTL_S: int = Field(default=300, gt=0)
    # Expected ``iss`` claim on the Clerk JWT. When unset, the
    # verifier skips the issuer check (dev/test). Prod sets this to
    # e.g. ``https://<tenant>.clerk.accounts.dev`` so a token minted
    # on a different Clerk instance is rejected outright.
    CLERK_JWT_ISSUER: str | None = Field(default=None)
    # Expected ``aud`` claim, when present. Clerk session tokens
    # historically didn't carry an audience; leave unset so we skip
    # the check unless the operator explicitly opts in.
    CLERK_JWT_AUDIENCE: str | None = Field(default=None)

    # ── Object store (uploaded OMs, STRs, P&Ls) ─────────────────────
    OBJECT_STORE_BACKEND: Literal["local", "s3"] = Field(default="local")
    DOCUMENT_STORAGE_ROOT: str = Field(default="/tmp/fondok")
    S3_BUCKET: str | None = Field(default=None)
    S3_REGION: str | None = Field(default=None)
    S3_KMS_KEY_ID: str | None = Field(default=None)
    S3_PREFIX: str = Field(default="fondok")

    # ── Upload size cap (per file) ──────────────────────────────────
    # Default 50 MB leaves comfortable head-room above the largest
    # legit OM Sam has shipped (~38 MB) and the real 19.7 MB OM that
    # blocked the Due Diligence tab in June 2026. Env-overridable so
    # an enterprise tenant with bigger broker packets can bump it
    # without a code change. S3 storage means no local-disk floor;
    # the practical ceiling is LlamaParse + FastAPI in-memory body
    # buffering — keep well under 200 MB until chunked upload lands.
    MAX_UPLOAD_MB: int = Field(default=50, gt=0)

    # ── CORS ────────────────────────────────────────────────────────
    # Comma-separated origin list; default permissive for dev.
    CORS_ORIGINS: str = Field(default="*")
    ALLOWED_CORS_ORIGIN_REGEX: str | None = Field(
        default=r"https://fondok-[a-z0-9]+-aprem01s-projects\.vercel\.app"
    )

    # ── OpenTelemetry ───────────────────────────────────────────────
    # Off by default — set OTEL_EXPORTER_OTLP_ENDPOINT to enable.
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = Field(default=None)
    DEPLOYMENT_ENVIRONMENT: str = Field(default="development")

    # ── Analyst memo streaming ──────────────────────────────────────
    # When true, ``run_analyst`` drafts the memo section-by-section
    # and publishes each completed section to the in-process
    # ``MemoBroadcast`` so the UI can render the memo as it builds.
    MEMO_STREAMING_ENABLED: bool = Field(default=False)

    # ── Analyst batch API (cost-opt V, 2026-07) ─────────────────────
    # When True, the memo lane routes through Anthropic's Message
    # Batches API (``POST /v1/messages/batches``) which charges 50% of
    # the standard input+output rate in exchange for up to 24h of
    # turnaround. Ships DARK — flip on per-tenant once the poller has
    # baked in staging. The interactive sync path stays intact and is
    # the fallback when this flag is false or the submit fails. See
    # ``app.agents.analyst_batch`` for the runtime contract.
    ANALYST_BATCH_API_ENABLED: bool = Field(default=False)

    # Poller cadence — the background task that checks Anthropic for
    # ended batches. 5 minutes matches the SDK's recommended polling
    # interval and keeps API cost negligible (a HEAD-style status call).
    ANALYST_BATCH_POLL_SECONDS: float = Field(default=300.0, gt=0.0)

    # ── Pub/sub backend ─────────────────────────────────────────────
    # When set, ``MemoBroadcast`` uses Redis pub/sub instead of the
    # single-process in-memory queue. Required for multi-replica
    # deployments where the SSE subscriber may land on a different
    # worker pod than the publisher. Railway provisions REDIS_URL
    # automatically when the Redis service is added.
    REDIS_URL: str | None = Field(default=None)

    # ── Sentry (worker) ─────────────────────────────────────────────
    # Off by default — set SENTRY_DSN_WORKER to enable. Different DSN
    # from the web app so prod / dev pipelines can be tuned independently.
    SENTRY_DSN_WORKER: str | None = Field(default=None)
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.05, ge=0.0, le=1.0)
    SENTRY_PROFILES_SAMPLE_RATE: float = Field(default=0.0, ge=0.0, le=1.0)
    SENTRY_RELEASE: str | None = Field(default=None)

    # ── Slack alerting ──────────────────────────────────────────────
    # Off by default — set SLACK_ALERT_WEBHOOK_URL to enable. Only
    # severities >= SLACK_ALERT_MIN_SEVERITY ship to Slack.
    SLACK_ALERT_WEBHOOK_URL: SecretStr | None = Field(default=None)
    SLACK_ALERT_MIN_SEVERITY: Literal["info", "warning", "error", "critical"] = (
        Field(default="error")
    )
    SLACK_ALERT_CHANNEL: str | None = Field(default=None)

    # ── Email backend (W4.5 — pipeline digests) ─────────────────────
    # ``log_only`` (default) writes the rendered email to the worker
    # log and returns success — useful for dev / CI and the only
    # backend exercised by the test suite. ``sendgrid`` honors
    # ``SENDGRID_API_KEY`` and POSTs to the SendGrid v3 API.
    # ``ses`` is a TODO — wire AWS creds when the first customer
    # asks for it.
    EMAIL_BACKEND: Literal["log_only", "sendgrid", "ses"] = Field(default="log_only")
    SENDGRID_API_KEY: SecretStr | None = Field(default=None)
    EMAIL_FROM_ADDRESS: str = Field(default="digests@fondok.app")
    EMAIL_FROM_NAME: str = Field(default="Fondok Digests")

    # ── Digest scheduler (W4.5) ─────────────────────────────────────
    # In-process scheduler tick interval. Tests fake-tick the loop;
    # production should swap to a real beat scheduler (see
    # services.digest_scheduler module docstring).
    DIGEST_SCHEDULER_ENABLED: bool = Field(default=True)
    DIGEST_SCHEDULER_TICK_SECONDS: float = Field(default=60.0, gt=0.0)

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy expects ``postgresql+asyncpg://`` for the asyncpg driver."""
        url = self.DATABASE_URL
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @property
    def sync_database_url(self) -> str:
        """LangGraph's PostgresSaver wants a sync DSN."""
        url = self.DATABASE_URL
        if url.startswith("postgresql+asyncpg://"):
            return "postgresql://" + url[len("postgresql+asyncpg://") :]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()  # type: ignore[call-arg]
