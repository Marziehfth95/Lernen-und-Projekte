"""OpenTelemetry spans for the investigation graph, verified with an in-memory exporter.

No model is called: the real-model path uses a ChatOpenAI client with a local transport.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from tests.test_agent_workflow import (  # noqa: F401  (shared fixture and fakes)
    FabricatedNumberProvider,
    InvalidToolProvider,
    TimeoutProvider,
    _complete_reply,
    _foundry_provider,
    _offline_chat_model,
    analysis_dir,
)

from process_optimizer.agents.provider import DeterministicMockProvider
from process_optimizer.agents.schemas import (
    AnalystOutput,
    InvestigationRequestV1,
    InvestigatorOutput,
    OptimizationOutput,
    RecommendationStatus,
    ToolRequest,
)
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.observability import configure_observability, operation_span, telemetry
from process_optimizer.observability.telemetry import LOGGER, SPAN_ATTRIBUTE_KEYS, TRACER_NAME

GOAL_MARKER = "UNTRUSTED-GOAL-TEXT-MUST-NOT-BE-EXPORTED"


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """A test-local SDK provider; the global provider is left untouched."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")  # conftest disables the SDK by default
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer(TRACER_NAME))
    return exporter


def _run(directory: Path, provider: Any, scenario: str = "group_returns") -> Any:
    runner = InvestigationRunner(directory, provider)
    return runner.run(
        InvestigationRequestV1(
            analysis_run_id=runner.analysis_run_id,
            investigation_goal=GOAL_MARKER,
            execution_mode=provider.mode,
            scenario=scenario,
        )
    )


def _by_name(spans: tuple[ReadableSpan, ...], name: str) -> list[ReadableSpan]:
    return [span for span in spans if span.name == name]


def _attr(span: ReadableSpan, key: str) -> Any:
    return (span.attributes or {}).get(f"process_optimizer.{key}")


def _parent_name(spans: tuple[ReadableSpan, ...], span: ReadableSpan) -> str | None:
    if span.parent is None:
        return None
    return next(item.name for item in spans if item.context.span_id == span.parent.span_id)


def test_successful_run_has_root_node_tool_and_validation_spans(
    analysis_dir: Path,  # noqa: F811
    exporter: InMemorySpanExporter,
) -> None:
    result = _run(analysis_dir, DeterministicMockProvider())
    spans = exporter.get_finished_spans()

    (root,) = _by_name(spans, "process_optimizer.investigation")
    assert root.parent is None
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}
    assert _attr(root, "analysis_run_id") == result.analysis_run_id
    assert _attr(root, "final_decision") == "VALIDATED_RECOMMENDATIONS"
    assert _attr(root, "outcome") == "success"
    assert _attr(root, "tool_calls_used") == 3

    nodes = [span for span in spans if span.name.startswith("process_optimizer.node.")]
    assert {_attr(span, "node") for span in nodes} == {
        "analyst", "investigator", "tool", "optimizer", "validator", "finalizer",
    }  # fmt: skip
    assert all(_parent_name(spans, span) == "process_optimizer.investigation" for span in nodes)
    assert all(_attr(span, "outcome") == "success" for span in nodes)

    tools = _by_name(spans, "process_optimizer.tool")
    assert [_attr(span, "tool_name") for span in tools] == [
        "compare_process_cohorts",
        "get_evidence_details",
        "simulate_group_transition_reduction",
    ]
    assert all(_parent_name(spans, span) == "process_optimizer.node.tool" for span in tools)
    assert all(_attr(span, "outcome") == "success" for span in tools)

    (validation,) = _by_name(spans, "process_optimizer.validation")
    assert _parent_name(spans, validation) == "process_optimizer.node.validator"
    assert _attr(validation, "valid_count") == 1
    assert _attr(validation, "error_codes") == ()
    assert _attr(validation, "outcome") == "success"


def test_model_timeout_marks_node_and_root_failed(
    analysis_dir: Path,  # noqa: F811
    exporter: InMemorySpanExporter,
) -> None:
    _run(analysis_dir, TimeoutProvider())
    spans = exporter.get_finished_spans()

    (analyst,) = _by_name(spans, "process_optimizer.node.analyst")
    (root,) = _by_name(spans, "process_optimizer.investigation")
    for span in (analyst, root):
        assert span.status.status_code == StatusCode.ERROR
        assert _attr(span, "error_category") == "PROVIDER_TIMEOUT"
    assert _attr(root, "final_decision") == "SAFE_TERMINATION"


