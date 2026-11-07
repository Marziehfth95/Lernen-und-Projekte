"""Typed Phase 5 agent-evaluation records."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvaluationCaseV1(FrozenModel):
    case_id: str = Field(pattern=r"^evaluation_case_[a-z0-9_]+$")
    scenario: Literal["group_returns", "completed_lifecycle"]
    goal: str
    expected_tools: tuple[str, ...]
    expected_final_decision: str
    prohibited_interpretations: tuple[str, ...]


class EvaluationCaseResultV1(FrozenModel):
    case_id: str
    scenario: str
    passed: bool
    tool_call_correctness: bool
    recommendation_groundedness: bool
    numerical_consistency: bool
    semantic_safety: bool
    no_business_action_execution: bool
    actual_tool_calls: tuple[str, ...]
    validation_outcomes: tuple[bool, ...]
    simulation_ids: tuple[str, ...]
    final_decision: str
    latency_ms: float = Field(ge=0.0)
    error_categories: tuple[str, ...]


class ModelInvocationRecordV1(FrozenModel):
    role: str
    model_deployment: str
    prompt_version: str
    workflow_version: str
    latency_ms: float = Field(ge=0.0)
    token_usage: dict[str, Any] | None = None
    error_category: str | None = None
    response_model: str | None = None
    system_fingerprint: str | None = None


class EvaluationReportV1(FrozenModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    evaluation_dataset_version: Literal["phase5-evaluation-v1"] = "phase5-evaluation-v1"
    prompt_version: Literal[
        "phase5-prompts-v1", "phase5-prompts-v2", "phase5-prompts-v3"
    ] = "phase5-prompts-v3"
    workflow_version: Literal["phase5-workflow-v1"] = "phase5-workflow-v1"
    execution_mode: Literal["mocked_llm", "real_model"]
    execution_label: Literal["DETERMINISTIC_MOCK_NOT_REAL_AI", "REAL_MODEL_OUTPUT"]
    model_deployment: str
    analysis_run_id: str
    case_results: tuple[EvaluationCaseResultV1, ...]
    invocation_records: tuple[ModelInvocationRecordV1, ...]
    passed_case_count: int = Field(ge=0)
    total_case_count: int = Field(ge=0)
    token_usage_total: dict[str, int] | None = None
    estimated_cost_usd: float | None = None
    pricing_assumption: str | None = None
    limitations: tuple[str, ...]
