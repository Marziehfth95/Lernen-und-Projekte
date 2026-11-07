"""OpenTelemetry-compatible operational telemetry boundary."""

from process_optimizer.observability.telemetry import (
    CorrelationAndTelemetryMiddleware,
    configure_observability,
    operation_span,
    record_operational_event,
)

__all__ = [
    "CorrelationAndTelemetryMiddleware",
    "configure_observability",
    "operation_span",
    "record_operational_event",
]
