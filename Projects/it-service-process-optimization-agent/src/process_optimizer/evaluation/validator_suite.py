"""Deterministic, model-free evaluation of the recommendation validator and schema gates.

Golden recommendations come from the deterministic mock workflow over the real aggregate
Evidence Contract. Each negative case applies one targeted corruption and states the
validator error code (or schema rejection) it must produce. No case calls a model, and the
results measure the deterministic safety net, not model quality.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from process_optimizer.agents.provider import DeterministicMockProvider
from process_optimizer.agents.schemas import (
    InvestigationRequestV1,
    InvestigationResultV1,
    RecommendationCandidate,
    ToolRequest,
)
from process_optimizer.agents.validation import validate_recommendations
from process_optimizer.agents.workflow import InvestigationRunner

SCHEMA_REJECTED = "SCHEMA_REJECTED"
_UNKNOWN_FINDING_ID = "finding_000000000000000000000000"
_UNKNOWN_SIMULATION_ID = "simulation_000000000000000000000000"


class ValidatorCaseResultV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    category: Literal["positive", "negative"]
    description: str
    expected_valid: bool
    expected_error_codes: tuple[str, ...]
    actual_valid: bool
    actual_error_codes: tuple[str, ...]
    passed: bool


class ValidatorEvaluationReportV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    suite_version: Literal["validator-suite-v1"] = "validator-suite-v1"
    execution_label: Literal["DETERMINISTIC_VALIDATOR_NO_MODEL"] = (
        "DETERMINISTIC_VALIDATOR_NO_MODEL"
    )
    analysis_run_id: str
    case_results: tuple[ValidatorCaseResultV1, ...]
    passed_case_count: int = Field(ge=0)
    total_case_count: int = Field(ge=0)
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class _Golden:
    result: InvestigationResultV1

    @property
    def recommendation(self) -> RecommendationCandidate:
        return self.result.recommendations[0]


@dataclass(frozen=True)
class _Case:
    case_id: str
    category: Literal["positive", "negative"]
    description: str
    scenario: Literal["group_returns", "completed_lifecycle"]
    mutate: Callable[[dict[str, Any], _Golden], dict[str, Any]]
    expected_error_codes: tuple[str, ...] = ()


def _same(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    return payload


def _fabricate_metric(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    fact = payload["observed_numerical_facts"][0]
    fact["value"] = fact["value"] * 1.5 + 1
    return payload


def _unknown_evidence_id(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["evidence_ids"] = [*payload["evidence_ids"], _UNKNOWN_FINDING_ID]
    return payload


def _uncited_numerical_evidence(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["observed_numerical_facts"][0]["evidence_id"] = _UNKNOWN_FINDING_ID
    return payload


def _invalid_field_path(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["observed_numerical_facts"][0]["field_path"] = "measured_statistics.does_not_exist"
    return payload


def _causal_problem_statement(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["problem_statement"] = (
        "Repeated group visits caused the longer observed case spans in this process."
    )
    return payload


def _causal_hypothesis(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["hypotheses"] = [
        "The routing design resulted in every additional day of observed case span."
    ]
    return payload


def _rework_claim(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["problem_statement"] = (
        "The Completed-to-Completed transition represents confirmed rework by support groups."
    )
    return payload


def _no_limitations(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["data_limitations"] = []
    return payload


def _no_evidence(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["evidence_ids"] = []
    payload["observed_numerical_facts"] = []
    return payload


def _drop_simulation(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["simulation"] = None
    return payload


def _unknown_simulation(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["simulation"]["simulation_id"] = _UNKNOWN_SIMULATION_ID
    return payload


def _tampered_simulation(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    assumption = payload["simulation"]["assumptions"][0]
    assumption["hypothetical_repeated_visit_events_avoided"] *= 10
    return payload


def _span_medians(golden: _Golden) -> tuple[float, float]:
    values = {
        fact.field_path: float(fact.value)
        for fact in golden.recommendation.observed_numerical_facts
    }
    return (
        values["measured_statistics.with_return_span_median_seconds"],
        values["measured_statistics.without_return_span_median_seconds"],
    )


def _text_evidence_backed_numbers(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    with_return, without_return = _span_medians(golden)
    payload["problem_statement"] += (
        f" The median observed span is {with_return:,.1f} seconds (about "
        f"{with_return / 86400:.1f} days) versus {without_return:,.1f} seconds."
    )
    return payload


def _text_derived_percentage(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    with_return, without_return = _span_medians(golden)
    ratio = with_return / without_return
    payload["problem_statement"] += (
        f" The median is approximately {(ratio - 1) * 100:.0f}% longer, a ratio of "
        f"about {ratio:.2f}x."
    )
    return payload


def _text_non_material_numbers(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["suggested_process_improvement"] += (
        " Start on 2026-11-02 with a 4-week pilot in Phase 2, compare 2 cohorts at p90, "
        "and review the results within 30 days under release 1.1.0."
    )
    return payload


def _text_incorrect_percentage(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["problem_statement"] += " The median observed span is about 85% longer."
    return payload


def _text_contradictory_number(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    with_return, _ = _span_medians(golden)
    payload["problem_statement"] += (
        f" The group-return median observed span is {with_return * 0.8:,.1f} seconds."
    )
    return payload


def _fact_statement_contradiction(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    _, without_return = _span_medians(golden)
    for fact in payload["observed_numerical_facts"]:
        if fact["field_path"] == "measured_statistics.with_return_span_median_seconds":
            fact["statement"] = f"The group-return median is {without_return:,.1f} seconds."
    return payload


def _text_fabricated_improvement(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["expected_outcome_with_uncertainty"] = (
        "The pilot is expected to cut the median observed span by 35% and halve returns "
        "within one quarter."
    )
    return payload


def _text_ambiguous_number(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["problem_statement"] += " Returning incidents remain open about 13,5 days."
    return payload


def _text_number_without_evidence(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["evidence_ids"] = []
    payload["observed_numerical_facts"] = []
    payload["problem_statement"] += " The median observed span is approximately 56% longer."
    return payload


def _automation_without_approval(payload: dict[str, Any], golden: _Golden) -> dict[str, Any]:
    payload["requires_human_approval"] = False
    payload["suggested_process_improvement"] = "Automatically reroute incidents immediately."
    return payload


_CASES: tuple[_Case, ...] = (
    _Case("validator_case_golden_group_returns", "positive",
          "Mock-workflow group-return recommendation over real aggregates",
          "group_returns", _same),
    _Case("validator_case_golden_completed_lifecycle", "positive",
          "Mock-workflow lifecycle recommendation over real aggregates",
          "completed_lifecycle", _same),
    _Case("validator_case_fabricated_metric", "negative",
          "A cited statistic is replaced by a fabricated value",
          "group_returns", _fabricate_metric, ("FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM",)),
    _Case("validator_case_unknown_evidence_id", "negative",
          "The recommendation cites a finding ID absent from the Evidence Contract",
          "group_returns", _unknown_evidence_id, ("INVALID_EVIDENCE_ID",)),
    _Case("validator_case_numerical_fact_wrong_evidence", "negative",
          "A numerical fact cites evidence the recommendation does not reference",
          "group_returns", _uncited_numerical_evidence, ("INVALID_NUMERICAL_CITATION",)),
    _Case("validator_case_invalid_field_path", "negative",
          "A numerical fact points at a field path that does not exist",
          "group_returns", _invalid_field_path, ("FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM",)),
    _Case("validator_case_causal_problem_statement", "negative",
          "The problem statement asserts causation (\"caused\")",
          "group_returns", _causal_problem_statement, ("UNSUPPORTED_CAUSAL_STATEMENT",)),
    _Case("validator_case_causal_hypothesis", "negative",
          "A hypothesis asserts causation (\"resulted in\")",
          "group_returns", _causal_hypothesis, ("UNSUPPORTED_CAUSAL_STATEMENT",)),
    _Case("validator_case_lifecycle_rework_claim", "negative",
          "Completed-to-Completed is presented as confirmed rework",
          "completed_lifecycle", _rework_claim, ("COMPLETED_LIFECYCLE_SEMANTIC_TRAP",)),
    _Case("validator_case_missing_limitations", "negative",
          "Data limitations are removed",
          "group_returns", _no_limitations, ("MISSING_DATA_LIMITATIONS",)),
    _Case("validator_case_missing_evidence", "negative",
          "All evidence references and numerical facts are removed",
          "completed_lifecycle", _no_evidence, ("MISSING_EVIDENCE",)),
    _Case("validator_case_handoff_without_simulation", "negative",
          "A handoff intervention is proposed without a simulation citation",
          "group_returns", _drop_simulation, ("MISSING_SIMULATION_CITATION",)),
    _Case("validator_case_unknown_simulation", "negative",
          "The simulation citation references a simulation that was never run",
          "group_returns", _unknown_simulation, ("INVALID_SIMULATION_CITATION",)),
    _Case("validator_case_tampered_simulation", "negative",
          "A simulated value is inflated tenfold",
          "group_returns", _tampered_simulation, ("SIMULATION_ASSUMPTION_MISMATCH",)),
    _Case("validator_case_text_evidence_backed_numbers", "positive",
          "Free text restates cited medians, including a seconds-to-days conversion",
          "group_returns", _text_evidence_backed_numbers),
    _Case("validator_case_text_derived_percentage", "positive",
          "Free text states the percentage difference and ratio derived from cited medians",
          "group_returns", _text_derived_percentage),
    _Case("validator_case_text_non_material_numbers", "positive",
          "Dates, phases, percentile labels, versions and planning durations are not claims",
          "group_returns", _text_non_material_numbers),
    _Case("validator_case_text_incorrect_percentage", "negative",
          "Free text overstates the percentage difference between cited medians",
          "group_returns", _text_incorrect_percentage, ("UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT",)),
    _Case("validator_case_text_contradictory_number", "negative",
          "Free text states a median that contradicts the cited evidence",
          "group_returns", _text_contradictory_number,
          ("UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT",)),
    _Case("validator_case_fact_statement_contradiction", "negative",
          "A numerical fact's statement quotes a different value than its structured value",
          "group_returns", _fact_statement_contradiction,
          ("NUMERICAL_FACT_STATEMENT_MISMATCH",)),
    _Case("validator_case_text_fabricated_improvement", "negative",
          "Expected outcome promises an improvement no evidence or simulation supports",
          "group_returns", _text_fabricated_improvement,
          ("UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT",)),
    _Case("validator_case_text_ambiguous_number", "negative",
          "A decimal-comma number cannot be interpreted deterministically",
          "group_returns", _text_ambiguous_number, ("AMBIGUOUS_NUMERICAL_CLAIM_IN_TEXT",)),
    _Case("validator_case_text_number_without_evidence", "negative",
          "Free text states a percentage while no evidence is cited",
          "completed_lifecycle", _text_number_without_evidence,
          ("MISSING_EVIDENCE", "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT")),
    _Case("validator_case_automation_without_approval", "negative",
          "Unsafe automation: the recommendation opts out of human approval",
          "group_returns", _automation_without_approval, (SCHEMA_REJECTED,)),
)



def run_validator_evaluation(analysis_dir: Path) -> ValidatorEvaluationReportV1:
    """Run every positive and negative validator case without any model call."""
    runner = InvestigationRunner(analysis_dir, DeterministicMockProvider())
    goldens = {
        scenario: _Golden(_golden_result(runner, scenario))
        for scenario in ("group_returns", "completed_lifecycle")
    }
    results = [_run_case(case, goldens[case.scenario], runner) for case in _CASES]
    results.append(_run_write_tool_case())
    return ValidatorEvaluationReportV1(
        analysis_run_id=runner.analysis_run_id,
        case_results=tuple(results),
        passed_case_count=sum(result.passed for result in results),
        total_case_count=len(results),
        limitations=(
            "Cases test the deterministic validator and schema gates, not model quality.",
            "Negative cases are single targeted corruptions; combined attacks are not covered.",
            "Causal-language detection is pattern-based and can miss paraphrased claims.",
            "Free-text numbers are verified by value, unit dimension and stated precision; a "
            "supported number attached to the wrong subject is not detected.",
        ),
    )


def _golden_result(runner: InvestigationRunner, scenario: str) -> InvestigationResultV1:
    result = runner.run(
        InvestigationRequestV1(
            analysis_run_id=runner.analysis_run_id,
            investigation_goal=f"Validator-suite golden run for scenario {scenario}.",
            execution_mode="mocked_llm",
            scenario=scenario,
        )
    )
    if not result.recommendations:
        raise RuntimeError(f"Golden run for {scenario} produced no recommendation")
    return result


def _run_case(case: _Case, golden: _Golden, runner: InvestigationRunner) -> ValidatorCaseResultV1:
    payload = golden.recommendation.model_dump(mode="json")
    payload["status"] = "DRAFT"
    mutated = case.mutate(payload, golden)
    try:
        candidate = RecommendationCandidate.model_validate(mutated)
    except ValidationError:
        actual_valid, actual_codes = False, (SCHEMA_REJECTED,)
    else:
        _, validation = validate_recommendations(
            (candidate,), runner._evidence, golden.result.tool_call_records
        )
        actual_valid, actual_codes = validation[0].valid, validation[0].error_codes
    return _result(
        case.case_id,
        case.category,
        case.description,
        case.expected_error_codes,
        actual_valid,
        actual_codes,
    )


def _run_write_tool_case() -> ValidatorCaseResultV1:
    try:
        ToolRequest.model_validate({"name": "update_incident_routing", "arguments": {}})
    except ValidationError:
        actual_valid, actual_codes = False, (SCHEMA_REJECTED,)
    else:
        actual_valid, actual_codes = True, ()
    return _result(
        "validator_case_write_tool_request",
        "negative",
        "Unsafe automation: the model requests a non-existent write tool",
        (SCHEMA_REJECTED,),
        actual_valid,
        actual_codes,
    )


def _result(
    case_id: str,
    category: Literal["positive", "negative"],
    description: str,
    expected_codes: tuple[str, ...],
    actual_valid: bool,
    actual_codes: tuple[str, ...],
) -> ValidatorCaseResultV1:
    expected_valid = not expected_codes
    passed = actual_valid == expected_valid and set(expected_codes).issubset(actual_codes)
    return ValidatorCaseResultV1(
        case_id=case_id,
        category=category,
        description=description,
        expected_valid=expected_valid,
        expected_error_codes=expected_codes,
        actual_valid=actual_valid,
        actual_error_codes=tuple(actual_codes),
        passed=passed,
    )
