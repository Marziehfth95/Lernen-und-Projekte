"""Safe operational traces and correlation separate from immutable business audit."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

LOGGER = logging.getLogger("process_optimizer.operations")
CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TRACER_NAME = "process_optimizer"
# Span attributes are allow-listed metadata only: never prompts, model output,
# recommendation text, tokens, credentials or actor identifiers.
SPAN_ATTRIBUTE_KEYS = frozenset(
    {
        "analysis_run_id",
        "scenario",
        "execution_mode",
        "node",
        "outcome",
        "error_category",
        "tool_name",
        "tool_sequence",
        "returned_count",
        "truncated",
        "iterations_used",
        "tool_calls_used",
        "recommendation_count",
        "valid_count",
        "error_codes",
        "final_decision",
        "guardrail_checkpoint",
        "guardrail_decision",
        "guardrail_reason_codes",
        "guardrail_policy_id",
        "guardrail_policy_version",
        # Sanitized provider-failure metadata: fixed codes, types and opaque IDs only.
        "exception_type",
        "http_status",
        "provider_error_code",
        "provider_request_id",
        "provider_apim_request_id",
        "agent_session_id",
        "agent_version",
    }
)


def configure_observability() -> bool:
    """Enable Azure Monitor only through explicit production configuration."""
    if os.environ.get("ENABLE_AZURE_MONITOR", "false").lower() != "true":
        return False
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        raise RuntimeError("Application Insights connection string is required")
    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry
    except ImportError as error:
        raise RuntimeError("Install the project observability extra") from error
    use_microsoft_opentelemetry(enable_azure_monitor=True, logger_name=LOGGER.name)
    # The distro attaches its handler but leaves the level unset; the inherited root
    # level (WARNING) would otherwise drop every INFO operational event.
    LOGGER.setLevel(logging.INFO)
    return True


class OperationSpan:
    """Allow-listed attribute writer over an OpenTelemetry span, or a no-op without one."""

    def __init__(self, span: Any = None) -> None:
        self._span = span

    def set(self, attributes: Mapping[str, object]) -> None:
        if self._span is None:
            return
        for key, value in attributes.items():
            safe = _safe_span_value(value)
            if key in SPAN_ATTRIBUTE_KEYS and safe is not None:
                self._span.set_attribute(f"process_optimizer.{key}", safe)

    def fail(self, error_category: str) -> None:
        """Mark the span failed with a category only, never an exception message."""
        self.set({"outcome": "error", "error_category": error_category})
        if self._span is not None:
            from opentelemetry.trace import Status, StatusCode

            self._span.set_status(Status(StatusCode.ERROR))


@contextmanager
def operation_span(
    name: str, attributes: Mapping[str, object] | None = None
) -> Iterator[OperationSpan]:
    """Open a current span when OpenTelemetry is installed; otherwise do nothing."""
    try:
        tracer = _tracer()
    except ImportError:
        yield OperationSpan()
        return
    # Exception messages can echo model output, so they are never recorded.
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as span:
        handle = OperationSpan(span)
        handle.set(attributes or {})
        try:
            yield handle
        except Exception as error:
            handle.fail(type(error).__name__)
            raise


def _tracer() -> Any:
    from opentelemetry import trace

    return trace.get_tracer(TRACER_NAME)


def _safe_span_value(value: object) -> str | int | float | bool | tuple[str, ...] | None:
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def record_operational_event(
    event_name: str,
    *,
    result: str,
    correlation_id: str | None = None,
    attributes: dict[str, str | int | float | bool | None] | None = None,
) -> None:
    """Emit allow-listed metadata without prompts, payloads or actor identifiers."""
    payload: dict[str, Any] = {
        "event": event_name,
        "result": result,
        "correlation_id": correlation_id,
        "attributes": dict(attributes or {}),
    }
    LOGGER.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    try:
        from opentelemetry import trace

        trace.get_current_span().add_event(event_name, attributes=_otel_attributes(payload))
    except ImportError:
        return


def _otel_attributes(payload: dict[str, Any]) -> dict[str, str | int | float | bool]:
    attributes: dict[str, str | int | float | bool] = {
        "process_optimizer.result": str(payload["result"]),
    }
    correlation_id = payload.get("correlation_id")
    if correlation_id:
        attributes["process_optimizer.correlation_id"] = str(correlation_id)
    for key, value in payload.get("attributes", {}).items():
        if isinstance(value, str | int | float | bool):
            attributes[f"process_optimizer.{key}"] = value
    return attributes


class CorrelationAndTelemetryMiddleware(BaseHTTPMiddleware):
    """Propagate validated correlation IDs and record bounded request metadata."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("x-correlation-id", "")
        correlation_id = (
            supplied if CORRELATION_PATTERN.fullmatch(supplied) else f"corr_{uuid.uuid4().hex}"
        )
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            route = request.scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            record_operational_event(
                "api_request",
                result="success" if status_code < 500 else "error",
                correlation_id=correlation_id,
                attributes={
                    "method": request.method,
                    "route": route_template,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                },
            )
            if response is not None:
                response.headers["x-correlation-id"] = correlation_id
