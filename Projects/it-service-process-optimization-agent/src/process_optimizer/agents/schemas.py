"""Versioned typed contracts for the Phase 4 investigation graph."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GRAPH_STATE_VERSION = "1.1.0"


class FrozenModel(BaseModel):
    """Immutable externally visible model."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RecommendationStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class FinalDecision(StrEnum):
    PENDING = "PENDING"
    VALIDATED_RECOMMENDATIONS = "VALIDATED_RECOMMENDATIONS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SAFE_TERMINATION = "SAFE_TERMINATION"


class EvidenceSelection(FrozenModel):
    finding_id: str = Field(pattern=r"^finding_[0-9a-f]{24}$")
    rationale: str = Field(min_length=1, max_length=1000)


class ToolRequest(FrozenModel):
    name: Literal[
        "get_process_overview",
        "get_bottleneck_findings",
        "analyze_group_handoffs",
        "compare_process_cohorts",
        "inspect_lifecycle_patterns",
        "get_evidence_details",
        "simulate_group_transition_reduction",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(FrozenModel):
    call_id: str = Field(pattern=r"^tool_call_[0-9a-f]{24}$")
    sequence: int = Field(ge=1)
    tool_name: str
    validated_arguments: dict[str, Any]
    status: Literal["success", "invalid_arguments", "tool_error"]
    returned_count: int = Field(default=0, ge=0, le=100)
    truncated: bool = False
    result_items: tuple[dict[str, Any], ...] = ()
    evidence_reference_ids: tuple[str, ...] = ()
    error_code: str | None = None

    @model_validator(mode="after")
    def enforce_safe_record(self) -> ToolCallRecord:
        serialized = self.model_dump_json().lower()
        forbidden = (
            '"case_id"',
            '"event_id"',
            "org:resource",
            '"resource_id"',
            '"resource_identifier"',
            "raw_path",
            "sql",
        )
        if any(token in serialized for token in forbidden):
            raise ValueError("Tool audit record contains prohibited content")
        return self


class Hypothesis(FrozenModel):
    hypothesis_id: str = Field(pattern=r"^hypothesis_[0-9a-f]{24}$")
    statement: str = Field(min_length=1, max_length=1500)
    evidence_ids: tuple[str, ...]
    status: Literal["untested", "supported_observation", "not_supported", "inconclusive"]
    explicitly_non_causal: bool = True


class ObservedNumericalFact(FrozenModel):
    statement: str = Field(min_length=1, max_length=1000)
    evidence_id: str = Field(pattern=r"^finding_[0-9a-f]{24}$")
    field_path: str = Field(min_length=1, max_length=300)
    value: int | float
    unit: str = Field(min_length=1, max_length=100)


class SimulationAssumptionCitationV1(FrozenModel):
    name: Literal["conservative", "moderate", "optimistic"]
    eligible_event_share_assumption: float = Field(ge=0.0, le=1.0)
    reduction_share_assumption: float = Field(ge=0.0, le=1.0)
    hypothetical_repeated_visit_events_avoided: float = Field(ge=0.0)


class SimulationCitationV1(FrozenModel):
    simulation_id: str = Field(pattern=r"^simulation_[0-9a-f]{24}$")
    classification: Literal["hypothetical_non_causal"] = "hypothetical_non_causal"
    status: Literal["ESTIMATED", "NOT_ESTIMABLE"]
    assumptions: tuple[SimulationAssumptionCitationV1, ...]


class RecommendationCandidate(FrozenModel):
    recommendation_id: str = Field(pattern=r"^recommendation_[0-9a-f]{24}$")
    problem_statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[str, ...]
    observed_numerical_facts: tuple[ObservedNumericalFact, ...]
    hypotheses: tuple[str, ...]
    data_limitations: tuple[str, ...]
    suggested_process_improvement: str = Field(min_length=1, max_length=3000)
    expected_outcome_with_uncertainty: str = Field(min_length=1, max_length=2000)
    proposed_follow_up_kpi: str = Field(min_length=1, max_length=1000)
    implementation_risk: str = Field(min_length=1, max_length=1500)
    simulation: SimulationCitationV1 | None = None
    status: RecommendationStatus = RecommendationStatus.DRAFT
    requires_human_approval: Literal[True] = True


class EvidenceValidationResult(FrozenModel):
    recommendation_id: str
    valid: bool
    validated_evidence_ids: tuple[str, ...]
    error_codes: tuple[str, ...]
    notes: tuple[str, ...]


class GraphError(FrozenModel):
    code: Literal[
        # Retained so results produced before the provider taxonomy still validate.
        "MODEL_TIMEOUT",
        "MODEL_OUTPUT_INVALID",
        "PROVIDER_AUTH_FAILED",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_BAD_REQUEST",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_CONNECTION_FAILED",
        "PROVIDER_CONFIGURATION_ERROR",
        "INTERNAL_ERROR",
        "TOOL_ARGUMENT_INVALID",
        "TOOL_EXECUTION_FAILED",
        "EVIDENCE_MISSING",
        "ITERATION_LIMIT_REACHED",
        "TOOL_BUDGET_EXHAUSTED",
        "INCOMPLETE_INVESTIGATION",
        "GUARDRAIL_BLOCKED",
        "MODEL_CALL_LIMIT_REACHED",
        "RUN_DEADLINE_EXCEEDED",
        "MODEL_TOKEN_BUDGET_EXHAUSTED",
    ]
    stage: str
    retryable: bool
    safe_message: str


class AuditEvent(FrozenModel):
    sequence: int = Field(ge=1)
    event_type: str
    stage: str
    outcome: str
    safe_details: dict[str, int | float | bool | str | None] = Field(default_factory=dict)


class InvestigationRequestV1(FrozenModel):
    schema_version: Literal["1.1.0"] = GRAPH_STATE_VERSION
    analysis_run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    investigation_goal: str = Field(min_length=1, max_length=2000)
    execution_mode: Literal["mocked_llm", "real_model"] = "mocked_llm"
    scenario: Literal["group_returns", "completed_lifecycle", "custom"] = "custom"
    iteration_budget: int = Field(default=3, ge=1, le=10)
    tool_call_budget: int = Field(default=6, ge=0, le=20)


class InvestigationStateV1(BaseModel):
    """LangGraph state; node updates are validated at every boundary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1.0"] = GRAPH_STATE_VERSION
    analysis_run_id: str
    investigation_goal: str
    execution_mode: Literal["mocked_llm", "real_model"]
    scenario: Literal["group_returns", "completed_lifecycle", "custom"]
    selected_findings: tuple[EvidenceSelection, ...] = ()
    pending_tool_calls: tuple[ToolRequest, ...] = ()
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    recommendation_candidates: tuple[RecommendationCandidate, ...] = ()
    validation_results: tuple[EvidenceValidationResult, ...] = ()
    errors: tuple[GraphError, ...] = ()
    audit_events: tuple[AuditEvent, ...] = ()
    iteration_budget: int = Field(ge=1, le=10)
    iterations_used: int = Field(default=0, ge=0)
    tool_call_budget: int = Field(ge=0, le=20)
    tool_calls_used: int = Field(default=0, ge=0)
    investigation_complete: bool = False
    tool_return_target: Literal["investigator", "optimizer"] = "investigator"
    optimization_attempts: int = Field(default=0, ge=0, le=2)
    # Per-run guardrail budget; both are internal and not part of the result contract.
    model_calls_used: int = Field(default=0, ge=0)
    model_tokens_used: int = Field(default=0, ge=0)
    deadline_epoch_seconds: float | None = None
    final_decision: FinalDecision = FinalDecision.PENDING


class InvestigationResultV1(FrozenModel):
    """Stable externally visible graph output."""

    schema_version: Literal["1.1.0"] = GRAPH_STATE_VERSION
    analysis_run_id: str
    execution_label: Literal["DETERMINISTIC_MOCK_NOT_REAL_AI", "REAL_MODEL_OUTPUT"]
    investigation_goal: str
    selected_findings: tuple[EvidenceSelection, ...]
    tool_call_records: tuple[ToolCallRecord, ...]
    hypotheses: tuple[Hypothesis, ...]
    recommendations: tuple[RecommendationCandidate, ...]
    validation_results: tuple[EvidenceValidationResult, ...]
    errors: tuple[GraphError, ...]
    audit_events: tuple[AuditEvent, ...]
    iterations_used: int
    tool_calls_used: int
    final_decision: FinalDecision


class AnalystOutput(FrozenModel):
    selected_findings: tuple[EvidenceSelection, ...]


class InvestigatorOutput(FrozenModel):
    hypotheses: tuple[Hypothesis, ...]
    requested_tools: tuple[ToolRequest, ...]
    needs_more_analysis: bool


class OptimizationOutput(FrozenModel):
    recommendations: tuple[RecommendationCandidate, ...]
    requested_tools: tuple[ToolRequest, ...] = ()
    needs_simulation: bool = False

    @model_validator(mode="after")
    def validate_simulation_request(self) -> OptimizationOutput:
        if self.needs_simulation and not self.requested_tools:
            raise ValueError("needs_simulation requires a tool request")
        if self.needs_simulation and self.recommendations:
            raise ValueError("recommendations must wait for requested simulation evidence")
        return self
