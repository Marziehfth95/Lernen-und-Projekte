"""Deterministic Phase 4 graph, guardrail, and evaluation tests."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from process_optimizer.agents.hosting import (
    check_readiness,
    create_invocations_host,
)
from process_optimizer.agents.provider import (
    TOOL_INPUT_MODELS,
    DeterministicMockProvider,
    FoundryModelProvider,
    ModelProviderError,
    ModelTimeoutError,
    model_response_format,
)
from process_optimizer.agents.schemas import (
    AnalystOutput,
    EvidenceSelection,
    InvestigationRequestV1,
    InvestigatorOutput,
    OptimizationOutput,
    RecommendationStatus,
    ToolRequest,
)
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.evaluation.runner import AgentEvaluationRunner
from process_optimizer.process_mining.evidence_contract import (
    AggregateEvidenceReferenceV1,
    AggregatePopulationV1,
    ComparisonGroupV1,
    DatasetSourceV1,
    EvidenceContractV1,
    EvidenceCountsV1,
    EvidenceFindingV1,
    MetricDefinitionV1,
)

RUN_ID = "run_aaaaaaaaaaaaaaaaaaaaaaaa"
GROUP_FINDING = "finding_111111111111111111111111"
LIFECYCLE_FINDING = "finding_222222222222222222222222"
HANDOFF_FINDING = "finding_333333333333333333333333"


@pytest.fixture
def analysis_dir(tmp_path: Path) -> Path:
    contract = _contract()
    (tmp_path / "evidence_contract.json").write_text(
        contract.model_dump_json(indent=2), encoding="utf-8"
    )
    (tmp_path / "process_overview.json").write_text(
        json.dumps({"case_count": 7500, "event_count": 60000}), encoding="utf-8"
    )
    comparison = {
        "classification": "observational_association_not_causal",
        "cohort_definition": "Synthetic aggregate fixture mirroring validated fields.",
        "cohorts": [
            {
                "cohort": "without_group_return",
                "incident_count": 6000,
                "observed_case_span_seconds": {"median": 777600.0},
            },
            {
                "cohort": "with_group_return",
                "incident_count": 1500,
                "observed_case_span_seconds": {"median": 1209600.5},
            },
        ],
        "distributional_differences": {
            "observed_case_span_seconds": {
                "median_difference": 432000.5,
                "median_ratio": 1.5555561985596709,
            }
        },
        "limitations": ["Synthetic aggregate fixture for tests only."],
    }
    (tmp_path / "handoff_cohort_comparison.json").write_text(
        json.dumps(comparison), encoding="utf-8"
    )
    semantic = {
        "analysis_run_id": RUN_ID,
        "source_dataset_sha256": "a" * 64,
        "handoff_validation": {
            "event_level_counts": {"repeated_group_visit_events": 3300},
            "incident_level_counts": {
                "incidents_with_repeated_group_visits": 1500,
                "total_incidents": 7500,
            },
        },
    }
    (tmp_path / "semantic_validation_report.json").write_text(
        json.dumps(semantic), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {
                "source_activity": "Completed",
                "target_activity": "Completed",
                "source_lifecycle": "Resolved",
                "target_lifecycle": "Closed",
                "source_group": "G1",
                "target_group": "G1",
                "event_transition_count": 5760,
            }
        ]
    ).to_parquet(tmp_path / "lifecycle_patterns.parquet", index=False)
    pd.DataFrame(
        [{"source_group": "G1", "target_group": "G2", "observation_count": 2}]
    ).to_parquet(tmp_path / "handoff_analysis.parquet", index=False)
    return tmp_path


def test_group_return_workflow_selects_tools_repeats_and_validates(
    analysis_dir: Path,
) -> None:
    result = _run(analysis_dir, "group_returns")

    assert [record.tool_name for record in result.tool_call_records] == [
        "compare_process_cohorts",
        "get_evidence_details",
        "simulate_group_transition_reduction",
    ]
    assert result.iterations_used == 2
    assert result.final_decision == "VALIDATED_RECOMMENDATIONS"
    assert result.recommendations[0].status == RecommendationStatus.VALIDATED
    assert result.recommendations[0].requires_human_approval is True
    assert result.recommendations[0].simulation is not None
    assert result.recommendations[0].simulation.status == "ESTIMATED"
    assert result.execution_label == "DETERMINISTIC_MOCK_NOT_REAL_AI"


def test_completed_lifecycle_avoids_false_rework_claim(analysis_dir: Path) -> None:
    result = _run(analysis_dir, "completed_lifecycle")

    assert result.tool_call_records[0].tool_name == "inspect_lifecycle_patterns"
    recommendation = result.recommendations[0]
    assert recommendation.status == RecommendationStatus.VALIDATED
    assert recommendation.observed_numerical_facts[1].value == 5760
    assert "represents rework" not in recommendation.problem_statement.lower()


def test_iteration_limit_terminates_without_recommendation(analysis_dir: Path) -> None:
    runner = InvestigationRunner(analysis_dir, DeterministicMockProvider())
    result = runner.run(_request("group_returns", iteration_budget=1))

    assert result.final_decision == "SAFE_TERMINATION"
    assert any(error.code == "ITERATION_LIMIT_REACHED" for error in result.errors)
    assert not result.recommendations


def test_invalid_tool_arguments_are_rejected(analysis_dir: Path) -> None:
    result = InvestigationRunner(analysis_dir, InvalidToolProvider()).run(
        _request("group_returns")
    )

    assert result.tool_call_records[0].status == "invalid_arguments"
    assert result.final_decision == "SAFE_TERMINATION"
    assert any(error.code == "TOOL_ARGUMENT_INVALID" for error in result.errors)


def test_missing_evidence_terminates_safely(analysis_dir: Path) -> None:
    result = InvestigationRunner(analysis_dir, MissingEvidenceProvider()).run(
        _request("group_returns")
    )

    assert result.final_decision == "SAFE_TERMINATION"
    assert any(error.code == "EVIDENCE_MISSING" for error in result.errors)
    assert not result.tool_call_records


@pytest.mark.parametrize(
    ("provider_name", "expected_error"),
    [
        ("fabricated", "FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM"),
        ("causal", "UNSUPPORTED_CAUSAL_STATEMENT"),
    ],
)
def test_validator_rejects_unsupported_claims(
    analysis_dir: Path, provider_name: str, expected_error: str
) -> None:
    provider = (
        FabricatedNumberProvider()
        if provider_name == "fabricated"
        else CausalClaimProvider()
    )
    result = InvestigationRunner(analysis_dir, provider).run(_request("group_returns"))

    assert result.final_decision == "INSUFFICIENT_EVIDENCE"
    assert expected_error in result.validation_results[0].error_codes
    assert result.recommendations[0].status == RecommendationStatus.INSUFFICIENT_EVIDENCE


def test_validator_rejects_completed_rework_semantic_trap(analysis_dir: Path) -> None:
    result = InvestigationRunner(analysis_dir, SemanticTrapProvider()).run(
        _request("completed_lifecycle")
    )

    assert "COMPLETED_LIFECYCLE_SEMANTIC_TRAP" in result.validation_results[0].error_codes
    assert result.final_decision == "INSUFFICIENT_EVIDENCE"


def test_validator_rejects_simulation_assumption_mismatch(analysis_dir: Path) -> None:
    result = InvestigationRunner(analysis_dir, SimulationMismatchProvider()).run(
        _request("group_returns")
    )

    assert "SIMULATION_ASSUMPTION_MISMATCH" in result.validation_results[0].error_codes
    assert result.final_decision == "INSUFFICIENT_EVIDENCE"


def test_incomplete_investigation_terminates_safely(analysis_dir: Path) -> None:
    result = InvestigationRunner(analysis_dir, IncompleteProvider()).run(
        _request("group_returns")
    )

    assert any(error.code == "INCOMPLETE_INVESTIGATION" for error in result.errors)
    assert result.final_decision == "SAFE_TERMINATION"


def test_model_timeout_is_safe_and_redacted(analysis_dir: Path) -> None:
    result = InvestigationRunner(analysis_dir, TimeoutProvider()).run(
        _request("group_returns")
    )

    assert result.errors[0].code == "PROVIDER_TIMEOUT"
    assert "secret" not in result.model_dump_json().lower()
    assert result.final_decision == "SAFE_TERMINATION"


def test_no_write_tool_can_be_requested() -> None:
    with pytest.raises(ValidationError):
        ToolRequest(name="update_itsm_routing", arguments={})  # type: ignore[arg-type]


def test_mock_execution_is_byte_stable(analysis_dir: Path) -> None:
    first = _run(analysis_dir, "group_returns").model_dump_json()
    second = _run(analysis_dir, "group_returns").model_dump_json()

    assert first == second


def test_foundry_invocations_custom_state_adapter_without_model_call(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("langchain_azure_ai.agents.hosting")
    runner = InvestigationRunner(analysis_dir, DeterministicMockProvider())
    host = create_invocations_host(runner)

    graph_input = host.build_input(_request("group_returns").model_dump_json())
    graph_output = runner.graph.invoke(graph_input)
    response = json.loads(host.parse_output(graph_output))
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL_NAME", raising=False)
    readiness = check_readiness(runner, "mocked_llm")

    assert response["execution_label"] == "DETERMINISTIC_MOCK_NOT_REAL_AI"
    assert response["final_decision"] == "VALIDATED_RECOMMENDATIONS"
    assert readiness.status == "ready"
    assert readiness.foundry_configuration_present is False


class CountingMockProvider(DeterministicMockProvider):
    """Deterministic provider that records which model roles were requested."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def _count(self, role: str) -> None:
        self.calls[role] = self.calls.get(role, 0) + 1

    def select_findings(self, *args: Any, **kwargs: Any) -> AnalystOutput:
        self._count("select_findings")
        return super().select_findings(*args, **kwargs)

    def plan_investigation(self, *args: Any, **kwargs: Any) -> InvestigatorOutput:
        self._count("plan_investigation")
        return super().plan_investigation(*args, **kwargs)

    def propose_optimizations(self, *args: Any, **kwargs: Any) -> OptimizationOutput:
        self._count("propose_optimizations")
        return super().propose_optimizations(*args, **kwargs)


