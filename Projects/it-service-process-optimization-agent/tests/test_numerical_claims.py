"""Deterministic free-text numerical-claim validation; synthetic aggregates only, no model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from process_optimizer.agents.numerical_claims import (
    build_supported_values,
    extract_numerical_claims,
)
from process_optimizer.agents.schemas import (
    RecommendationCandidate,
    SimulationCitationV1,
    ToolCallRecord,
)
from process_optimizer.agents.validation import validate_recommendations
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
# Synthetic aggregate values shared by these fixtures (not values of any real dataset).
GROUP_FINDING = "finding_0123456789abcdef0000a001"
LIFECYCLE_FINDING = "finding_222222222222222222222222"
SIMULATION_ID = "simulation_cccccccccccccccccccccccc"
MODEL_TEXT_FIXTURE = Path("tests/data") / "model_text_regression_recommendation.json"
SPAN_FIELD = "measured_statistics.with_return_span_median_seconds"

GROUP_STATISTICS = {
    "with_return_incident_count": 1500,
    "without_return_incident_count": 6000,
    "with_return_span_median_seconds": 1209600.5,
    "without_return_span_median_seconds": 777600.0,
    "span_median_ratio": 1.5555561985596709,
}


@pytest.fixture
def contract() -> EvidenceContractV1:
    source = DatasetSourceV1(
        name="Synthetic aggregate unit-test fixture",
        source_url="https://example.invalid/synthetic",
        sha256="a" * 64,
    )
    common: dict[str, Any] = {
        "source_dataset": source,
        "analysis_run_id": RUN_ID,
        "analysis_configuration_version": "test-v1",
        "data_limitations": ("Synthetic aggregate fixture for tests only.",),
        "evidence_references": (
            AggregateEvidenceReferenceV1(
                reference_id="evidence_ref_" + "1" * 24,
                artifact="synthetic.json",
                artifact_sha256="1" * 64,
                selector={"fixture": "synthetic"},
                metric="synthetic aggregate",
            ),
        ),
    }
    group = EvidenceFindingV1(
        finding_id=GROUP_FINDING,
        metric=MetricDefinitionV1(
            name="observed_span_association_with_group_returns",
            definition="Synthetic aggregate comparison.",
            unit="incidents_and_seconds",
        ),
        aggregate_population=AggregatePopulationV1(label="synthetic cohorts"),
        counts=EvidenceCountsV1(
            count_level="incident", observation_count=1500, numerator=1500, denominator=7500
        ),
        measured_statistics=GROUP_STATISTICS,
        baseline_or_comparison=ComparisonGroupV1(
            label="without returns", statistics={"incident_count": 6000}
        ),
        sufficiency_status="sufficient",
        **common,
    )
    lifecycle = EvidenceFindingV1(
        finding_id=LIFECYCLE_FINDING,
        metric=MetricDefinitionV1(
            name="lifecycle_decomposition_of_activity_transition",
            definition="Synthetic aggregate lifecycle decomposition.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(label="synthetic transitions"),
        counts=EvidenceCountsV1(
            count_level="event_transition",
            observation_count=5800,
            numerator=5760,
            denominator=5800,
        ),
        measured_statistics={"same_group_event_count": 5790},
        sufficiency_status="context_only",
        **common,
    )
    return EvidenceContractV1(
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        source_dataset=source,
        findings=(group, lifecycle),
    )


def _recommendation(**overrides: Any) -> RecommendationCandidate:
    payload: dict[str, Any] = {
        "recommendation_id": "recommendation_" + "b" * 24,
        "problem_statement": (
            "Incidents with repeated group visits have a longer observed span; the "
            "association is unadjusted and does not establish a routing cause."
        ),
        "evidence_ids": [GROUP_FINDING],
        "observed_numerical_facts": [
            {
                "statement": "The group-return median observed span is 1,209,600.5 seconds.",
                "evidence_id": GROUP_FINDING,
                "field_path": SPAN_FIELD,
                "value": 1209600.5,
                "unit": "seconds",
            }
        ],
        "hypotheses": ["Some returns may reflect valid specialist collaboration."],
        "data_limitations": ["Cohorts are observational and unadjusted."],
        "suggested_process_improvement": "Run a human-led stratified routing review.",
        "expected_outcome_with_uncertainty": "No reduction in elapsed time is promised.",
        "proposed_follow_up_kpi": "Track observed-span median/p90 in matched cohorts.",
        "implementation_risk": "Routing changes require authenticated human approval.",
    }
    payload.update(overrides)
    return RecommendationCandidate.model_validate(payload)


def _validate(
    contract: EvidenceContractV1,
    recommendation: RecommendationCandidate,
    tool_records: tuple[ToolCallRecord, ...] = (),
) -> tuple[RecommendationCandidate, tuple[str, ...], tuple[str, ...]]:
    validated, results = validate_recommendations((recommendation,), contract, tool_records)
    return validated[0], results[0].error_codes, results[0].notes


def _supported() -> Any:
    return build_supported_values(
        [
            {
                "counts": {"observation_count": 1500, "numerator": 1500, "denominator": 7500},
                "measured_statistics": GROUP_STATISTICS,
            }
        ]
    )


# --- Extraction: legitimate non-claims ------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Review on 2026-10-18 at 09:30 and again on 18.10.2026.",
        "The BPI Challenge 2013 dataset is the source.",
        "Deployed with gpt-4.1-mini under release 1.1.0 and schema v1.",
        "See finding_0123456789abcdef0000a001 and recommendation_0123456789abcdef0000a001.",
        "Report median, p75 and p90 per cohort.",
        "The 3rd group and the 2nd handoff are reviewed in Phase 2, step 4.",
        "Compare 2 cohorts across 3 support tiers.",
        "Run a 4-week pilot, review within 30 days and again 2 weeks later.",
        "Hold the review 3 times per month.",
    ],
)
def test_dates_identifiers_versions_and_ordinals_are_not_material(text: str) -> None:
    assert not [claim for claim in extract_numerical_claims(text) if claim.material]


def test_claim_kinds_units_and_hedges_are_extracted() -> None:
    claims = extract_numerical_claims(
        "About 56% longer, 1.6x the baseline, 14.0 days, 1,500 incidents, 1.2 million seconds."
    )

    assert [(claim.text, claim.kind) for claim in claims] == [
        ("56%", "percent"),
        ("1.6x", "multiplier"),
        ("14.0 days", "time"),
        ("1,500 incidents", "count"),
        ("1.2 million seconds", "time"),
    ]
    assert claims[0].hedged and not claims[1].hedged
    assert claims[2].value == pytest.approx(14.0 * 86400)
    assert claims[4].value == pytest.approx(1.2e6)


@pytest.mark.parametrize(
    "text",
    ["Open for 13,5 days.", "A span of 1.209.600 seconds.", "Roughly fifty-six percent longer."],
)
def test_ambiguous_numerical_expressions_are_flagged(text: str) -> None:
    claims = extract_numerical_claims(text)

    assert claims and all(claim.ambiguous for claim in claims)


# --- Support: evidence values and derivations -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "approximately 56% longer",  # (1,209,600.5 / 777,600 - 1) x 100 = 55.56
        "approximately 55% longer",  # hedged rounding
        "about 36% shorter",  # (1 - 777,600 / 1,209,600.5) x 100 = 35.71
        "20% of incidents",  # 1,500 / 7,500
        "a ratio of about 1.56",
        "1.6x the no-return median",
        "1,209,600.5 seconds",
        "about 14.0 days",  # unit conversion
        "432,000.5 seconds",  # absolute difference of the medians
        "1,500 incidents",
    ],
)
def test_evidence_backed_and_derived_values_are_supported(text: str) -> None:
    supported = _supported()
    claims = [claim for claim in extract_numerical_claims(text) if claim.material]

    assert claims and all(supported.supports(claim) for claim in claims)


@pytest.mark.parametrize(
    "text",
    [
        "55% longer",  # unhedged claim must match its stated precision
        "about 85% longer",
        "900,000 seconds",
        "1,209,600.5 incidents",  # right number, wrong unit dimension
        "doubled",
        "2x the no-return median",
        "a 45% reduction",
        "1.5 million seconds",
    ],
)
def test_incorrect_or_fabricated_values_are_not_supported(text: str) -> None:
    supported = _supported()
    claims = [claim for claim in extract_numerical_claims(text) if claim.material]

    assert claims and not any(supported.supports(claim) for claim in claims)


# --- Validator integration ------------------------------------------------------------


def test_correct_free_text_numbers_pass_without_rewriting(contract: EvidenceContractV1) -> None:
    statement = (
        "Incidents with repeated group visits have a median observed span approximately "
        "56% longer (1,209,600.5 vs 777,600.0 seconds); this is not causal."
    )
    recommendation = _recommendation(problem_statement=statement)

    validated, codes, _ = _validate(contract, recommendation)

    assert codes == ()
    assert validated.problem_statement == statement


@pytest.mark.parametrize(
    "field",
    [
        "problem_statement",
        "suggested_process_improvement",
        "expected_outcome_with_uncertainty",
        "proposed_follow_up_kpi",
        "implementation_risk",
    ],
)
def test_fabricated_metric_in_any_text_field_fails_closed(
    contract: EvidenceContractV1, field: str
) -> None:
    text = "The pilot will reduce the median observed span by 35%."
    validated, codes, notes = _validate(contract, _recommendation(**{field: text}))

    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in codes
    assert validated.status == "INSUFFICIENT_EVIDENCE"
    assert any(field in note and "35%" in note for note in notes)
    assert getattr(validated, field) == text


@pytest.mark.parametrize("field", ["hypotheses", "data_limitations"])
def test_fabricated_metric_in_list_fields_fails_closed(
    contract: EvidenceContractV1, field: str
) -> None:
    _, codes, _ = _validate(
        contract, _recommendation(**{field: ["Returns add 4.5 days to every incident."]})
    )

    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in codes


def test_incorrect_percentage_fails(contract: EvidenceContractV1) -> None:
    _, codes, _ = _validate(
        contract, _recommendation(problem_statement="The median span is about 85% longer.")
    )

    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in codes


def test_contradictory_free_text_number_fails(contract: EvidenceContractV1) -> None:
    _, codes, _ = _validate(
        contract,
        _recommendation(problem_statement="The group-return median is 900,000 seconds."),
    )

    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in codes


def test_fact_statement_must_state_its_own_value(contract: EvidenceContractV1) -> None:
    fact = {
        # 777,600.0 is in the evidence, but it is not this fact's value.
        "statement": "The group-return median observed span is 777,600.0 seconds.",
        "evidence_id": GROUP_FINDING,
        "field_path": SPAN_FIELD,
        "value": 1209600.5,
        "unit": "seconds",
    }
    _, codes, _ = _validate(contract, _recommendation(observed_numerical_facts=[fact]))

    assert codes == ("NUMERICAL_FACT_STATEMENT_MISMATCH",)


def test_ambiguous_number_fails_closed(contract: EvidenceContractV1) -> None:
    _, codes, _ = _validate(
        contract, _recommendation(problem_statement="Returning incidents stay open 13,5 days.")
    )

    assert "AMBIGUOUS_NUMERICAL_CLAIM_IN_TEXT" in codes


def test_number_without_any_evidence_reference_fails(contract: EvidenceContractV1) -> None:
    _, codes, _ = _validate(
        contract,
        _recommendation(
            evidence_ids=[],
            observed_numerical_facts=[],
            problem_statement="The median span is approximately 56% longer.",
        ),
    )

    assert {"MISSING_EVIDENCE", "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT"} <= set(codes)


def test_number_from_uncited_finding_is_not_supported(contract: EvidenceContractV1) -> None:
    text = "5,760 event transitions follow the Resolved-to-Closed pattern."
    _, uncited, _ = _validate(contract, _recommendation(problem_statement=text))
    _, cited, _ = _validate(
        contract,
        _recommendation(problem_statement=text, evidence_ids=[GROUP_FINDING, LIFECYCLE_FINDING]),
    )

    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in uncited
    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" not in cited


def test_percentages_are_not_derived_across_unrelated_findings(
    contract: EvidenceContractV1,
) -> None:
    # 5,760 / 7,500 = 76.8 % pairs a lifecycle count with a group-return count.
    _, codes, _ = _validate(
        contract,
        _recommendation(
            problem_statement="Returns affect 76.8% of the lifecycle transitions.",
            evidence_ids=[GROUP_FINDING, LIFECYCLE_FINDING],
        ),
    )

    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in codes


def test_simulated_values_are_supported_only_through_a_cited_simulation(
    contract: EvidenceContractV1,
) -> None:
    simulation = {
        "simulation_id": SIMULATION_ID,
        "status": "ESTIMATED",
        "classification": "hypothetical_non_causal",
        "baseline": {"selected_repeated_group_visit_events": 92},
        "scenarios": [
            {
                "name": "moderate",
                "eligible_event_share_assumption": 0.25,
                "reduction_share_assumption": 0.3,
                "hypothetical_repeated_visit_events_avoided": {"value": 6.9},
            }
        ],
        "evidence_references": [],
    }
    record = ToolCallRecord(
        call_id="tool_call_" + "d" * 24,
        sequence=1,
        tool_name="simulate_group_transition_reduction",
        validated_arguments={},
        status="success",
        returned_count=1,
        result_items=(simulation,),
    )
    citation = SimulationCitationV1(
        simulation_id=SIMULATION_ID,
        status="ESTIMATED",
        assumptions=(
            {
                "name": "moderate",
                "eligible_event_share_assumption": 0.25,
                "reduction_share_assumption": 0.3,
                "hypothetical_repeated_visit_events_avoided": 6.9,
            },
        ),
    )
    text = "Under the moderate scenario about 6.9 events would be avoided (25% eligible)."

    _, with_simulation, _ = _validate(
        contract,
        _recommendation(expected_outcome_with_uncertainty=text, simulation=citation),
        (record,),
    )
    _, without_simulation, _ = _validate(
        contract, _recommendation(expected_outcome_with_uncertainty=text), (record,)
    )

    assert with_simulation == ()
    assert "UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT" in without_simulation


def test_model_written_recommendation_fixture_still_validates(contract: EvidenceContractV1) -> None:
    saved = json.loads(MODEL_TEXT_FIXTURE.read_text(encoding="utf-8"))
    payload = dict(saved["recommendations"][0], status="DRAFT")
    recommendation = RecommendationCandidate.model_validate(payload)

    validated, codes, _ = _validate(contract, recommendation)

    assert codes == ()
    assert validated.problem_statement == recommendation.problem_statement
    assert [claim.text for claim in extract_numerical_claims(recommendation.problem_statement)] == [
        "56%"
    ]
