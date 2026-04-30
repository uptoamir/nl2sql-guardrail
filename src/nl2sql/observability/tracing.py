"""OpenTelemetry tracing init — sends to Phoenix / OTLP collector.

Per `final_1.md` §35.5 + §C0-7 (port 4318 not 6006). v0 ships a no-op
fallback when OTEL_EXPORTER_OTLP_ENDPOINT is unset.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def init_tracing(default_service_name: str = "nl2sql") -> Any:
    """Configure OTel tracing if OTEL_EXPORTER_OTLP_ENDPOINT is set.

    Returns a no-op tracer otherwise.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        # Tracing disabled (e.g., CI / mock-only runs). Return a no-op tracer
        # so caller code can still call .start_as_current_span() without
        # checking.
        try:
            from opentelemetry import trace

            return trace.get_tracer(__name__)
        except ImportError:
            return _NoOpTracer()

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:  # pragma: no cover
        logger.warning(
            "OTel libs not installed; tracing disabled. Install via "
            "`uv sync --extra obs-local`. (%s)",
            e,
        )
        return _NoOpTracer()

    service_name = os.getenv("OTEL_SERVICE_NAME", default_service_name)
    extra_attrs = dict(
        kv.split("=", 1) for kv in os.getenv("OTEL_RESOURCE_ATTRIBUTES", "").split(",") if "=" in kv
    )
    resource = Resource.create({"service.name": service_name, **extra_attrs})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # Auto-instrument sqlite3 + httpx if available
    try:
        from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor

        SQLite3Instrumentor().instrument()
    except ImportError:
        pass
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass

    return trace.get_tracer(__name__)


class _NoOpTracer:
    """Minimal no-op tracer for when OTel isn't installed."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> Any:  # noqa: ARG002
        return _NoOpSpan()


class _NoOpSpan:
    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_span_context(self) -> Any:
        return None

    def is_recording(self) -> bool:
        return False
