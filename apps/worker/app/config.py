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
    # Extractor + Normalizer use Sonnet — STR/P&L parsing needs reasoning.
    ANTHROPIC_EXTRACTOR_MODEL: str = Field(default="claude-sonnet-4-6")
    ANTHROPIC_NORMALIZER_MODEL: str = Field(default="claude-sonnet-4-6")
    # Analyst uses Opus — IC memo writing + variance reasoning.
    ANTHROPIC_ANALYST_MODEL: str = Field(default="claude-opus-4-7")
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

    # ── Tenancy ─────────────────────────────────────────────────────
    # UUID-shaped string for dev. Real tenants are provisioned in DB.
    DEFAULT_TENANT_ID: str = Field(
        default="00000000-0000-0000-0000-000000000001"
    )

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
