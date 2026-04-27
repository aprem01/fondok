"""OpenTelemetry wiring.

Exports traces over OTLP/HTTP. Off by default — set
``OTEL_EXPORTER_OTLP_ENDPOINT`` (and optionally ``OTEL_EXPORTER_OTLP_HEADERS``)
to enable.

Auto-instrumentation:
  * FastAPI request lifecycle (one span per HTTP request)
  * SQLAlchemy queries
  * httpx client calls (Anthropic SDK rides on httpx)
  * asyncpg low-level Postgres calls

Manual instrumentation:
  * ``@trace_agent("Extractor")`` decorator on each agent's ``run_*`` so
    we get span/duration per agent invocation.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from .config import get_settings

logger = logging.getLogger(__name__)

_INSTRUMENTED = False
_TRACER: Any = None

F = TypeVar("F", bound=Callable[..., Any])


def setup_telemetry(app: Any | None = None) -> bool:
    """Initialize OTel + auto-instrumentors. Idempotent.

    Returns True if instrumentation was activated, False when no
    exporter endpoint is configured (the worker still runs; spans
    just go nowhere).
    """
    global _INSTRUMENTED, _TRACER
    if _INSTRUMENTED:
        if app is not None:
            _try_instrument_fastapi(app)
        return True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.info("otel: disabled (OTEL_EXPORTER_OTLP_ENDPOINT not set)")
        return False

    settings = get_settings()
    service_name = os.environ.get("OTEL_SERVICE_NAME", "fondok-worker")

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("otel: import failed — disabled (%s)", exc)
        return False

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "fondok",
            "deployment.environment": os.environ.get(
                "DEPLOYMENT_ENVIRONMENT", settings.DEPLOYMENT_ENVIRONMENT
            ),
            "fondok.tenant": settings.DEFAULT_TENANT_ID,
        }
    )

    provider = TracerProvider(resource=resource)
    headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=_parse_headers(headers))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("fondok.worker")

    _try_instrument_sqlalchemy()
    _try_instrument_httpx()
    _try_instrument_asyncpg()
    if app is not None:
        _try_instrument_fastapi(app)

    _INSTRUMENTED = True
    logger.info(
        "otel: enabled service=%s endpoint=%s",
        service_name,
        endpoint.split("?", 1)[0],
    )
    return True


def _parse_headers(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("{"):
        import json

        try:
            decoded = json.loads(raw)
            return {str(k): str(v) for k, v in decoded.items()}
        except json.JSONDecodeError:
            logger.warning("otel: OTEL_EXPORTER_OTLP_HEADERS isn't JSON — ignoring")
            return None
    out: dict[str, str] = {}
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        key, _, value = piece.partition("=")
        out[key.strip()] = value.strip()
    return out or None


def _try_instrument_fastapi(app: Any) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("otel: FastAPI auto-instrumented")
    except Exception as exc:
        logger.warning("otel: FastAPI instrumentation failed (%s)", exc)


def _try_instrument_sqlalchemy() -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from .database import get_engine

        SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
        logger.info("otel: SQLAlchemy auto-instrumented")
    except Exception as exc:
        logger.warning("otel: SQLAlchemy instrumentation failed (%s)", exc)


def _try_instrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.info("otel: httpx auto-instrumented")
    except Exception as exc:
        logger.warning("otel: httpx instrumentation failed (%s)", exc)


def _try_instrument_asyncpg() -> None:
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

        AsyncPGInstrumentor().instrument()
        logger.info("otel: asyncpg auto-instrumented")
    except Exception as exc:
        logger.warning("otel: asyncpg instrumentation failed (%s)", exc)


def get_tracer() -> Any:
    """Lazy tracer accessor. Returns a no-op tracer when OTel isn't
    enabled so callers don't have to special-case the off path."""
    if _TRACER is not None:
        return _TRACER
    try:
        from opentelemetry import trace

        return trace.get_tracer("fondok.worker")
    except ImportError:

        class _NoopSpan:
            def set_attribute(self, *_a: Any, **_k: Any) -> None:
                pass

            def record_exception(self, *_a: Any, **_k: Any) -> None:
                pass

            def __enter__(self) -> "_NoopSpan":
                return self

            def __exit__(self, *_a: Any) -> None:
                pass

        class _NoopTracer:
            def start_as_current_span(self, *_a: Any, **_k: Any) -> _NoopSpan:
                return _NoopSpan()

        return _NoopTracer()


def trace_agent(agent_name: str) -> Callable[[F], F]:
    """Decorator: wrap an async ``run_*`` agent in a span tagged with
    ``agent.name``. Records exceptions on the span without swallowing.

    Usage::

        @trace_agent("Extractor")
        async def run_extractor(payload: ExtractorInput) -> ExtractorOutput: ...
    """

    def _decorator(fn: F) -> F:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"trace_agent requires an async function, got {fn!r}"
            )

        @wraps(fn)
        async def _wrapped(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(
                f"agent.{agent_name.lower()}.run"
            ) as span:
                span.set_attribute("agent.name", agent_name)
                payload = args[0] if args else kwargs.get("payload")
                deal_id = getattr(payload, "deal_id", None) if payload else None
                if deal_id:
                    span.set_attribute("fondok.deal_id", str(deal_id))
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    raise

        return _wrapped  # type: ignore[return-value]

    return _decorator


__all__ = [
    "get_tracer",
    "setup_telemetry",
    "trace_agent",
]
