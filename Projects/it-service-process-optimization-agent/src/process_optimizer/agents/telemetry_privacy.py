"""Observability setup for the Hosted Agent that cannot export raw exception text.

Third-party GenAI auto-instrumentation (the LangChain instrumentor that
``microsoft-opentelemetry`` enables, and OpenAI v2 when LangChain is absent) records
SDK exception messages, provider error bodies and stack traces on its spans. The
agent's own spans and audit events already carry the sanitized provider category,
HTTP status and request IDs, so every auto-instrumentor is disabled instead of
filtered.

Only public, documented hooks are used: ``AgentServerHost(configure_observability=...)``,
``azure.ai.agentserver.core.configure_observability(instrumentation_options=...)`` and
``BaseInstrumentor.is_instrumented_by_opentelemetry``. The host swallows exceptions
raised during observability setup, so the fail-closed check runs afterwards.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

INSTRUMENTOR_GROUP = "opentelemetry_instrumentor"


def installed_instrumentation_names() -> tuple[str, ...]:
    return tuple(sorted({entry.name for entry in entry_points(group=INSTRUMENTOR_GROUP)}))


def disabled_instrumentation_options() -> dict[str, dict[str, Any]]:
    """Disable every installed auto-instrumentor, whatever the distro supports."""
    return {name: {"enabled": False} for name in installed_instrumentation_names()}


def configure_private_observability(**kwargs: Any) -> None:
    """``configure_observability`` replacement passed to the agent-server host.

    Keeps console logging and the tracer/exporter setup, never enables sensitive-data
    recording, and turns off all auto-instrumentation.
    """
    from azure.ai.agentserver.core import configure_observability

    kwargs["enable_sensitive_data"] = False
    options = dict(kwargs.pop("instrumentation_options", None) or {})
    options.update(disabled_instrumentation_options())
    configure_observability(**kwargs, instrumentation_options=options)


def active_auto_instrumentations() -> tuple[str, ...]:
    """Names of installed auto-instrumentors that are currently instrumenting."""
    active = []
    for entry in entry_points(group=INSTRUMENTOR_GROUP):
        try:
            instrumentor = entry.load()()
        except Exception:  # noqa: BLE001 - an instrumentor that cannot load cannot be active
            continue
        if getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
            active.append(entry.name)
    return tuple(sorted(set(active)))


def require_no_auto_instrumentation() -> None:
    """Refuse to serve when any auto-instrumentor is active (fail closed)."""
    active = active_auto_instrumentations()
    if active:
        raise RuntimeError(
            "Hosted runtime refused to start: TELEMETRY_AUTO_INSTRUMENTATION_ACTIVE "
            + ",".join(active)
        )