def test_invalid_and_failing_tools_are_recorded_by_category(
    analysis_dir: Path,  # noqa: F811
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run(analysis_dir, InvalidToolProvider())
    (invalid,) = _by_name(exporter.get_finished_spans(), "process_optimizer.tool")
    assert _attr(invalid, "error_category") == "ARGUMENT_VALIDATION_FAILED"
    assert invalid.status.status_code == StatusCode.ERROR

    exporter.clear()

    def unavailable(self: Any, arguments: Any) -> Any:
        raise OSError("aggregate artifact unavailable")

    from process_optimizer.tools.analytics import AggregateAnalyticsTools

    monkeypatch.setattr(AggregateAnalyticsTools, "compare_process_cohorts", unavailable)
    _run(analysis_dir, DeterministicMockProvider())
    failed = _by_name(exporter.get_finished_spans(), "process_optimizer.tool")[0]
    assert _attr(failed, "tool_name") == "compare_process_cohorts"
    assert _attr(failed, "error_category") == "AGGREGATE_TOOL_FAILED"


def test_validator_rejection_is_recorded_with_error_codes(
    analysis_dir: Path,  # noqa: F811
    exporter: InMemorySpanExporter,
) -> None:
    _run(analysis_dir, FabricatedNumberProvider())
    spans = exporter.get_finished_spans()

    (validation,) = _by_name(spans, "process_optimizer.validation")
    assert _attr(validation, "outcome") == "rejected"
    assert "FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM" in _attr(validation, "error_codes")
    (root,) = _by_name(spans, "process_optimizer.investigation")
    assert _attr(root, "outcome") == "insufficient_evidence"
    assert root.status.status_code != StatusCode.ERROR


def _offline_real_model_run(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Real-model graph path over a local transport, replaying mock decisions."""
    mock = _run(analysis_dir, DeterministicMockProvider(), scenario="completed_lifecycle")
    recommendation = mock.recommendations[0].model_copy(
        update={"status": RecommendationStatus.DRAFT}
    )
    record = mock.tool_call_records[0]
    tool = ToolRequest(name=record.tool_name, arguments=record.validated_arguments)
    replies = [
        _complete_reply(AnalystOutput(selected_findings=mock.selected_findings)),
        _complete_reply(
            InvestigatorOutput(
                hypotheses=mock.hypotheses, requested_tools=(tool,), needs_more_analysis=True
            )
        ),
        _complete_reply(
            InvestigatorOutput(
                hypotheses=mock.hypotheses, requested_tools=(), needs_more_analysis=False
            )
        ),
        _complete_reply(OptimizationOutput(recommendations=(recommendation,))),
    ]
    bodies: list[dict[str, Any]] = []
    provider = _foundry_provider(monkeypatch, _offline_chat_model(replies, bodies))
    return _run(analysis_dir, provider, scenario="completed_lifecycle"), bodies


def test_model_call_metadata_is_attached_to_agent_node_spans(
    analysis_dir: Path,  # noqa: F811
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, bodies = _offline_real_model_run(analysis_dir, monkeypatch)
    spans = exporter.get_finished_spans()

    # The replayed mock run comes first; [-1] selects the real-model graph's node span.
    assert result.final_decision == "VALIDATED_RECOMMENDATIONS"
    assert len(bodies) == 4
    for node, role in (
        ("analyst", "Process Analyst"),
        ("investigator", "Root Cause Investigator"),
        ("optimizer", "Optimization Agent"),
    ):
        span = _by_name(spans, f"process_optimizer.node.{node}")[-1]
        assert _parent_name(spans, span) == "process_optimizer.investigation"
        (event,) = [item for item in span.events if item.name == "agent_model_invocation"]
        attributes = dict(event.attributes or {})
        assert attributes["process_optimizer.agent_role"] == role
        assert attributes["process_optimizer.input_tokens"] == 100
        assert attributes["process_optimizer.output_tokens"] == 20
        assert attributes["process_optimizer.response_model"] == "gpt-4.1-mini-2025-04-14"
        assert attributes["process_optimizer.result"] == "success"


def test_spans_never_carry_prompts_outputs_or_credentials(
    analysis_dir: Path,  # noqa: F811
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = _run(analysis_dir, DeterministicMockProvider())
    real, bodies = _offline_real_model_run(analysis_dir, monkeypatch)
    spans = exporter.get_finished_spans()
    exported = json.dumps(
        [
            {
                "attributes": dict(span.attributes or {}),
                "events": [dict(event.attributes or {}) for event in span.events],
                "status": span.status.description,
            }
            for span in spans
        ],
        default=str,
    )

    forbidden = [
        GOAL_MARKER,
        "offline-placeholder",
        "Bearer",
        "trusted_aggregate_context",
        *(item.statement for item in real.hypotheses),
        *(item.rationale for item in mock.selected_findings),
    ]
    for result in (mock, real):
        for recommendation in result.recommendations:
            forbidden += [
                recommendation.problem_statement,
                recommendation.suggested_process_improvement,
                *(fact.statement for fact in recommendation.observed_numerical_facts),
            ]
    assert bodies and all(text and text not in exported for text in forbidden)
    span_keys = {key for span in spans for key in (span.attributes or {})}
    assert span_keys <= {f"process_optimizer.{key}" for key in SPAN_ATTRIBUTE_KEYS}


def test_operation_span_drops_unlisted_attributes_and_exception_messages(
    exporter: InMemorySpanExporter,
) -> None:
    with (
        pytest.raises(ValueError),
        operation_span("probe", {"node": "analyst", "prompt": "secret prompt"}),
    ):
        raise ValueError("model said: secret output")
    (span,) = exporter.get_finished_spans()

    assert dict(span.attributes or {}) == {
        "process_optimizer.node": "analyst",
        "process_optimizer.outcome": "error",
        "process_optimizer.error_category": "ValueError",
    }
    assert span.status.status_code == StatusCode.ERROR
    assert not span.status.description
    assert not span.events


def test_enabled_observability_emits_info_operational_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    fake = SimpleNamespace(use_microsoft_opentelemetry=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "microsoft.opentelemetry", fake)
    monkeypatch.setenv("ENABLE_AZURE_MONITOR", "true")
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=offline")
    root = logging.getLogger()
    original_levels = (LOGGER.level, root.level)
    try:
        LOGGER.setLevel(logging.NOTSET)
        root.setLevel(logging.WARNING)  # Python's default; other tests' libraries may change it
        assert not LOGGER.isEnabledFor(logging.INFO)  # the inherited root level drops INFO

        assert configure_observability() is True

        assert calls == [{"enable_azure_monitor": True, "logger_name": LOGGER.name}]
        assert LOGGER.isEnabledFor(logging.INFO)
    finally:
        LOGGER.setLevel(original_levels[0])
        root.setLevel(original_levels[1])


def test_hosted_agent_disables_genai_content_capture_by_default(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = pytest.importorskip("azure.ai.agentserver.core")
    from process_optimizer.agents.hosting import GENAI_CONTENT_CAPTURE_ENV, create_invocations_host
    from process_optimizer.agents.telemetry_privacy import installed_instrumentation_names

    captured: list[bool] = []
    options: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> None:
        captured.append(kwargs["enable_sensitive_data"])
        options.append(kwargs["instrumentation_options"])

    # The host's observability callable delegates to the public agent-server setup.
    monkeypatch.setattr(core, "configure_observability", record)
    runner = InvestigationRunner(analysis_dir, DeterministicMockProvider())

    monkeypatch.delenv(GENAI_CONTENT_CAPTURE_ENV, raising=False)
    create_invocations_host(runner).app  # noqa: B018  (the host builds the server lazily)
    assert os.environ[GENAI_CONTENT_CAPTURE_ENV] == "false"

    # The installed library's own default is to capture content. An explicit opt-in is
    # refused before the server is built, so it can never reach the library.
    monkeypatch.setenv(GENAI_CONTENT_CAPTURE_ENV, "true")
    with pytest.raises(RuntimeError, match=GENAI_CONTENT_CAPTURE_ENV):
        create_invocations_host(runner)
    assert captured == [False]
    assert options[0] == {name: {"enabled": False} for name in installed_instrumentation_names()}
    assert {"langchain", "openai"} <= set(options[0])


def test_hosted_agent_configuration_disables_genai_content_capture() -> None:
    config = Path("azure.yaml").read_text(encoding="utf-8")

    assert 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: "false"' in config


def test_investigation_runs_unchanged_without_opentelemetry(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traced = _run(analysis_dir, DeterministicMockProvider()).model_dump_json()
    # A None entry makes "from opentelemetry import trace" raise ImportError.
    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    untraced = _run(analysis_dir, DeterministicMockProvider()).model_dump_json()

    assert untraced == traced
