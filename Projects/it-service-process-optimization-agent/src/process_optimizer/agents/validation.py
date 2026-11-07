"""Deterministic evidence and claim validation for agent recommendations."""

from __future__ import annotations

import math
import re
from typing import Any

from process_optimizer.agents.numerical_claims import (
    SupportedValues,
    build_supported_values,
    extract_numerical_claims,
    supported_values_for_fact,
)
from process_optimizer.agents.schemas import (
    EvidenceValidationResult,
    RecommendationCandidate,
    RecommendationStatus,
    ToolCallRecord,
)
from process_optimizer.guardrails.policy import GuardrailPolicyV1, load_policy
from process_optimizer.observability import operation_span
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1

_CAUSAL_PATTERNS = (
    re.compile(r"\bcaused\b", re.IGNORECASE),
    re.compile(r"\bcauses\b", re.IGNORECASE),
    re.compile(r"\bbecause of\b", re.IGNORECASE),
    re.compile(r"\bresulted in\b", re.IGNORECASE),
    re.compile(r"\bproves? that\b", re.IGNORECASE),
)
_SEMANTIC_TRAP = re.compile(
    r"completed(?:-to-|\s*→\s*)completed.{0,100}(?:is|represents|confirms).{0,40}rework",
    re.IGNORECASE | re.DOTALL,
)
_FREE_TEXT_FIELDS = (
    "problem_statement",
    "suggested_process_improvement",
    "expected_outcome_with_uncertainty",
    "proposed_follow_up_kpi",
    "implementation_risk",
)
_FREE_TEXT_LIST_FIELDS = ("hypotheses", "data_limitations")


def validate_recommendations(
    recommendations: tuple[RecommendationCandidate, ...],
    evidence: EvidenceContractV1,
    tool_records: tuple[ToolCallRecord, ...] = (),
    policy: GuardrailPolicyV1 | None = None,
) -> tuple[tuple[RecommendationCandidate, ...], tuple[EvidenceValidationResult, ...]]:
    """Validate IDs, structured and free-text numeric claims, semantics, and causality.

    ``policy`` should be the guardrail policy that will decide on the result, so that
    both checks run under one policy version; it defaults to the packaged policy.
    """
    with operation_span(
        "process_optimizer.validation",
        {"analysis_run_id": evidence.analysis_run_id, "recommendation_count": len(recommendations)},
    ) as span:
        validated, results = _validate(
            recommendations, evidence, tool_records, policy or load_policy()
        )
        error_codes = sorted({code for result in results for code in result.error_codes})
        span.set(
            {
                "valid_count": sum(result.valid for result in results),
                "error_codes": tuple(error_codes),
                "outcome": "success" if results and not error_codes else "rejected",
            }
        )
        return validated, results


