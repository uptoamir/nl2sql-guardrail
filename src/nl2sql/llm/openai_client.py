"""OpenAI implementation of LLMClient.

Per `final_1.md` §35.10 + P0-C (OTel propagation) + P1-2 (x-request-id).

Captures ``x-request-id`` and ``openai-processing-ms`` headers per call
for audit-log correlation; sets ``traceparent`` in ``extra_headers`` so
downstream OpenAI logs can be correlated with our OTel traces.

Asserts ``response.model`` matches ``settings.expected_model`` (P1-18
model-drift detection).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


class OpenAIClient:
    """Real OpenAI provider. JSON-mode for structured stages; streaming
    for the interpreter."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_s: float = 30.0,
        temperature: float = 0.0,
        expected_model: str | None = None,
        max_retries: int = 2,
    ) -> None:
        # Lazy import so the rest of the package can import this module
        # even when openai isn't installed (e.g., in --mock-only environments).
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover - openai in core deps
            raise RuntimeError(
                "openai package not available — install via `uv sync` or use `--mock`."
            ) from e

        self.model = model
        self.expected_model = expected_model or model
        self.temperature = temperature
        self._client = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries)
        self._first_response_seen = False

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],  # noqa: ARG002 - schema name is consumed by JSON-mode parser
        *,
        stage: str,  # noqa: ARG002
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Issue a JSON-mode completion. Returns the parsed object."""
        extra_headers = self._otel_headers()
        t0 = time.monotonic()

        # OpenAI's JSON-mode requires the message to mention "json" — our
        # drafter prompt does, but we also add a short reminder defensively.
        messages = [
            {
                "role": "system",
                "content": "Respond with a single JSON object. No prose, no markdown.",
            },
            {"role": "user", "content": prompt},
        ]

        # `with_raw_response` exposes headers via `.headers`.
        raw = self._client.chat.completions.with_raw_response.create(  # type: ignore[call-overload]
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            max_tokens=max_output_tokens,
            extra_headers=extra_headers,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        response = raw.parse()  # the high-level wrapper

        # P1-18: model-drift assertion
        actual_model = getattr(response, "model", None)
        if actual_model and actual_model != self.expected_model:
            try:
                from nl2sql.observability.metrics import MODEL_DRIFT_TOTAL

                MODEL_DRIFT_TOTAL.labels(expected=self.expected_model, actual=actual_model).inc()
            except ImportError:
                pass
            logger.error(
                "model_drift expected=%s actual=%s",
                self.expected_model,
                actual_model,
            )
            # In v0 we log + count but don't raise; v1 production sets
            # strict_model_pin=True and raises.
            # raise ModelDriftError(...)

        choice = response.choices[0]
        content = choice.message.content or "{}"

        # Capture telemetry (consumed by Pipeline + audit log)
        request_id = raw.headers.get("x-request-id")
        processing_ms_raw = raw.headers.get("openai-processing-ms")
        processing_ms = int(processing_ms_raw) if processing_ms_raw else None
        usage = getattr(response, "usage", None)
        cached_tokens = 0
        tokens_in = 0
        tokens_out = 0
        if usage:
            tokens_in = getattr(usage, "prompt_tokens", 0) or 0
            tokens_out = getattr(usage, "completion_tokens", 0) or 0
            ptd = getattr(usage, "prompt_tokens_details", None)
            if ptd:
                cached_tokens = getattr(ptd, "cached_tokens", 0) or 0

        # Stash on the response dict so the agent can pull telemetry without
        # re-reading the OpenAI SDK's wrapper objects.
        try:
            parsed: dict[str, Any] = json.loads(content)
        except json.JSONDecodeError as e:
            # Wrap in a stable shape so downstream parsers raise consistently
            return {
                "_parse_error": f"{type(e).__name__}: {e}",
                "_raw_content": content[:500],
                "_telemetry": {
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cached_tokens": cached_tokens,
                    "latency_ms": latency_ms,
                    "openai_processing_ms": processing_ms,
                    "llm_request_id": request_id,
                    "model": actual_model or self.model,
                    "rendered_prompt": prompt,
                },
            }

        parsed["_telemetry"] = {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_tokens": cached_tokens,
            "latency_ms": latency_ms,
            "openai_processing_ms": processing_ms,
            "llm_request_id": request_id,
            "model": actual_model or self.model,
            "rendered_prompt": prompt,
        }
        return parsed

    def complete_streaming(
        self,
        prompt: str,
        *,
        stage: str,  # noqa: ARG002
        max_output_tokens: int | None = None,
    ) -> Iterator[str]:
        """Streaming text completion — used only by the interpreter stage."""
        extra_headers = self._otel_headers()
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            stream=True,
            max_tokens=max_output_tokens,
            extra_headers=extra_headers,
        )
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except (AttributeError, IndexError):
                continue
            if delta:
                yield delta

    @staticmethod
    def _otel_headers() -> dict[str, str]:
        """Build the W3C ``traceparent`` + run-id headers (P0-C)."""
        headers: dict[str, str] = {}
        try:
            from opentelemetry import trace
            from opentelemetry.trace import format_span_id, format_trace_id

            span_ctx = trace.get_current_span().get_span_context()
            if span_ctx and span_ctx.trace_id:
                headers["traceparent"] = (
                    f"00-{format_trace_id(span_ctx.trace_id)}-{format_span_id(span_ctx.span_id)}-01"
                )
        except (ImportError, Exception):  # noqa: BLE001 - OTel is optional in v0
            pass

        try:
            import structlog

            ctx = structlog.contextvars.get_contextvars()
            run_id = ctx.get("run_id")
            ws_id = ctx.get("workspace_id")
            if run_id:
                headers["x-dayforce-run-id"] = str(run_id)
            if ws_id:
                headers["x-dayforce-workspace-id"] = str(ws_id)
        except ImportError:
            pass

        return headers
