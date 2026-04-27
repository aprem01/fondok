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