def _validate(
    recommendations: tuple[RecommendationCandidate, ...],
    evidence: EvidenceContractV1,
    tool_records: tuple[ToolCallRecord, ...],
    policy: GuardrailPolicyV1,
) -> tuple[tuple[RecommendationCandidate, ...], tuple[EvidenceValidationResult, ...]]:
    evidence_by_id = {
        finding.finding_id: finding.model_dump(mode="json") for finding in evidence.findings
    }
    simulations = _simulation_results(tool_records)
    validated_recommendations = []
    results = []
    for recommendation in recommendations:
        errors: list[str] = []
        notes: list[str] = []
        valid_ids = tuple(
            evidence_id
            for evidence_id in recommendation.evidence_ids
            if evidence_id in evidence_by_id
        )
        if len(valid_ids) != len(recommendation.evidence_ids):
            errors.append("INVALID_EVIDENCE_ID")
        if not recommendation.evidence_ids:
            errors.append("MISSING_EVIDENCE")

        for fact in recommendation.observed_numerical_facts:
            finding = evidence_by_id.get(fact.evidence_id)
            if finding is None or fact.evidence_id not in recommendation.evidence_ids:
                errors.append("INVALID_NUMERICAL_CITATION")
                continue
            expected = _resolve_path(finding, fact.field_path)
            if not _numbers_match(expected, fact.value):
                errors.append("FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM")

        cited_simulation = (
            simulations.get(recommendation.simulation.simulation_id)
            if recommendation.simulation is not None
            else None
        )
        supported = build_supported_values(
            (evidence_by_id[evidence_id] for evidence_id in valid_ids),
            (cited_simulation,) if cited_simulation is not None else (),
        )
        text_errors, text_notes = _check_free_text_numbers(recommendation, supported)
        errors.extend(text_errors)
        notes.extend(text_notes)

        prose = " ".join(
            (
                recommendation.problem_statement,
                recommendation.expected_outcome_with_uncertainty,
                recommendation.suggested_process_improvement,
                *recommendation.hypotheses,
            )
        )
        if any(pattern.search(prose) for pattern in _CAUSAL_PATTERNS):
            errors.append("UNSUPPORTED_CAUSAL_STATEMENT")
        errors.extend(_claim_evidence_errors(recommendation, valid_ids, evidence_by_id, policy))
        if _SEMANTIC_TRAP.search(prose):
            errors.append("COMPLETED_LIFECYCLE_SEMANTIC_TRAP")
        if not recommendation.requires_human_approval:
            errors.append("HUMAN_APPROVAL_NOT_REQUIRED")
        if not recommendation.data_limitations:
            errors.append("MISSING_DATA_LIMITATIONS")
        handoff_evidence = any(
            evidence_by_id.get(evidence_id, {}).get("metric", {}).get("name")
            == "high_frequency_or_repeated_group_handoff"
            for evidence_id in recommendation.evidence_ids
        )
        if handoff_evidence and recommendation.simulation is None:
            errors.append("MISSING_SIMULATION_CITATION")
        if recommendation.simulation is not None:
            if recommendation.simulation.status == "NOT_ESTIMABLE":
                errors.append("SIMULATION_NOT_ESTIMABLE")
            result = simulations.get(recommendation.simulation.simulation_id)
            if result is None:
                errors.append("INVALID_SIMULATION_CITATION")
            elif not _simulation_matches(recommendation, result):
                errors.append("SIMULATION_ASSUMPTION_MISMATCH")

        unique_errors = tuple(dict.fromkeys(errors))
        is_valid = not unique_errors
        notes.append(
            "Numerical claims were checked against exact Evidence Contract V1 field paths."
        )
        notes.append(
            "Free-text numbers were cross-checked against cited evidence and derived values."
        )
        notes.append("Validation is deterministic and does not establish causality.")
        status = (
            RecommendationStatus.VALIDATED
            if is_valid
            else RecommendationStatus.INSUFFICIENT_EVIDENCE
        )
        validated_recommendations.append(recommendation.model_copy(update={"status": status}))
        results.append(
            EvidenceValidationResult(
                recommendation_id=recommendation.recommendation_id,
                valid=is_valid,
                validated_evidence_ids=valid_ids,
                error_codes=unique_errors,
                notes=tuple(notes),
            )
        )
    return tuple(validated_recommendations), tuple(results)


def _claim_evidence_errors(
    recommendation: RecommendationCandidate,
    valid_ids: tuple[str, ...],
    evidence_by_id: dict[str, dict[str, Any]],
    policy: GuardrailPolicyV1,
) -> list[str]:
    """CA-07: a claim needs a cited finding whose interpretation can support it.

    Matching numbers do not help: a cohort statistic restated as an effect still needs
    intervention-effect evidence, which Evidence Contract V1 cannot provide.
    """
    interpretations = {evidence_by_id[item].get("interpretation") for item in valid_ids}
    errors = []
    if interpretations - set(policy.claim_evidence.known_evidence_interpretations):
        errors.append("UNKNOWN_EVIDENCE_TYPE")
    rules = policy.claim_rules
    texts = {field: getattr(recommendation, field) for field in _FREE_TEXT_FIELDS}
    for claim in rules.matched_claim_types(texts, recommendation.hypotheses):
        if not claim.required & interpretations:
            errors.append(claim.reason_code)
    return errors


