"""Deterministic-by-default evaluation runner for the Phase 5 workflow."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from process_optimizer.agents.provider import DeterministicMockProvider, FoundryModelProvider
from process_optimizer.agents.schemas import InvestigationRequestV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.evaluation.dataset import evaluation_cases
from process_optimizer.evaluation.schemas import (
    EvaluationCaseResultV1,
    EvaluationReportV1,
    ModelInvocationRecordV1,
)
from process_optimizer.evaluation.validator_suite import run_validator_evaluation
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR


class AgentEvaluationRunner:
    """Run aggregate-only workflow evaluations and record reproducible outcomes."""

    def __init__(self, analysis_dir: Path, provider: Any) -> None:
        self._provider = provider
        self._runner = InvestigationRunner(analysis_dir, provider)

    def run(self) -> EvaluationReportV1:
        case_results = tuple(self._run_case(case) for case in evaluation_cases())
        invocation_records = tuple(
            ModelInvocationRecordV1.model_validate(record)
            for record in getattr(self._provider, "invocation_records", [])
        )
        token_total = _sum_tokens(invocation_records)
        real_mode = self._provider.mode == "real_model"
        return EvaluationReportV1(
            execution_mode=self._provider.mode,
            execution_label="REAL_MODEL_OUTPUT" if real_mode else "DETERMINISTIC_MOCK_NOT_REAL_AI",
            model_deployment=(
                os.environ.get("FOUNDRY_MODEL_NAME", "unconfigured")
                if real_mode
                else "deterministic-mock-provider"
            ),
            analysis_run_id=self._runner.analysis_run_id,
            case_results=case_results,
            invocation_records=invocation_records,
            passed_case_count=sum(result.passed for result in case_results),
            total_case_count=len(case_results),
            token_usage_total=token_total,
            estimated_cost_usd=None,
            pricing_assumption=None,
            limitations=(
                "The aggregate evidence dataset is not a model-quality benchmark.",
                "Mock results test orchestration determinism, not real-model reasoning quality.",
                "No cost is estimated without an explicitly documented pricing assumption.",
            ),
        )

    def _run_case(self, case: Any) -> EvaluationCaseResultV1:
        started = time.perf_counter()
        request = InvestigationRequestV1(
            analysis_run_id=self._runner.analysis_run_id,
            investigation_goal=case.goal,
            execution_mode=self._provider.mode,
            scenario=case.scenario,
        )
        result = self._runner.run(request)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        tool_names = tuple(record.tool_name for record in result.tool_call_records)
        serialized = result.model_dump_json().lower()
        validation = tuple(item.valid for item in result.validation_results)
        simulation_ids = tuple(
            recommendation.simulation.simulation_id
            for recommendation in result.recommendations
            if recommendation.simulation is not None
        )
        tool_correct = tool_names == case.expected_tools
        grounded = bool(validation) and all(validation)
        numerical = not any(
            "NUMERICAL" in code
            for item in result.validation_results
            for code in item.error_codes
        )
        semantic_safe = not any(term in serialized for term in case.prohibited_interpretations)
        no_action = all("write" not in name and "update" not in name for name in tool_names)
        passed = all(
            (
                tool_correct,
                grounded,
                numerical,
                semantic_safe,
                no_action,
                result.final_decision == case.expected_final_decision,
            )
        )
        return EvaluationCaseResultV1(
            case_id=case.case_id,
            scenario=case.scenario,
            passed=passed,
            tool_call_correctness=tool_correct,
            recommendation_groundedness=grounded,
            numerical_consistency=numerical,
            semantic_safety=semantic_safe,
            no_business_action_execution=no_action,
            actual_tool_calls=tool_names,
            validation_outcomes=validation,
            simulation_ids=simulation_ids,
            final_decision=result.final_decision,
            latency_ms=latency_ms,
            error_categories=tuple(error.code for error in result.errors),
        )


def _sum_tokens(records: tuple[ModelInvocationRecordV1, ...]) -> dict[str, int] | None:
    usages = [record.token_usage for record in records if record.token_usage]
    if not usages:
        return None
    totals: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Phase 5 agent evaluation suite.")
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument(
        "--suite",
        choices=("agent", "validator"),
        default="agent",
        help="agent: workflow evaluation (mock or real model); validator: model-free cases.",
    )
    parser.add_argument("--mode", choices=("mocked_llm", "real_model"), default="mocked_llm")
    parser.add_argument("--allow-paid-model-call", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.suite == "validator":
        validator_report = run_validator_evaluation(arguments.analysis_dir)
        print(validator_report.model_dump_json(indent=2))
        return 0 if validator_report.passed_case_count == validator_report.total_case_count else 1
    if arguments.mode == "real_model" and not arguments.allow_paid_model_call:
        print(
            "Real-model evaluation requires explicit paid-call approval; no call was made.",
            file=sys.stderr,
        )
        return 2
    provider = (
        FoundryModelProvider()
        if arguments.mode == "real_model"
        else DeterministicMockProvider()
    )
    report = AgentEvaluationRunner(arguments.analysis_dir, provider).run()
    print(report.model_dump_json(indent=2))
    return 0 if report.passed_case_count == report.total_case_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
