"""Conditional LangGraph workflow for evidence-grounded process investigation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from process_optimizer.agents.provider import (
    TOOL_INPUT_MODELS,
    DeterministicMockProvider,
    ModelProviderError,
    ModelTimeoutError,
    StructuredModelProvider,
    capture_model_usage,
)
from process_optimizer.agents.provider_errors import RETRYABLE_CATEGORIES
from process_optimizer.agents.schemas import (
    AuditEvent,
    FinalDecision,
    GraphError,
    InvestigationRequestV1,
    InvestigationResultV1,
    InvestigationStateV1,
    RecommendationStatus,
    ToolCallRecord,
    ToolRequest,
)
from process_optimizer.agents.validation import validate_recommendations
from process_optimizer.guardrails import Decision, GuardrailDecisionV1, GuardrailEngine
from process_optimizer.observability import operation_span, record_operational_event
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.tools.analytics import AggregateAnalyticsTools

_TOOL_INPUTS: dict[str, type[Any]] = TOOL_INPUT_MODELS


class InvestigationRunner:
    """Own the trusted evidence, read-only tools, provider, and compiled graph."""

    def __init__(
        self,
        analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
        provider: StructuredModelProvider | None = None,
        tools: Any | None = None,
        evidence: EvidenceContractV1 | None = None,
        guardrails: GuardrailEngine | None = None,
        *,
        max_model_calls_per_run: int | None = None,
        run_deadline_seconds: float | None = None,
        run_token_budget: int | None = None,
    ) -> None:
        self._analysis_dir = Path(analysis_dir)
        self._evidence = evidence or EvidenceContractV1.model_validate_json(
            (self._analysis_dir / "evidence_contract.json").read_text(encoding="utf-8")
        )
        self._guardrails = guardrails or GuardrailEngine()
        limits = self._guardrails.policy.hosted_runtime
        # Configuration may only tighten the policy limits, never widen them.
        self._max_model_calls = min(
            max_model_calls_per_run
            or _env_int("MODEL_RUN_MAX_CALLS", limits.max_model_calls_per_run),
            limits.max_model_calls_per_run,
        )
        self._run_deadline_seconds = min(
            run_deadline_seconds or _env_int("RUN_DEADLINE_SECONDS", limits.run_deadline_seconds),
            limits.run_deadline_seconds,
        )
        self._run_token_budget = min(
            run_token_budget or _env_int("MODEL_RUN_TOKEN_BUDGET", limits.max_run_tokens),
            limits.max_run_tokens,
        )
        self._call_reservation = limits.call_reservation_tokens
        # Models only ever see findings without small groups; the validator keeps the
        # complete trusted contract so citations are still checked exactly.
        visible = self._guardrails.visible_finding_ids(self._evidence.findings)
        self._model_evidence = self._evidence.model_copy(
            update={
                "findings": tuple(
                    item for item in self._evidence.findings if item.finding_id in visible
                )
            }
        )
        self._tools = tools or AggregateAnalyticsTools(
            self._analysis_dir,
            small_group_threshold=self._guardrails.policy.small_group_threshold,
        )
        self._provider = provider or DeterministicMockProvider()
        self.graph = self._build_graph()

    @property
    def guardrail_policy_version(self) -> str:
        return self._guardrails.policy.version

    def run(self, request: InvestigationRequestV1) -> InvestigationResultV1:
        """Run a bounded investigation and return a stable typed result."""
        with operation_span(
            "process_optimizer.investigation",
            {
                "analysis_run_id": request.analysis_run_id,
                "scenario": request.scenario,
                "execution_mode": request.execution_mode,
            },
        ) as span:
            initial = self.build_initial_state(request)
            raw = self.graph.invoke(initial)
            result = self.build_result(raw)
            span.set(
                {
                    "final_decision": str(result.final_decision),
                    "iterations_used": result.iterations_used,
                    "tool_calls_used": result.tool_calls_used,
                    "recommendation_count": len(result.recommendations),
                }
            )
            if result.final_decision == FinalDecision.SAFE_TERMINATION:
                span.fail(result.errors[-1].code if result.errors else "SAFE_TERMINATION")
            elif result.final_decision == FinalDecision.VALIDATED_RECOMMENDATIONS:
                span.set({"outcome": "success"})
            else:
                span.set({"outcome": "insufficient_evidence"})
            return result

    @property
    def analysis_run_id(self) -> str:
        """Return the trusted evidence run identifier without exposing the contract."""
        return self._evidence.analysis_run_id

    def build_initial_state(self, request: InvestigationRequestV1) -> InvestigationStateV1:
        """Validate a request and map it to the graph's custom state."""
        if request.analysis_run_id != self._evidence.analysis_run_id:
            raise ValueError("Request analysis_run_id does not match trusted evidence")
        if request.execution_mode != self._provider.mode:
            raise ValueError("Request execution mode does not match configured provider")
        screened = self._guardrails.check_invocation_input(goal=request.investigation_goal)
        if screened.decision != Decision.ALLOW:
            # Reason codes are fixed enums, so echoing them never reflects caller input.
            raise ValueError(
                "Investigation goal was blocked by input guardrails: "
                + ", ".join(screened.reason_codes)
            )
        return InvestigationStateV1(
            analysis_run_id=request.analysis_run_id,
            investigation_goal=request.investigation_goal,
            execution_mode=request.execution_mode,
            scenario=request.scenario,
            iteration_budget=request.iteration_budget,
            tool_call_budget=request.tool_call_budget,
            deadline_epoch_seconds=time.time() + self._run_deadline_seconds,
            audit_events=(
                AuditEvent(
                    sequence=1,
                    event_type="investigation_started",
                    stage="initialize",
                    outcome="accepted",
                    safe_details={
                        "iteration_budget": request.iteration_budget,
                        "tool_call_budget": request.tool_call_budget,
                        "model_call_budget": self._max_model_calls,
                        "model_token_budget": self._run_token_budget,
                        "run_deadline_seconds": self._run_deadline_seconds,
                        **screened.audit_details(),
                    },
                ),
            ),
        )

    @staticmethod
    def build_result(raw: Any) -> InvestigationResultV1:
        """Map custom graph state to the externally visible result schema."""
        state = InvestigationStateV1.model_validate(raw)
        return InvestigationResultV1(
            analysis_run_id=state.analysis_run_id,
            execution_label=(
                "DETERMINISTIC_MOCK_NOT_REAL_AI"
                if state.execution_mode == "mocked_llm"
                else "REAL_MODEL_OUTPUT"
            ),
            investigation_goal=state.investigation_goal,
            selected_findings=state.selected_findings,
            tool_call_records=state.tool_call_records,
            hypotheses=state.hypotheses,
            recommendations=state.recommendation_candidates,
            validation_results=state.validation_results,
            errors=state.errors,
            audit_events=state.audit_events,
            iterations_used=state.iterations_used,
            tool_calls_used=state.tool_calls_used,
            final_decision=state.final_decision,
        )

    def _build_graph(self) -> Any:
        builder = StateGraph(InvestigationStateV1)
        builder.add_node("analyst", self._traced("analyst", self._analyst))
        builder.add_node("investigator", self._traced("investigator", self._investigator))
        builder.add_node("tool", self._traced("tool", self._execute_tool))
        builder.add_node("optimizer", self._traced("optimizer", self._optimizer))
        builder.add_node("validator", self._traced("validator", self._validator))
        builder.add_node("finalizer", self._traced("finalizer", self._finalizer))
        builder.add_edge(START, "analyst")
        builder.add_conditional_edges(
            "analyst",
            self._route_after_analyst,
            {"investigator": "investigator", "finalizer": "finalizer"},
        )
        builder.add_conditional_edges(
            "investigator",
            self._route_after_investigator,
            {"tool": "tool", "optimizer": "optimizer", "finalizer": "finalizer"},
        )
        builder.add_conditional_edges(
            "tool",
            self._route_after_tool,
            {
                "tool": "tool",
                "investigator": "investigator",
                "optimizer": "optimizer",
                "finalizer": "finalizer",
            },
        )
        builder.add_conditional_edges(
            "optimizer",
            self._route_after_optimizer,
            {"tool": "tool", "validator": "validator", "finalizer": "finalizer"},
        )
        builder.add_edge("validator", "finalizer")
        builder.add_edge("finalizer", END)
        return builder.compile()

    @staticmethod
    def _traced(
        node: str, step: Callable[[InvestigationStateV1], dict[str, Any]]
    ) -> Callable[[InvestigationStateV1], dict[str, Any]]:
        """Wrap a node in a child span; nodes run inside the graph host too."""

        def traced(state: InvestigationStateV1) -> dict[str, Any]:
            with operation_span(
                f"process_optimizer.node.{node}",
                {"analysis_run_id": state.analysis_run_id, "node": node},
            ) as span:
                update = step(state)
                new_errors = tuple(update.get("errors", state.errors))[len(state.errors) :]
                summary: dict[str, object] = {}
                if "recommendation_candidates" in update:
                    summary["recommendation_count"] = len(update["recommendation_candidates"])
                if "validation_results" in update:
                    summary["valid_count"] = sum(r.valid for r in update["validation_results"])
                if "final_decision" in update:
                    summary["final_decision"] = str(update["final_decision"])
                for event in tuple(update.get("audit_events", ()))[len(state.audit_events) :]:
                    summary.update(
                        {
                            key: value
                            for key, value in event.safe_details.items()
                            if key.startswith("guardrail_")
                        }
                    )
                span.set(summary)
                if new_errors:
                    span.fail(new_errors[-1].code)
                else:
                    span.set({"outcome": "success"})
                return update

        return traced

    def _analyst(self, state: InvestigationStateV1) -> dict[str, Any]:
        output, spent = self._model_step(
            state,
            "analyst",
            lambda: self._provider.select_findings(
                state.investigation_goal, state.scenario, self._model_evidence
            ),
        )
        if output is None:
            return spent
        trusted_ids = {finding.finding_id for finding in self._model_evidence.findings}
        selected = tuple(
            selection
            for selection in output.selected_findings
            if selection.finding_id in trusted_ids
        )
        errors = state.errors
        if len(selected) != len(output.selected_findings) or not selected:
            errors += (
                GraphError(
                    code="EVIDENCE_MISSING",
                    stage="analyst",
                    retryable=False,
                    safe_message="No trusted investigation finding was selected.",
                ),
            )
        return {
            "selected_findings": selected,
            **spent,
            "errors": errors,
            "audit_events": self._audit(
                state,
                "findings_selected",
                "analyst",
                "success" if selected else "insufficient",
                {"selected_count": len(selected)},
            ),
        }

    def _investigator(self, state: InvestigationStateV1) -> dict[str, Any]:
        if state.iterations_used >= state.iteration_budget:
            return self._limit_update(state, "ITERATION_LIMIT_REACHED", "investigator")
        output, spent = self._model_step(
            state,
            "investigator",
            lambda: self._provider.plan_investigation(
                state.investigation_goal,
                state.scenario,
                tuple(item.finding_id for item in state.selected_findings),
                tuple(item.model_dump(mode="json") for item in state.tool_call_records),
            ),
        )
        if output is None:
            return spent
        errors = state.errors
        if output.needs_more_analysis and not output.requested_tools:
            errors += (
                GraphError(
                    code="INCOMPLETE_INVESTIGATION",
                    stage="investigator",
                    retryable=False,
                    safe_message="Further analysis was requested without a valid tool call.",
                ),
            )
        return {
            "hypotheses": output.hypotheses,
            "pending_tool_calls": output.requested_tools,
            "investigation_complete": not output.needs_more_analysis,
            "tool_return_target": "investigator",
            "iterations_used": state.iterations_used + 1,
            **spent,
            "errors": errors,
            "audit_events": self._audit(
                state,
                "investigation_planned",
                "investigator",
                "success",
                {
                    "requested_tool_count": len(output.requested_tools),
                    "needs_more_analysis": output.needs_more_analysis,
                },
            ),
        }

    def _execute_tool(self, state: InvestigationStateV1) -> dict[str, Any]:
        if not state.pending_tool_calls:
            return {}
        if state.tool_calls_used >= state.tool_call_budget:
            return self._limit_update(state, "TOOL_BUDGET_EXHAUSTED", "tool")
        request = state.pending_tool_calls[0]
        allowed = self._guardrails.check_tool_request(
            stage=state.tool_return_target,
            tool_name=request.name,
            deadline_epoch_seconds=state.deadline_epoch_seconds,
        )
        if allowed.blocked:
            return self._guardrail_block(state, "tool", allowed)
        record = self._call_tool(request, state.tool_calls_used + 1)
        screened = self._guardrails.check_tool_result(items=record.result_items)
        if screened.blocked:
            # Defence in depth behind tool-side suppression: drop the result entirely.
            return {
                **self._guardrail_block(state, "tool", screened),
                "tool_calls_used": state.tool_calls_used + 1,
            }
        errors = state.errors
        if record.status == "invalid_arguments":
            errors += (
                GraphError(
                    code="TOOL_ARGUMENT_INVALID",
                    stage="tool",
                    retryable=False,
                    safe_message="A read-only tool request failed argument validation.",
                ),
            )
        elif record.status == "tool_error":
            errors += (
                GraphError(
                    code="TOOL_EXECUTION_FAILED",
                    stage="tool",
                    retryable=True,
                    safe_message="A read-only aggregate tool could not complete.",
                ),
            )
        remaining = state.pending_tool_calls[1:]
        if (
            not remaining
            and state.tool_return_target != "optimizer"
            and state.iterations_used >= state.iteration_budget
        ):
            errors += (
                GraphError(
                    code="ITERATION_LIMIT_REACHED",
                    stage="investigator",
                    retryable=False,
                    safe_message="The configured investigation iteration budget was exhausted.",
                ),
            )
        return {
            "pending_tool_calls": remaining,
            "tool_call_records": (*state.tool_call_records, record),
            "tool_calls_used": state.tool_calls_used + 1,
            "errors": errors,
            "audit_events": self._audit(
                state,
                "tool_called",
                "tool",
                record.status,
                {"tool_name": request.name, "returned_count": record.returned_count},
            ),
        }

    def _optimizer(self, state: InvestigationStateV1) -> dict[str, Any]:
        if state.optimization_attempts >= 2:
            return self._limit_update(state, "ITERATION_LIMIT_REACHED", "optimizer")
        output, spent = self._model_step(
            state,
            "optimizer",
            lambda: self._provider.propose_optimizations(
                state.investigation_goal,
                state.scenario,
                tuple(item.finding_id for item in state.selected_findings),
                self._model_evidence,
                tuple(item.model_dump(mode="json") for item in state.tool_call_records),
            ),
        )
        if output is None:
            return spent
        errors = state.errors
        if output.needs_simulation:
            invalid = any(
                request.name != "simulate_group_transition_reduction"
                for request in output.requested_tools
            )
            if invalid:
                errors += (
                    GraphError(
                        code="TOOL_ARGUMENT_INVALID",
                        stage="optimizer",
                        retryable=False,
                        safe_message="The optimization agent requested a non-simulation tool.",
                    ),
                )
            return {
                "pending_tool_calls": output.requested_tools if not invalid else (),
                "tool_return_target": "optimizer",
                "optimization_attempts": state.optimization_attempts + 1,
                **spent,
                "errors": errors,
                "audit_events": self._audit(
                    state,
                    "simulation_requested",
                    "optimizer",
                    "success" if not invalid else "rejected",
                    {"requested_tool_count": len(output.requested_tools)},
                ),
            }
        if not output.recommendations:
            errors += (
                GraphError(
                    code="INCOMPLETE_INVESTIGATION",
                    stage="optimizer",
                    retryable=False,
                    safe_message="No recommendation candidate was produced.",
                ),
            )
        return {
            "recommendation_candidates": output.recommendations,
            "optimization_attempts": state.optimization_attempts + 1,
            **spent,
            "errors": errors,
            "audit_events": self._audit(
                state,
                "recommendations_proposed",
                "optimizer",
                "success" if output.recommendations else "insufficient",
                {"recommendation_count": len(output.recommendations)},
            ),
        }

    def _validator(self, state: InvestigationStateV1) -> dict[str, Any]:
        recommendations, results = validate_recommendations(
            state.recommendation_candidates,
            self._evidence,
            state.tool_call_records,
            policy=self._guardrails.policy,
        )
        decision = self._guardrails.check_recommendations(
            recommendations=recommendations, validation_results=results
        )
        if decision.decision != Decision.REQUIRE_APPROVAL:
            recommendations, results = _reject_all(recommendations, results, decision)
        return {
            "recommendation_candidates": recommendations,
            "validation_results": results,
            "audit_events": self._audit(
                state,
                "evidence_validated",
                "validator",
                "success" if results and all(item.valid for item in results) else "rejected",
                {
                    "validation_count": len(results),
                    "valid_count": sum(item.valid for item in results),
                    **decision.audit_details(),
                },
            ),
        }

    def _finalizer(self, state: InvestigationStateV1) -> dict[str, Any]:
        valid = bool(state.validation_results) and all(
            result.valid for result in state.validation_results
        )
        if valid:
            decision = FinalDecision.VALIDATED_RECOMMENDATIONS
        elif state.errors:
            decision = FinalDecision.SAFE_TERMINATION
        else:
            decision = FinalDecision.INSUFFICIENT_EVIDENCE
        return {
            "final_decision": decision,
            "audit_events": self._audit(
                state,
                "investigation_finalized",
                "finalizer",
                decision.value,
                {"recommendation_count": len(state.recommendation_candidates)},
            ),
        }

    def _call_tool(self, request: ToolRequest, sequence: int) -> ToolCallRecord:
        with operation_span(
            "process_optimizer.tool",
            {"tool_name": request.name, "tool_sequence": sequence},
        ) as span:
            record = self._run_tool(request, sequence)
            if record.status == "success":
                span.set(
                    {
                        "outcome": "success",
                        "returned_count": record.returned_count,
                        "truncated": record.truncated,
                    }
                )
            else:
                span.fail(record.error_code or record.status.upper())
            return record

    def _run_tool(self, request: ToolRequest, sequence: int) -> ToolCallRecord:
        started = time.perf_counter()
        input_model = _TOOL_INPUTS[request.name]
        call_id = _stable_id("tool_call", str(sequence), request.name, request.arguments)
        try:
            validated = input_model.model_validate(request.arguments)
        except ValidationError:
            record_operational_event(
                "agent_tool_invocation",
                result="invalid_arguments",
                attributes={
                    "tool_name": request.name,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            return ToolCallRecord(
                call_id=call_id,
                sequence=sequence,
                tool_name=request.name,
                validated_arguments={},
                status="invalid_arguments",
                error_code="ARGUMENT_VALIDATION_FAILED",
            )
        try:
            method: Callable[[Any], Any] = getattr(self._tools, request.name)
            result = method(validated)
        except (OSError, ValueError):
            record_operational_event(
                "agent_tool_invocation",
                result="error",
                attributes={
                    "tool_name": request.name,
                    "error_category": "AGGREGATE_TOOL_FAILED",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            return ToolCallRecord(
                call_id=call_id,
                sequence=sequence,
                tool_name=request.name,
                validated_arguments=validated.model_dump(mode="json"),
                status="tool_error",
                error_code="AGGREGATE_TOOL_FAILED",
            )
        record_operational_event(
            "agent_tool_invocation",
            result="success",
            attributes={
                "tool_name": request.name,
                "returned_count": result.returned_count,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        items = tuple(result.items)
        references = tuple(sorted(_collect_reference_ids(items)))
        return ToolCallRecord(
            call_id=call_id,
            sequence=sequence,
            tool_name=request.name,
            validated_arguments=validated.model_dump(mode="json"),
            status="success",
            returned_count=result.returned_count,
            truncated=result.truncated,
            result_items=items,
            evidence_reference_ids=references,
        )

    @staticmethod
    def _route_after_analyst(state: InvestigationStateV1) -> str:
        return "investigator" if state.selected_findings and not state.errors else "finalizer"

    @staticmethod
    def _route_after_investigator(state: InvestigationStateV1) -> str:
        if state.errors:
            return "finalizer"
        if state.pending_tool_calls:
            return "tool"
        if state.investigation_complete and state.tool_call_records:
            return "optimizer"
        return "finalizer"

    @staticmethod
    def _route_after_tool(state: InvestigationStateV1) -> str:
        if state.errors:
            return "finalizer"
        if state.pending_tool_calls:
            return "tool"
        if state.tool_return_target == "optimizer":
            return "optimizer"
        if state.iterations_used >= state.iteration_budget:
            return "finalizer"
        return "investigator"

    @staticmethod
    def _route_after_optimizer(state: InvestigationStateV1) -> str:
        if state.errors:
            return "finalizer"
        if state.pending_tool_calls:
            return "tool"
        if state.recommendation_candidates:
            return "validator"
        return "finalizer"

    def _provider_error_update(
        self, state: InvestigationStateV1, stage: str, error: Exception
    ) -> dict[str, Any]:
        if isinstance(error, ValidationError):
            code, diagnostics = "MODEL_OUTPUT_INVALID", {}
        else:
            code = getattr(error, "category", "INTERNAL_ERROR")
            diagnostics = dict(getattr(error, "diagnostics", {}) or {})
        if code not in _PROVIDER_FAILURE_MESSAGES:
            code = "INTERNAL_ERROR"
        retryable = code in RETRYABLE_CATEGORIES
        graph_error = GraphError(
            code=code,
            stage=stage,
            retryable=retryable,
            safe_message=_PROVIDER_FAILURE_MESSAGES[code],
        )
        details: dict[str, int | float | bool | str | None] = {
            "retryable": retryable,
            "error_category": code,
            **{key: diagnostics[key] for key in _PROVIDER_DIAGNOSTIC_KEYS if key in diagnostics},
            # Correlates with the platform's invocation/session logs; IDs only, no content.
            "agent_session_id": _safe_platform_id("FOUNDRY_AGENT_SESSION_ID"),
            "agent_version": _safe_platform_id("FOUNDRY_AGENT_VERSION"),
        }
        return {
            "errors": (*state.errors, graph_error),
            "audit_events": self._audit(state, "provider_failed", stage, graph_error.code, details),
        }

    def _model_step(
        self,
        state: InvestigationStateV1,
        stage: str,
        call: Callable[[], Any],
    ) -> tuple[Any | None, dict[str, Any]]:
        """Guard, make and account one model call.

        Returns ``(output, budget_update)``, or ``(None, full_update)`` when the call
        was blocked or failed. Calls and tokens are charged for failed attempts too.
        """
        blocked = self._model_call_guard(state, stage)
        if blocked is not None:
            return None, blocked
        failure: Exception | None = None
        output: Any = None
        with capture_model_usage() as usage:
            try:
                output = call()
            except (ModelTimeoutError, ModelProviderError, ValidationError) as error:
                failure = error
        # No model is called in mock mode, so nothing is charged; otherwise unknown
        # usage costs the full reservation (fail closed).
        charged = usage.charge(self._call_reservation) if self._provider.mode == "real_model" else 0
        spent = {
            "model_calls_used": state.model_calls_used + 1,
            "model_tokens_used": state.model_tokens_used + charged,
        }
        if failure is not None:
            return None, {**self._provider_error_update(state, stage, failure), **spent}
        return output, spent

    def _model_call_guard(
        self, state: InvestigationStateV1, stage: str
    ) -> dict[str, Any] | None:
        decision = self._guardrails.check_model_call(
            calls_used=state.model_calls_used,
            max_calls=self._max_model_calls,
            deadline_epoch_seconds=state.deadline_epoch_seconds,
            tokens_used=state.model_tokens_used,
            token_budget=self._run_token_budget,
        )
        if not decision.blocked:
            return None
        for code in (
            "RUN_DEADLINE_EXCEEDED",
            "MODEL_CALL_LIMIT_REACHED",
            "MODEL_TOKEN_BUDGET_EXHAUSTED",
        ):
            if code in decision.reason_codes:
                return self._limit_update(state, code, stage, decision)
        return self._guardrail_block(state, stage, decision)

    def _guardrail_block(
        self, state: InvestigationStateV1, stage: str, decision: GuardrailDecisionV1
    ) -> dict[str, Any]:
        code = (
            "RUN_DEADLINE_EXCEEDED"
            if decision.reason_codes == ("RUN_DEADLINE_EXCEEDED",)
            else "GUARDRAIL_BLOCKED"
        )
        error = GraphError(
            code=code,
            stage=stage,
            retryable=False,
            safe_message="A deterministic guardrail blocked this step.",
        )
        return {
            "pending_tool_calls": (),
            "errors": (*state.errors, error),
            "audit_events": self._audit(
                state, "guardrail_blocked", stage, "BLOCK", decision.audit_details()
            ),
        }

    def _limit_update(
        self,
        state: InvestigationStateV1,
        code: str,
        stage: str,
        decision: GuardrailDecisionV1 | None = None,
    ) -> dict[str, Any]:
        error = GraphError(
            code=code,
            stage=stage,
            retryable=False,
            safe_message="The configured investigation safety budget was exhausted.",
        )
        return {
            "pending_tool_calls": (),
            "errors": (*state.errors, error),
            "audit_events": self._audit(
                state,
                "budget_exhausted",
                stage,
                code,
                {"retryable": False, **(decision.audit_details() if decision else {})},
            ),
        }

    @staticmethod
    def _audit(
        state: InvestigationStateV1,
        event_type: str,
        stage: str,
        outcome: str,
        details: dict[str, int | float | bool | str | None],
    ) -> tuple[AuditEvent, ...]:
        return (
            *state.audit_events,
            AuditEvent(
                sequence=len(state.audit_events) + 1,
                event_type=event_type,
                stage=stage,
                outcome=outcome,
                safe_details=details,
            ),
        )


_PROVIDER_FAILURE_MESSAGES = {
    "PROVIDER_AUTH_FAILED": "The model provider rejected the agent's credentials.",
    "PROVIDER_RATE_LIMITED": "The model provider rate-limited the request.",
    "PROVIDER_BAD_REQUEST": "The model provider rejected the request as invalid.",
    "PROVIDER_UNAVAILABLE": "The model provider was unavailable.",
    "PROVIDER_TIMEOUT": "The configured model timed out.",
    "PROVIDER_CONNECTION_FAILED": "The model provider could not be reached.",
    "PROVIDER_CONFIGURATION_ERROR": "The model provider is not configured correctly.",
    "MODEL_OUTPUT_INVALID": "The configured model returned an invalid response.",
    "MODEL_CALL_LIMIT_REACHED": "The configured model-call limit was reached.",
    "GUARDRAIL_BLOCKED": "A guardrail blocked the model request before it was sent.",
    "INTERNAL_ERROR": "The model call failed for an unclassified reason.",
}
_PROVIDER_DIAGNOSTIC_KEYS = (
    "exception_type",
    "http_status",
    "provider_error_code",
    "provider_request_id",
    "provider_apim_request_id",
)
_PLATFORM_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def _safe_platform_id(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and _PLATFORM_ID.fullmatch(value) else None


def _reject_all(
    recommendations: tuple[Any, ...],
    results: tuple[Any, ...],
    decision: GuardrailDecisionV1,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Fold a non-approvable guardrail decision into the validation results."""
    rejected = tuple(
        item.model_copy(update={"status": RecommendationStatus.INSUFFICIENT_EVIDENCE})
        for item in recommendations
    )
    folded = tuple(
        result.model_copy(
            update={
                "valid": False,
                "error_codes": tuple(dict.fromkeys((*result.error_codes, *decision.reason_codes))),
            }
        )
        for result in results
    )
    return rejected, folded


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    if not value.isdigit() or int(value) < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _collect_reference_ids(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("reference_id")
        if isinstance(reference, str) and reference.startswith("evidence_ref_"):
            references.add(reference)
        for nested in value.values():
            references.update(_collect_reference_ids(nested))
    elif isinstance(value, list | tuple):
        for nested in value:
            references.update(_collect_reference_ids(nested))
    return references


def _stable_id(prefix: str, *values: Any) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"