def _check_free_text_numbers(
    recommendation: RecommendationCandidate,
    supported: SupportedValues,
) -> tuple[list[str], list[str]]:
    """Fail closed on material free-text numbers that cited evidence does not support."""
    texts: list[tuple[str, str]] = [
        (field, getattr(recommendation, field)) for field in _FREE_TEXT_FIELDS
    ]
    for field in _FREE_TEXT_LIST_FIELDS:
        texts.extend(
            (f"{field}[{index}]", text) for index, text in enumerate(getattr(recommendation, field))
        )
    texts.extend(
        (f"observed_numerical_facts[{index}].statement", fact.statement)
        for index, fact in enumerate(recommendation.observed_numerical_facts)
    )
    errors: list[str] = []
    notes: list[str] = []
    for location, text in texts:
        for claim in extract_numerical_claims(text):
            if claim.ambiguous:
                errors.append("AMBIGUOUS_NUMERICAL_CLAIM_IN_TEXT")
                notes.append(f"Ambiguous numerical expression in {location}: '{claim.text}'.")
            elif claim.material and not supported.supports(claim):
                errors.append("UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT")
                notes.append(f"Unsupported numerical claim in {location}: '{claim.text}'.")
    for index, fact in enumerate(recommendation.observed_numerical_facts):
        own_value = supported_values_for_fact(fact.field_path, fact.value)
        material = [
            claim
            for claim in extract_numerical_claims(fact.statement)
            if claim.material and not claim.ambiguous
        ]
        if material and not any(own_value.supports(claim) for claim in material):
            errors.append("NUMERICAL_FACT_STATEMENT_MISMATCH")
            notes.append(
                f"observed_numerical_facts[{index}].statement does not state its own value."
            )
    return errors, notes


def _resolve_path(payload: dict[str, Any], field_path: str) -> Any:
    current: Any = payload
    for segment in field_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _numbers_match(expected: Any, claimed: int | float) -> bool:
    if not isinstance(expected, int | float) or isinstance(expected, bool):
        return False
    return math.isclose(float(expected), float(claimed), rel_tol=1e-9, abs_tol=1e-9)


def _simulation_results(tool_records: tuple[ToolCallRecord, ...]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for record in tool_records:
        if record.tool_name != "simulate_group_transition_reduction" or record.status != "success":
            continue
        for item in record.result_items:
            simulation_id = item.get("simulation_id")
            if isinstance(simulation_id, str):
                results[simulation_id] = item
    return results


def _simulation_matches(
    recommendation: RecommendationCandidate,
    result: dict[str, Any],
) -> bool:
    citation = recommendation.simulation
    if citation is None:
        return False
    if result.get("classification") != citation.classification:
        return False
    if result.get("status") != citation.status:
        return False
    simulation_evidence_ids = {
        reference.get("finding_id")
        for reference in result.get("evidence_references", [])
        if reference.get("finding_id") is not None
    }
    if not simulation_evidence_ids.issubset(set(recommendation.evidence_ids)):
        return False
    expected = {
        scenario.get("name"): (
            scenario.get("eligible_event_share_assumption"),
            scenario.get("reduction_share_assumption"),
            scenario.get("hypothetical_repeated_visit_events_avoided", {}).get("value"),
        )
        for scenario in result.get("scenarios", [])
    }
    actual = {
        assumption.name: (
            assumption.eligible_event_share_assumption,
            assumption.reduction_share_assumption,
            assumption.hypothetical_repeated_visit_events_avoided,
        )
        for assumption in citation.assumptions
    }
    return expected == actual