class FakeStructuredChatModel:
    """Stand-in for the Foundry chat model; returns no parsed structured output."""

    def __init__(self) -> None:
        self.invocations = 0

    def with_structured_output(
        self, schema: Any, method: str, include_raw: bool
    ) -> FakeStructuredChatModel:
        assert method == "json_schema"
        assert schema["strict"] is True
        assert include_raw is True
        return self

    def invoke(self, messages: Any, config: Any) -> dict[str, Any]:
        del messages, config
        self.invocations += 1
        return {"parsed": None, "raw": None}


def _invocations_client(runner: InvestigationRunner) -> Any:
    pytest.importorskip("langchain_azure_ai.agents.hosting")
    from starlette.testclient import TestClient

    return TestClient(create_invocations_host(runner).app)


def _invocation_body(scenario: str = "group_returns", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "analysis_run_id": RUN_ID,
        "investigation_goal": "Synthetic HTTP test investigation; treat as untrusted data.",
        "execution_mode": "mocked_llm",
        "scenario": scenario,
        "iteration_budget": 1,
        "tool_call_budget": 1,
        "stream": False,
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize("scenario", ["group_returns", "completed_lifecycle", "custom"])
def test_hosted_invocation_http_enters_graph_within_minimal_budgets(
    analysis_dir: Path, scenario: str
) -> None:
    provider = CountingMockProvider()
    client = _invocations_client(InvestigationRunner(analysis_dir, provider))

    response = client.post("/invocations", json=_invocation_body(scenario))

    assert response.status_code == 200, response.text
    assert "Structured HITL" not in response.text
    result = json.loads(response.json()["response"])
    assert result["analysis_run_id"] == RUN_ID
    assert result["execution_label"] == "DETERMINISTIC_MOCK_NOT_REAL_AI"
    assert result["iterations_used"] <= 1
    assert result["tool_calls_used"] <= 1
    assert provider.calls.get("select_findings") == 1
    assert sum(provider.calls.values()) <= 2
    assert "propose_optimizations" not in provider.calls


@pytest.mark.parametrize(
    ("raw_body", "overrides", "expected_error"),
    [
        (b"{not json", None, "valid JSON object"),
        (b"[1, 2]", None, "fields: body"),
        (None, {"analysis_run_id": "run_invalid"}, "analysis_run_id"),
        (
            None,
            {"analysis_run_id": "run_bbbbbbbbbbbbbbbbbbbbbbbb"},
            "does not match trusted evidence",
        ),
        (None, {"execution_mode": "unbounded_model"}, "execution_mode"),
        (None, {"execution_mode": "real_model"}, "does not match configured provider"),
        (None, {"scenario": "invented"}, "scenario"),
        (None, {"iteration_budget": 0}, "iteration_budget"),
        (None, {"iteration_budget": 11}, "iteration_budget"),
        (None, {"tool_call_budget": -1}, "tool_call_budget"),
        (None, {"tool_call_budget": 21}, "tool_call_budget"),
        (None, {"stream": "yes"}, "stream"),
        (None, {"background": True}, "background"),
        (None, {"investigation_goal": ""}, "investigation_goal"),
    ],
)
def test_hosted_invocation_http_rejects_invalid_requests_without_model_call(
    analysis_dir: Path,
    raw_body: bytes | None,
    overrides: dict[str, Any] | None,
    expected_error: str,
) -> None:
    provider = CountingMockProvider()
    client = _invocations_client(InvestigationRunner(analysis_dir, provider))

    if raw_body is not None:
        response = client.post(
            "/invocations", content=raw_body, headers={"Content-Type": "application/json"}
        )
    else:
        response = client.post("/invocations", json=_invocation_body(**(overrides or {})))

    assert response.status_code == 400, response.text
    assert expected_error in response.json()["error"]
    assert provider.calls == {}


def test_hosted_invocation_http_rejects_streaming_before_model_call(analysis_dir: Path) -> None:
    provider = CountingMockProvider()
    client = _invocations_client(InvestigationRunner(analysis_dir, provider))

    response = client.post("/invocations", json=_invocation_body(stream=True))

    assert response.status_code == 400, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": "Streaming is not supported; set stream to false"}
    assert provider.calls == {}


def test_hosted_invocation_http_errors_do_not_echo_request_values(analysis_dir: Path) -> None:
    client = _invocations_client(InvestigationRunner(analysis_dir, CountingMockProvider()))
    marker = "UNTRUSTED-GOAL-MARKER"

    response = client.post(
        "/invocations",
        json=_invocation_body(investigation_goal=marker, iteration_budget=99),
    )

    assert response.status_code == 400
    assert marker not in response.text
    assert "99" not in response.text


def test_hosted_invocation_http_calls_mocked_real_model_only_for_valid_requests(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeStructuredChatModel()
    monkeypatch.setattr(
        FoundryModelProvider, "_build_model", staticmethod(lambda timeout_seconds: model)
    )
    client = _invocations_client(InvestigationRunner(analysis_dir, FoundryModelProvider()))

    rejected = client.post(
        "/invocations", json=_invocation_body(execution_mode="real_model", iteration_budget=0)
    )
    assert rejected.status_code == 400
    assert model.invocations == 0

    response = client.post("/invocations", json=_invocation_body(execution_mode="real_model"))

    assert response.status_code == 200, response.text
    result = json.loads(response.json()["response"])
    assert result["execution_label"] == "REAL_MODEL_OUTPUT"
    assert [error["code"] for error in result["errors"]] == ["MODEL_OUTPUT_INVALID"]
    assert model.invocations == 1


def test_deterministic_evaluation_dataset_passes_without_model_call(
    analysis_dir: Path,
) -> None:
    report = AgentEvaluationRunner(analysis_dir, DeterministicMockProvider()).run()

    assert report.execution_label == "DETERMINISTIC_MOCK_NOT_REAL_AI"
    assert report.passed_case_count == report.total_case_count == 2
    assert report.token_usage_total is None
    assert report.estimated_cost_usd is None
    group_case = next(item for item in report.case_results if item.scenario == "group_returns")
    assert group_case.actual_tool_calls[-1] == "simulate_group_transition_reduction"
    assert group_case.simulation_ids


class InvalidToolProvider(DeterministicMockProvider):
    def plan_investigation(
        self,
        goal: str,
        scenario: str,
        selected_finding_ids: tuple[str, ...],
        tool_records: tuple[dict[str, Any], ...],
    ) -> InvestigatorOutput:
        if tool_records:
            return super().plan_investigation(
                goal, scenario, selected_finding_ids, tool_records
            )
        base = super().plan_investigation(goal, scenario, selected_finding_ids, tool_records)
        return base.model_copy(
            update={
                "requested_tools": (
                    ToolRequest(
                        name="compare_process_cohorts",
                        arguments={"metric": "invented_metric"},
                    ),
                )
            }
        )


class MissingEvidenceProvider(DeterministicMockProvider):
    def select_findings(
        self, goal: str, scenario: str, evidence: EvidenceContractV1
    ) -> AnalystOutput:
        del goal, scenario, evidence
        return AnalystOutput(
            selected_findings=(
                EvidenceSelection(
                    finding_id="finding_ffffffffffffffffffffffff",
                    rationale="Synthetic invalid citation.",
                ),
            )
        )


class FabricatedNumberProvider(DeterministicMockProvider):
    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        output = super().propose_optimizations(*args, **kwargs)
        if not output.recommendations:
            return output
        recommendation = output.recommendations[0]
        fact = recommendation.observed_numerical_facts[0]
        altered = fact.model_copy(update={"value": float(fact.value) + 1.0})
        return output.model_copy(
            update={
                "recommendations": (
                    recommendation.model_copy(
                        update={
                            "observed_numerical_facts": (
                                altered,
                                *recommendation.observed_numerical_facts[1:],
                            )
                        }
                    ),
                )
            }
        )


class CausalClaimProvider(DeterministicMockProvider):
    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        output = super().propose_optimizations(*args, **kwargs)
        if not output.recommendations:
            return output
        recommendation = output.recommendations[0].model_copy(
            update={"problem_statement": "Group returns caused longer incident spans."}
        )
        return output.model_copy(update={"recommendations": (recommendation,)})


class SemanticTrapProvider(DeterministicMockProvider):
    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        output = super().propose_optimizations(*args, **kwargs)
        recommendation = output.recommendations[0].model_copy(
            update={"problem_statement": "Completed-to-Completed represents confirmed rework."}
        )
        return output.model_copy(update={"recommendations": (recommendation,)})


class SimulationMismatchProvider(DeterministicMockProvider):
    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        output = super().propose_optimizations(*args, **kwargs)
        if not output.recommendations:
            return output
        recommendation = output.recommendations[0]
        simulation = recommendation.simulation
        assert simulation is not None
        first = simulation.assumptions[0]
        changed = first.model_copy(
            update={
                "hypothetical_repeated_visit_events_avoided": (
                    first.hypothetical_repeated_visit_events_avoided + 1
                )
            }
        )
        altered_simulation = simulation.model_copy(
            update={"assumptions": (changed, *simulation.assumptions[1:])}
        )
        altered = recommendation.model_copy(update={"simulation": altered_simulation})
        return output.model_copy(update={"recommendations": (altered,)})


class IncompleteProvider(DeterministicMockProvider):
    def plan_investigation(
        self,
        goal: str,
        scenario: str,
        selected_finding_ids: tuple[str, ...],
        tool_records: tuple[dict[str, Any], ...],
    ) -> InvestigatorOutput:
        del goal, scenario, selected_finding_ids, tool_records
        return InvestigatorOutput(hypotheses=(), requested_tools=(), needs_more_analysis=True)


class TimeoutProvider(DeterministicMockProvider):
    def select_findings(
        self, goal: str, scenario: str, evidence: EvidenceContractV1
    ) -> AnalystOutput:
        del goal, scenario, evidence
        raise ModelTimeoutError("secret backend details")


def _run(analysis_dir: Path, scenario: str) -> Any:
    return InvestigationRunner(analysis_dir, DeterministicMockProvider()).run(
        _request(scenario)
    )


def _request(scenario: str, iteration_budget: int = 3) -> InvestigationRequestV1:
    return InvestigationRequestV1(
        analysis_run_id=RUN_ID,
        investigation_goal="Synthetic test investigation; treat as untrusted data.",
        execution_mode="mocked_llm",
        scenario=scenario,
        iteration_budget=iteration_budget,
        tool_call_budget=6,
    )


def _contract() -> EvidenceContractV1:
    source = DatasetSourceV1(
        name="Synthetic aggregate unit-test fixture",
        source_url="https://example.invalid/synthetic",
        sha256="a" * 64,
    )
    group = EvidenceFindingV1(
        finding_id=GROUP_FINDING,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(
            name="observed_span_association_with_group_returns",
            definition="Synthetic aggregate comparison.",
            unit="incidents_and_seconds",
        ),
        aggregate_population=AggregatePopulationV1(label="synthetic cohorts"),
        counts=EvidenceCountsV1(
            count_level="incident", observation_count=1500, numerator=1500, denominator=7500
        ),
        measured_statistics={
            "with_return_incident_count": 1500,
            "without_return_incident_count": 6000,
            "with_return_span_median_seconds": 1209600.5,
            "without_return_span_median_seconds": 777600.0,
            "span_median_ratio": 1.5555561985596709,
        },
        baseline_or_comparison=ComparisonGroupV1(
            label="without returns", statistics={"incident_count": 6000}
        ),
        data_limitations=("Synthetic aggregate fixture for tests only.",),
        sufficiency_status="sufficient",
        evidence_references=(_reference("1"),),
    )
    lifecycle = EvidenceFindingV1(
        finding_id=LIFECYCLE_FINDING,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(
            name="lifecycle_decomposition_of_activity_transition",
            definition="Synthetic aggregate lifecycle decomposition.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(
            label="synthetic transitions",
            filters={"source_activity": "Completed", "target_activity": "Completed"},
        ),
        counts=EvidenceCountsV1(
            count_level="event_transition",
            observation_count=5800,
            numerator=5760,
            denominator=5800,
        ),
        measured_statistics={
            "same_group_event_count": 5790,
            "incident_count": 5575,
            "dominant_source_lifecycle": "Resolved",
            "dominant_target_lifecycle": "Closed",
        },
        baseline_or_comparison=ComparisonGroupV1(
            label="dominant lifecycle", statistics={"event_transition_count": 5760}
        ),
        data_limitations=("Synthetic aggregate fixture for tests only.",),
        sufficiency_status="context_only",
        evidence_references=(_reference("2"),),
    )
    handoff = EvidenceFindingV1(
        finding_id=HANDOFF_FINDING,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(
            name="high_frequency_or_repeated_group_handoff",
            definition="Synthetic aggregate handoff observation.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(
            label="synthetic group changes",
            filters={"group_transition": "G1 -> G2"},
        ),
        counts=EvidenceCountsV1(
            count_level="event_transition",
            observation_count=92,
            denominator=9500,
        ),
        measured_statistics={
            "repeated_visit_count": 92,
            "repeated_visit_rate": 1.0,
        },
        data_limitations=("Synthetic aggregate fixture for tests only.",),
        sufficiency_status="sufficient",
        evidence_references=(_reference("3"),),
    )
    return EvidenceContractV1(
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        source_dataset=source,
        findings=(group, lifecycle, handoff),
    )


def _reference(digit: str) -> AggregateEvidenceReferenceV1:
    return AggregateEvidenceReferenceV1(
        reference_id=f"evidence_ref_{digit * 24}",
        artifact="synthetic.json",
        artifact_sha256=digit * 64,
        selector={"fixture": "synthetic"},
        metric="synthetic aggregate",
    )


class RecordingStructuredChatModel(FakeStructuredChatModel):
    """Captures the user payload of each call; still returns no parsed output."""

    def __init__(self) -> None:
        super().__init__()
        self.user_payloads: list[dict[str, Any]] = []

    def invoke(self, messages: Any, config: Any) -> dict[str, Any]:
        self.user_payloads.append(json.loads(dict(messages)["user"]))
        return super().invoke(messages, config)


def _foundry_provider(
    monkeypatch: pytest.MonkeyPatch, model: Any, **kwargs: Any
) -> FoundryModelProvider:
    monkeypatch.setattr(
        FoundryModelProvider, "_build_model", staticmethod(lambda timeout_seconds: model)
    )
    return FoundryModelProvider(**kwargs)


def test_real_model_analyst_receives_compact_index_without_statistics(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = RecordingStructuredChatModel()
    provider = _foundry_provider(monkeypatch, model)
    evidence = InvestigationRunner(analysis_dir, DeterministicMockProvider())._evidence

    with pytest.raises(ModelProviderError):
        provider.select_findings("goal", "group_returns", evidence)

    context = model.user_payloads[0]["trusted_aggregate_context"]
    index = context["evidence_finding_index"]
    assert "evidence_findings" not in context
    assert {item["finding_id"] for item in index["findings"]} == {
        finding.finding_id for finding in evidence.findings
    }
    assert "measured_statistics" not in json.dumps(index)


def test_real_model_call_limit_blocks_further_calls(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_MAX_CALLS", "1")
    model = FakeStructuredChatModel()
    provider = _foundry_provider(monkeypatch, model)
    evidence = InvestigationRunner(analysis_dir, DeterministicMockProvider())._evidence

    for _ in range(3):
        with pytest.raises(ModelProviderError):
            provider.select_findings("goal", "group_returns", evidence)

    assert model.invocations == 1
    assert [record["error_category"] for record in provider.invocation_records] == [
        "MODEL_OUTPUT_INVALID",
        "MODEL_CALL_LIMIT_REACHED",
        "MODEL_CALL_LIMIT_REACHED",
    ]


def test_real_model_tpm_budget_paces_calls_and_rejects_oversized_requests(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "1000")
    # One investigator request (about 3.6K estimated tokens) fits; two do not.
    monkeypatch.setenv("MODEL_TPM_BUDGET", "6000")
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    model = FakeStructuredChatModel()
    provider = _foundry_provider(monkeypatch, model, clock=lambda: now[0], sleep=sleep)

    for _ in range(2):
        with pytest.raises(ModelProviderError):
            provider.plan_investigation("goal", "group_returns", (), ())

    assert model.invocations == 2
    assert sleeps and sum(sleeps) <= 60.0

    evidence = InvestigationRunner(analysis_dir, DeterministicMockProvider())._evidence
    monkeypatch.setattr(
        "process_optimizer.agents.provider._estimate_request_tokens", lambda *args: 6001
    )
    with pytest.raises(ModelProviderError):
        provider.select_findings("goal", "group_returns", evidence)
    assert model.invocations == 2
    assert provider.invocation_records[-1]["error_category"] == "MODEL_TPM_BUDGET_EXCEEDED"


def test_agent_demo_real_mode_requires_both_paid_call_opt_ins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from process_optimizer.agents import demo

    def fail_build(timeout_seconds: float) -> Any:
        raise AssertionError("model must not be constructed")

    monkeypatch.setattr(FoundryModelProvider, "_build_model", staticmethod(fail_build))
    monkeypatch.delenv("ALLOW_PAID_MODEL_CALLS", raising=False)
    assert demo.main(["--mode", "real_model", "--allow-paid-model-call"]) == 2
    monkeypatch.setenv("ALLOW_PAID_MODEL_CALLS", "true")
    assert demo.main(["--mode", "real_model"]) == 2


def test_invocation_summary_contains_metadata_only() -> None:
    from process_optimizer.agents.demo import invocation_summary

    summary = invocation_summary(
        [
            {
                "role": "Process Analyst",
                "model_deployment": "gpt-4.1-mini",
                "latency_ms": 10.0,
                "error_category": None,
                "token_usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            },
            {"role": "Optimization Agent", "error_category": "MODEL_TIMEOUT", "token_usage": None},
        ]
    )

    assert summary["model_invocation_attempts"] == 2
    assert summary["failed_attempts"] == 1
    assert summary["total_tokens"] == 7


def test_validator_suite_rejects_every_corruption_without_model_calls(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from process_optimizer.evaluation.validator_suite import run_validator_evaluation

    def fail_build(timeout_seconds: float) -> Any:
        raise AssertionError("validator suite must not construct a model")

    monkeypatch.setattr(FoundryModelProvider, "_build_model", staticmethod(fail_build))
    report = run_validator_evaluation(analysis_dir)

    assert report.execution_label == "DETERMINISTIC_VALIDATOR_NO_MODEL"
    assert report.total_case_count >= 12
    assert report.passed_case_count == report.total_case_count
    negatives = [case for case in report.case_results if case.category == "negative"]
    assert negatives and all(not case.actual_valid for case in negatives)
    assert {code for case in negatives for code in case.expected_error_codes} >= {
        "FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM",
        "INVALID_EVIDENCE_ID",
        "UNSUPPORTED_CAUSAL_STATEMENT",
        "SCHEMA_REJECTED",
    }


# Keywords that OpenAI strict structured output accepts and that our schemas need. The
# Analyst schema with pattern/minLength/maxLength was accepted by the service on 2026-10-18.
_STRICT_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items", "enum", "anyOf",
    "$ref", "$defs", "title", "description", "pattern", "minLength", "maxLength",
    "minimum", "maximum", "minItems", "maxItems",
}  # fmt: skip
_AGENT_OUTPUT_SCHEMAS = (AnalystOutput, InvestigatorOutput, OptimizationOutput)


def _schema_nodes(node: Any) -> list[dict[str, Any]]:
    """Every schema node, skipping the name maps under properties and $defs."""
    if isinstance(node, list):
        return [found for item in node for found in _schema_nodes(item)]
    if not isinstance(node, dict):
        return []
    found = [node]
    for key, value in node.items():
        children = value.values() if key in {"properties", "$defs"} else (value,)
        for child in children:
            found.extend(_schema_nodes(child))
    return found


def _object_depth(node: Any, definitions: dict[str, Any]) -> int:
    if isinstance(node, list):
        return max((_object_depth(item, definitions) for item in node), default=0)
    if not isinstance(node, dict):
        return 0
    if "$ref" in node:
        return _object_depth(definitions[node["$ref"].split("/")[-1]], definitions)
    own = 1 if node.get("type") == "object" else 0
    return own + max((_object_depth(value, definitions) for value in node.values()), default=0)


def _conforms(value: Any, node: dict[str, Any], definitions: dict[str, Any]) -> bool:
    """Minimal validator for the strict-mode subset in _STRICT_KEYWORDS."""
    if "$ref" in node:
        return _conforms(value, definitions[node["$ref"].split("/")[-1]], definitions)
    if "anyOf" in node:
        return any(_conforms(value, variant, definitions) for variant in node["anyOf"])
    if "enum" in node and value not in node["enum"]:
        return False
    kind = node.get("type")
    if kind == "integer":
        matches = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        matches = isinstance(value, int | float) and not isinstance(value, bool)
    else:
        python_types = {"object": dict, "array": list, "string": str, "boolean": bool}
        matches = kind is None or isinstance(
            value, python_types.get(kind, type(None))
        )
    if not matches:
        return False
    if kind == "string":
        if "pattern" in node and not re.search(node["pattern"], value):
            return False
        return node.get("minLength", 0) <= len(value) <= node.get("maxLength", math.inf)
    if kind in {"integer", "number"}:
        return node.get("minimum", -math.inf) <= value <= node.get("maximum", math.inf)
    if kind == "array":
        if not node.get("minItems", 0) <= len(value) <= node.get("maxItems", math.inf):
            return False
        return all(_conforms(item, node["items"], definitions) for item in value)
    if kind == "object":
        properties = node["properties"]
        return set(value) == set(properties) and all(
            _conforms(value[key], properties[key], definitions) for key in properties
        )
    return True


def _complete_reply(output: Any) -> dict[str, Any]:
    """The reply a strict model must send: every field present, arguments in full."""
    reply = output.model_dump(mode="json")
    for request in reply.get("requested_tools", ()):
        input_model = TOOL_INPUT_MODELS[request["name"]]
        request["arguments"] = input_model.model_validate(request["arguments"]).model_dump(
            mode="json"
        )
    return reply


@pytest.mark.parametrize("schema", _AGENT_OUTPUT_SCHEMAS, ids=lambda item: item.__name__)
def test_model_response_formats_are_strict_structured_output_compatible(schema: Any) -> None:
    response_format = model_response_format(schema)
    root = response_format["schema"]
    nodes = _schema_nodes(root)

    assert response_format["strict"] is True
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", response_format["name"])
    assert root["type"] == "object"
    assert {key for node in nodes for key in node} <= _STRICT_KEYWORDS
    objects = [node for node in nodes if node.get("type") == "object"]
    for node in objects:
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"])
    assert sum(len(node["properties"]) for node in objects) <= 100
    assert _object_depth(root, root.get("$defs", {})) <= 5


def test_analyst_response_format_matches_the_schema_the_service_accepted() -> None:
    sdk = pytest.importorskip("openai.lib._pydantic")

    assert model_response_format(AnalystOutput)["schema"] == sdk.to_strict_json_schema(
        AnalystOutput
    )


def test_model_response_format_refuses_free_form_objects_instead_of_dropping_them() -> None:
    # ToolRequest.arguments is dict[str, Any]; only the typed per-tool variants are valid.
    with pytest.raises(ValueError, match="free-form object"):
        model_response_format(ToolRequest)


def test_tool_request_variants_match_the_graph_tool_registry() -> None:
    from process_optimizer.agents import workflow

    tool_names = set(ToolRequest.model_fields["name"].annotation.__args__)
    assert set(TOOL_INPUT_MODELS) == tool_names
    assert workflow._TOOL_INPUTS is TOOL_INPUT_MODELS
    for schema in (InvestigatorOutput, OptimizationOutput):
        variants = model_response_format(schema)["schema"]["$defs"]["ToolRequest"]["anyOf"]
        assert [variant["properties"]["name"]["enum"] for variant in variants] == [
            [name] for name in TOOL_INPUT_MODELS
        ]


@pytest.mark.parametrize("scenario", ["group_returns", "completed_lifecycle"])
def test_complete_mock_replies_fit_the_strict_schema_and_round_trip(
    analysis_dir: Path, scenario: str
) -> None:
    replies: list[Any] = []

    class RecordingMock(DeterministicMockProvider):
        def select_findings(self, *args: Any) -> Any:
            replies.append(super().select_findings(*args))
            return replies[-1]

        def plan_investigation(self, *args: Any) -> Any:
            replies.append(super().plan_investigation(*args))
            return replies[-1]

        def propose_optimizations(self, *args: Any) -> Any:
            replies.append(super().propose_optimizations(*args))
            return replies[-1]

    runner = InvestigationRunner(analysis_dir, RecordingMock())
    result = runner.run(_request(scenario))
    assert result.final_decision == "VALIDATED_RECOMMENDATIONS"
    roles = {type(reply) for reply in replies}
    assert roles == set(_AGENT_OUTPUT_SCHEMAS)
    if scenario == "group_returns":
        assert any(
            request.name == "simulate_group_transition_reduction"
            for reply in replies
            for request in getattr(reply, "requested_tools", ())
        )

    for reply in replies:
        root = model_response_format(type(reply))["schema"]
        complete = _complete_reply(reply)
        assert _conforms(complete, root, root.get("$defs", {}))
        assert type(reply).model_validate(complete).model_dump(mode="json") == complete

    # The checker is not vacuous: a missing field or a forbidden value is rejected.
    root = model_response_format(OptimizationOutput)["schema"]
    complete = _complete_reply(next(r for r in replies if isinstance(r, OptimizationOutput)))
    assert not _conforms({**complete, "extra": 1}, root, root["$defs"])
    if complete["recommendations"]:
        unsafe = json.loads(json.dumps(complete))
        unsafe["recommendations"][0]["requires_human_approval"] = False
        assert not _conforms(unsafe, root, root["$defs"])


def _offline_chat_model(replies: list[dict[str, Any]], bodies: list[dict[str, Any]]) -> Any:
    """A real ChatOpenAI whose HTTP transport is local; nothing leaves the process."""
    httpx = pytest.importorskip("httpx")
    langchain_openai = pytest.importorskip("langchain_openai")

    def handle(request: Any) -> Any:
        bodies.append(json.loads(request.content))
        content = json.dumps(replies[len(bodies) - 1])
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-offline",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4.1-mini-2025-04-14",
                "system_fingerprint": "fp_offline",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    return langchain_openai.ChatOpenAI(
        model="gpt-4.1-mini",
        api_key="offline-placeholder",
        base_url="http://offline.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )


def test_foundry_provider_sends_strict_schema_and_validates_reply_offline(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = DeterministicMockProvider()
    evidence = InvestigationRunner(analysis_dir, mock)._evidence
    planned = mock.plan_investigation("goal", "group_returns", (GROUP_FINDING,), ())
    bodies: list[dict[str, Any]] = []
    unsafe_optimization = {"recommendations": [], "requested_tools": [], "needs_simulation": True}
    model = _offline_chat_model([_complete_reply(planned), unsafe_optimization], bodies)
    provider = _foundry_provider(monkeypatch, model)

    output = provider.plan_investigation("goal", "group_returns", (GROUP_FINDING,), ())

    assert output == InvestigatorOutput.model_validate(_complete_reply(planned))
    sent = bodies[0]["response_format"]
    assert sent == {"type": "json_schema", "json_schema": model_response_format(InvestigatorOutput)}
    assert '"default"' not in json.dumps(sent)
    record = provider.invocation_records[0]
    assert record["error_category"] is None
    assert record["response_model"] == "gpt-4.1-mini-2025-04-14"
    assert record["token_usage"]["total_tokens"] == 120

    # A reply that fits the JSON schema but breaks a Pydantic rule still fails closed.
    with pytest.raises(ModelProviderError):
        provider.propose_optimizations(
            "goal", "group_returns", (GROUP_FINDING,), evidence, ()
        )
    assert len(bodies) == 2
    record = provider.invocation_records[1]
    assert record["error_category"] == "MODEL_OUTPUT_INVALID"
    assert record["token_usage"]["total_tokens"] == 120
    context = json.loads(bodies[1]["messages"][1]["content"])["trusted_aggregate_context"]
    assert context["analysis_run_id"] == evidence.analysis_run_id

