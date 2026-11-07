"""Regression matrix for CA-03 (clause-scoped labels), CA-04, CA-05 and CA-06 (offline).

Each case changes one field of the recommendation from the first real Hosted Agent run
(2026-10-31, v5) and runs it through the guardrail engine with the validation result that
run produced (valid, no error codes). The rules are pattern based, so passing this matrix
does not establish semantic completeness. Closed gaps are kept in
``CLOSED_FALSE_NEGATIVES``; the remaining false positives are asserted as such, and the
residual false negatives are recorded in ``test_causal_grounding_evaluation.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from tests.test_agent_workflow import analysis_dir  # noqa: F401 - pytest fixture re-export
from tests.test_causal_wording_regression import BLOCKED_RUN_RECOMMENDATION, FINDING

from process_optimizer.agents import validation as validator
from process_optimizer.agents.provider import DeterministicMockProvider
from process_optimizer.agents.schemas import (
    EvidenceValidationResult,
    InvestigationRequestV1,
    RecommendationCandidate,
    SimulationCitationV1,
)
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.guardrails import Decision, GuardrailEngine, load_policy
from process_optimizer.process_mining.evidence_contract import EvidenceFindingV1

ENGINE = GuardrailEngine()
EO = "expected_outcome_with_uncertainty"
PS = "problem_statement"
SI = "suggested_process_improvement"
HY = "hypotheses"
KPI = "proposed_follow_up_kpi"
CAUSAL = "UNSUPPORTED_CAUSAL_STATEMENT"
CONCLUSION = "UNSUPPORTED_CAUSAL_CONCLUSION"
INTERVENTION = "UNSUPPORTED_INTERVENTION_CLAIM"
INEFFICIENCY = "UNSUPPORTED_INEFFICIENCY_CLAIM"
UNQUALIFIED = "UNQUALIFIED_EXPECTED_OUTCOME"
SAVINGS = "UNSUPPORTED_SAVINGS_CLAIM"
ATTRIBUTION = "UNSUPPORTED_CAUSAL_ATTRIBUTION"
TIMING = "UNSUPPORTED_TIMING_EFFECT"
QUANTIFIED = "UNSUPPORTED_QUANTIFIED_EFFECT"

SAFE_OUTCOME = (
    "A stratified review may identify which repeated group visits are avoidable and which "
    "reflect valid routing; no reduction in observed case span is promised from the current "
    "observational evidence."
)
SIMULATION = SimulationCitationV1(
    simulation_id="simulation_" + "0" * 24,
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

# (case id, field, text, expected codes; empty = REQUIRE_APPROVAL, "sim" = cite simulation)
Case = tuple[str, str, Any, tuple[str, ...]]

# Frozen baseline: 25 unsafe variants (all BLOCK).
UNSAFE_BASELINE: list[Case] = [
    ("ORIG", EO, BLOCKED_RUN_RECOMMENDATION[EO],
     (CAUSAL, INTERVENTION, INEFFICIENCY, UNQUALIFIED)),
    ("U01", EO, "Potential reduction in case span by minimizing inefficient repeated group "
     "visits, though uncertainty remains given the lack of causal evidence.",
     (INTERVENTION, INEFFICIENCY, UNQUALIFIED)),
    ("U02", EO, "Potential reduction in case span by minimizing inefficient repeated group "
     "visits, although this is uncertain because causal evidence is lacking.",
     (INTERVENTION, INEFFICIENCY, UNQUALIFIED)),
    ("U03", EO, "Reducing repeated group visits is expected to shorten case spans.",
     (INTERVENTION, UNQUALIFIED)),
    ("U04", EO, "Minimizing repeated group visits should reduce observed case spans.",
     (INTERVENTION, UNQUALIFIED)),
    ("U05", EO, "Fewer repeated group visits would mean shorter resolution times.",
     (INTERVENTION, UNQUALIFIED)),
    ("U06", EO, "Faster resolution can be achieved by avoiding unnecessary group returns.",
     (INTERVENTION, INEFFICIENCY, UNQUALIFIED)),
    ("U07", EO, "Avoiding group returns would improve SLA compliance.",
     (INTERVENTION, UNQUALIFIED)),
    ("U08", EO, "Time to resolution will decrease once returns are minimized.",
     (INTERVENTION, UNQUALIFIED)),
    ("U09", EO, "This is likely to lower the median case span.", (INTERVENTION, UNQUALIFIED)),
    ("U10", EO, "Potentially, minimizing repeated visits may improve resolution times.",
     (INTERVENTION, UNQUALIFIED)),
    ("U11", EO, "Removing returns yields shorter spans.", (INTERVENTION, UNQUALIFIED)),
    ("U12", PS, "Repeated group visits are inefficient and prolong case spans.",
     (INTERVENTION, INEFFICIENCY)),
    ("U13", PS, "Inefficient group returns add delay to incident handling.",
     (INTERVENTION, INEFFICIENCY)),
    ("U14", SI, "Reduce repeated group visits to shorten case spans.", (INTERVENTION,)),
    ("U15", SI, "Eliminate wasteful group returns to speed up resolution.",
     (CAUSAL, INTERVENTION, INEFFICIENCY)),
    ("U16", HY, ("Repeated group visits prolong case spans.",), (CONCLUSION,)),
    ("U17", HY, ("Returns increase the observed case span.",), (CONCLUSION,)),
    ("U18", KPI, "Track the reduction in case span achieved by fewer returns.", (INTERVENTION,)),
    ("U19", EO, "Eliminating group returns could cut case span by about 40%.",
     (INTERVENTION, UNQUALIFIED)),
    ("U20", EO, "Shorter spans can be expected once avoidable returns are removed.",
     (INTERVENTION, INEFFICIENCY, UNQUALIFIED)),
    ("U21", EO, "Not only would fewer returns shorten spans, they would also lower workload.",
     (INTERVENTION, UNQUALIFIED)),
    ("C01", EO, "Avoiding returns results in faster closure.", (INTERVENTION, UNQUALIFIED)),
    ("C02", EO, "The pilot will reduce delays.", (CAUSAL, INTERVENTION, UNQUALIFIED)),
    ("C03", HY, ("Inefficient routing causes the longer spans.",), (CAUSAL,)),
]

# Frozen baseline: 20 safe variants (REQUIRE_APPROVAL unless listed as a known FP).
SAFE_BASELINE: list[Case] = [
    ("S01", EO, SAFE_OUTCOME, ()),
    ("S02", EO, "No reduction in observed case span is promised or implied by this "
     "observational evidence.", ()),
    ("S03", EO, "This analysis does not show that reducing repeated group visits would "
     "shorten case spans; a review would examine routing patterns first.", ()),
    ("S04", EO, "The review itself will not reduce case spans; it will measure which "
     "returns reflect valid routing.", ()),
    ("S05", PS, "Incidents with repeated group visits have a longer observed case span; "
     "the association is unadjusted and does not establish a routing cause.", ()),
    ("S06", HY, ("Hypothesis: fewer avoidable returns could shorten case spans; this is "
     "not established.",), ()),
    ("S07", HY, ("Some returns may reflect avoidable routing, while others may reflect "
     "valid specialist collaboration.",), ()),
    ("S08", EO, "This should improve analytical precision and reduce false rework "
     "narratives; it does not imply that the observed interval itself will become shorter.",
     ()),
    ("S09", SI, "Conduct a human-led stratified routing review by product, impact and "
     "organization before proposing any routing pilot.", ()),
    ("S10", KPI, "Median observed case span for incidents stratified by repetition status, "
     "with sample sizes.", ()),
    ("S11", EO, "A pilot may test whether fewer returns coincide with shorter spans; no "
     "reduction is promised.", ()),
    ("S12", PS, "Repeated group visits do not by themselves establish inefficiency; the "
     "longer observed span is an association only.", ()),
    ("S14", EO, "Under the moderate scenario about 6.9 repeated-visit events would be "
     "avoided as a hypothetical count; no reduction in case span is promised.", ("sim",)),
    ("S15", PS, "Incidents without repeated group visits show shorter observed case spans "
     "than incidents with them.", ()),
    ("S16", PS, "The with-return cohort has a higher median and a longer p90 observed case "
     "span.", ()),
    ("S17", SI, "Review whether returns can be reduced for specific products, and measure "
     "whether the observed case span changes in a matched comparison.", ()),
    ("S18", KPI, "Rate of repeated group visits and median, p75 and p90 observed case span, "
     "before and after any approved pilot.", ()),
    ("S19", SI, "Consider additional factors such as product type and impact before any "
     "routing change.", ()),
    ("S20", EO, "Lifecycle-qualified labels should improve analytical precision; no change "
     "in resolution time is implied.", ()),
    # Known false positive of the pre-existing CA-01 list ("due to"), kept unchanged.
    ("S13", EO, "A review may clarify routing; uncertainty remains due to the lack of causal "
     "evidence.", (CAUSAL,)),
]

# The six paraphrases that bypassed the earlier CA-04/CA-05 prototype.
PARAPHRASES: list[Case] = [
    ("P1", EO, "Fewer handoffs would let incidents close sooner.", (TIMING,)),
    ("P2", EO, "Incidents could be resolved more quickly with fewer returns.", (TIMING,)),
    ("P3", EO, "Streamlining routing would benefit turnaround.", (INTERVENTION, UNQUALIFIED)),
    ("P4", EO, "This would free capacity in second-level support.",
     (INTERVENTION, UNQUALIFIED)),
    ("P5", EO, "Tightening routing rules is the obvious fix for the long spans.",
     (INTERVENTION, UNQUALIFIED)),
    ("P6", EO, "Cutting returns would shrink the gap between the cohorts.",
     (INTERVENTION, UNQUALIFIED)),
]

CATEGORY_CASES: list[Case] = [
    # Safe observational comparisons.
    ("OBS1", PS, "The p90 observed case span is longer for incidents with a repeated group "
     "visit.", ()),
    ("OBS2", PS, "Repeated group visits co-occur with longer observed case spans; the "
     "comparison is unadjusted.", ()),
    ("OBS3", PS, "Incidents with fewer group visits show shorter observed case spans.", ()),
    # Labelled causal hypotheses in the hypotheses field.
    ("HYP1", HY, ("Repeated group visits may prolong observed case spans.",), ()),
    ("HYP2", HY, ("Hypothesis: returns could lengthen spans; this is not established.",), ()),
    ("HYP3", HY, ("Gruppenrückläufe könnten die Laufzeit verlängern.",), ()),
    # A label in another clause does not cover an unlabelled causal clause.
    ("HYP4", HY, ("Some returns may be valid; returns prolong case spans.",), (CONCLUSION,)),
    # Negated promises.
    ("NEG1", EO, "A review may clarify routing; a reduction in case span is not promised.", ()),
    ("NEG2", EO, "The data do not show that cutting returns would shorten spans; a matched "
     "review would test this.", ()),
    ("NEG3", SI, "Do not assume that removing returns shortens spans; review routing first.",
     ()),
    # Unsupported implied intervention benefits.
    ("BEN1", SI, "Prevent repeated group visits to improve SLA attainment.", (INTERVENTION,)),
    ("BEN2", PS, "Repeated group visits add days to resolution.", (INTERVENTION,)),
    ("BEN3", EO, "A pilot would measure the reduction in span achieved by fewer returns.",
     (INTERVENTION,)),
    # Unsupported inefficiency labels, and safe hedged or negated uses.
    ("INE1", PS, "Most repeated group visits are unnecessary.", (INEFFICIENCY,)),
    ("INE2", SI, "Target the redundant handoffs between second-level groups.", (INEFFICIENCY,)),
    ("INE3", SI, "Distinguish between necessary repeated group visits and potentially "
     "avoidable ones.", ()),
    ("INE4", SI, "If reviewers identify a coherent avoidable pattern, prepare a reversible "
     "pilot.", ()),
    ("INE5", PS, "Repeated group visits are not necessarily inefficient.", ()),
    # KPI fields: claimed effects block, planned measurements pass.
    ("KPI1", KPI, "Hours of case span removed thanks to fewer returns.", (INTERVENTION,)),
    ("KPI2", KPI, "Median case span, which will decrease after the pilot.", (INTERVENTION,)),
    ("KPI3", KPI, "Change in median observed case span between matched pilot and comparison "
     "cohorts.", ()),
    ("KPI4", KPI, "Reduction in the rate of repeated group visits, reported with sample "
     "sizes.", ()),
    # Contradictory claims and disclaimers: the disclaimer never cancels the claim.
    ("CON1", EO, "Fewer returns would shorten spans; no reduction is promised.",
     (INTERVENTION,)),
    ("CON2", EO, "Removing returns would shorten spans and this is not guaranteed.",
     (INTERVENTION,)),
    ("CON3", EO, "This does not imply causality, and cutting returns will reduce delays.",
     (CAUSAL, INTERVENTION)),
    ("CON4", EO, "No reduction is promised: fewer returns shorten resolution time.",
     (INTERVENTION,)),
    # Numerical effect claims restated from cohort differences.
    ("NUM1", EO, "Removing returns could take about 432000 seconds off each incident; no "
     "reduction is promised.", (QUANTIFIED,)),
    ("NUM2", SI, "Cut repeated group visits by 40%.", (INTERVENTION,)),
    ("NUM3", EO, "Avoiding returns could save roughly 41% of the span.", (QUANTIFIED,)),
    # Hypothetical simulation counts: labelled and cited pass; unlabelled or uncited block.
    ("SIM1", EO, "Under the moderate scenario about 6.9 events would be avoided (25% "
     "eligible).", ("sim",)),
    ("SIM2", EO, "Under the moderate scenario about 6.9 events would be avoided (25% "
     "eligible).", (INTERVENTION, UNQUALIFIED)),
    ("SIM3", EO, "About 6.9 events would be avoided; no reduction in case span is promised.",
     ("sim", INTERVENTION)),
    ("SIM4", EO, "Under the moderate scenario about 6.9 avoided events would shorten case "
     "spans.", ("sim", INTERVENTION)),
]

# The four false negatives demonstrated against policy a0b4adeb…, closed by CA-07.
CLOSED_FALSE_NEGATIVES: list[Case] = [
    ("FN1", PS, "Group returns are the bottleneck behind long case spans.", (ATTRIBUTION,)),
    ("FN2", SI, "Route incidents directly to the final group to get them closed earlier.",
     (TIMING,)),
    ("FN3", HY, ("Returns are the main reason for long spans.",), (ATTRIBUTION,)),
    ("FN4", PS, "Each group return costs roughly five extra days.", (QUANTIFIED,)),
]
# CA-07 counterparts that must stay REQUIRE_APPROVAL.
CA07_SAFE: list[Case] = [
    ("A01", HY, ("Returns may be the main reason for long spans.",), ()),
    ("A02", PS, "The data do not show that returns are the bottleneck behind long spans.", ()),
    ("A03", PS, "Incidents with a repeated group visit had a median observed case span about "
     "432000.5 seconds longer than incidents without one.", ()),
    ("A04", EO, "A review may identify whether returns are the reason for longer spans; no "
     "reduction is promised.", ()),
    ("A05", PS, "Incidents with returns closed later on average than incidents without.", ()),
]
CA07_UNSAFE: list[Case] = [
    # A label never makes a quantified effect acceptable in a hypothesis.
    ("Q01", HY, ("Each return may add about five days to the span.",), (QUANTIFIED,)),
    ("Q02", PS, "Every additional handoff adds 5 days.", (QUANTIFIED,)),
    ("Q03", SI, "Fix routing so that incidents are resolved sooner.", (TIMING,)),
    ("Q04", PS, "Long spans stem from returns to earlier groups.", (ATTRIBUTION,)),
    ("Q05", HY, ("Some returns may be valid; returns are the cause of long spans.",),
     (ATTRIBUTION,)),
]
# Descriptive wording the vocabulary misreads as a claim.
KNOWN_FALSE_POSITIVES: list[Case] = [
    ("FP1", PS, "Incidents with reduced handoffs show shorter observed case spans.",
     (INTERVENTION,)),
]

ALL_CASES = (
    UNSAFE_BASELINE + SAFE_BASELINE + PARAPHRASES + CATEGORY_CASES
    + CLOSED_FALSE_NEGATIVES + CA07_SAFE + CA07_UNSAFE + KNOWN_FALSE_POSITIVES
)


def _decide(field: str, text: Any, *, simulation: bool) -> Any:
    payload = dict(BLOCKED_RUN_RECOMMENDATION)
    if field != EO or text is not BLOCKED_RUN_RECOMMENDATION[EO]:
        payload[EO] = SAFE_OUTCOME
    payload[field] = list(text) if isinstance(text, tuple) else text
    payload["simulation"] = SIMULATION.model_dump(mode="json") if simulation else None
    candidate = RecommendationCandidate.model_validate(payload)
    # The validator's causal-text check, exactly as validation.py applies it; its numeric
    # checks need the full aggregate contract and run in the offline pipeline matrix.
    prose = " ".join((candidate.problem_statement, candidate.expected_outcome_with_uncertainty,
                      candidate.suggested_process_improvement, *candidate.hypotheses))
    causal = any(pattern.search(prose) for pattern in validator._CAUSAL_PATTERNS)  # noqa: SLF001
    # CA-07 exactly as the validator runs it, with the cited finding's real interpretation.
    claim_codes = validator._claim_evidence_errors(  # noqa: SLF001
        candidate, candidate.evidence_ids,
        {FINDING: {"interpretation": "observational_non_causal"}}, ENGINE.policy,
    )
    codes = (*((CAUSAL,) if causal else ()), *claim_codes)
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=not codes,
        validated_evidence_ids=candidate.evidence_ids,
        error_codes=codes,
        notes=(),
    )
    return ENGINE.check_recommendations(
        recommendations=(candidate,), validation_results=(validation,)
    )


@pytest.mark.parametrize(("case_id", "field", "text", "expected"), ALL_CASES,
                         ids=[case[0] for case in ALL_CASES])
def test_causal_grounding_matrix(case_id: str, field: str, text: Any, expected: tuple) -> None:
    codes = tuple(code for code in expected if code != "sim")
    result = _decide(field, text, simulation="sim" in expected)

    assert result.reason_codes == codes, case_id
    assert result.decision == (Decision.BLOCK if codes else Decision.REQUIRE_APPROVAL), case_id


def test_matrix_sizes_are_frozen() -> None:
    assert (len(UNSAFE_BASELINE), len(SAFE_BASELINE), len(PARAPHRASES)) == (25, 20, 6)


def test_original_blocked_azure_recommendation_still_blocks() -> None:
    candidate = RecommendationCandidate.model_validate(BLOCKED_RUN_RECOMMENDATION)
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=True,
        validated_evidence_ids=candidate.evidence_ids,
        error_codes=(),
        notes=(),
    )
    result = ENGINE.check_recommendations(
        recommendations=(candidate,), validation_results=(validation,)
    )

    assert result.decision == Decision.BLOCK
    assert result.reason_codes == (CAUSAL, INTERVENTION, INEFFICIENCY, UNQUALIFIED)


@pytest.mark.parametrize("scenario", ["group_returns", "completed_lifecycle"])
def test_reference_recommendations_still_require_approval(
    analysis_dir: Path, scenario: str  # noqa: F811
) -> None:
    result = InvestigationRunner(analysis_dir, DeterministicMockProvider()).run(
        InvestigationRequestV1(
            analysis_run_id=InvestigationRunner(
                analysis_dir, DeterministicMockProvider()
            ).analysis_run_id,
            investigation_goal="Prepare an aggregate, non-causal improvement hypothesis.",
            execution_mode="mocked_llm",
            scenario=scenario,
        )
    )

    assert result.final_decision == "VALIDATED_RECOMMENDATIONS"
    assert result.recommendations and all(item.valid for item in result.validation_results)


def test_no_evidence_class_can_support_an_intervention_effect() -> None:
    # CA-04 and CA-06 have no exemption for measured intervention effects because the
    # Evidence Contract cannot express one. A new interpretation needs a reviewed rule.
    interpretation = EvidenceFindingV1.model_fields["interpretation"].annotation
    assert get_args(interpretation) == ("observational_non_causal",)


def test_claim_rule_defects_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = GuardrailEngine(load_policy())

    def broken(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("defect")

    monkeypatch.setattr(type(engine.policy.claim_rules), "intervention_claim", broken)
    candidate = RecommendationCandidate.model_validate(
        {**BLOCKED_RUN_RECOMMENDATION, EO: SAFE_OUTCOME}
    )
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=True,
        validated_evidence_ids=candidate.evidence_ids,
        error_codes=(),
        notes=(),
    )
    result = engine.check_recommendations(
        recommendations=(candidate,), validation_results=(validation,)
    )

    assert result.decision == Decision.BLOCK
    assert result.reason_codes == ("GUARDRAIL_INTERNAL_ERROR",)


def _historical(path: str, prefix: str) -> RecommendationCandidate:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if str(node.get("recommendation_id", "")).startswith(prefix) and (
                EO in node
            ):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads((Path(__file__).parents[1] / path).read_text(encoding="utf-8")))
    return RecommendationCandidate.model_validate(
        {k: v for k, v in found[0].items() if k in RecommendationCandidate.model_fields}
    )


def test_model_written_intervention_promise_is_blocked_at_submission() -> None:
    # The fixture keeps the wording of a model-written recommendation that promised
    # interventions "could reduce median observed case span" (numbers are synthetic).
    # Stored approvals are not re-checked (decide() does not re-validate); only a new
    # submission of this text is blocked.
    candidate = _historical(
        "tests/data/model_text_regression_recommendation.json", "recommendation_0123456789abcdef"
    )
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=True,
        validated_evidence_ids=candidate.evidence_ids,
        error_codes=(),
        notes=(),
    )
    result = ENGINE.check_recommendations(
        recommendations=(candidate,), validation_results=(validation,)
    )

    assert result.decision == Decision.BLOCK
    assert result.reason_codes == (INTERVENTION,)


def _claim_codes(text: str, interpretation: object, policy: Any = None) -> list[str]:
    candidate = RecommendationCandidate.model_validate(
        {**BLOCKED_RUN_RECOMMENDATION, EO: SAFE_OUTCOME, PS: text}
    )
    return validator._claim_evidence_errors(  # noqa: SLF001
        candidate, candidate.evidence_ids, {FINDING: {"interpretation": interpretation}},
        policy or ENGINE.policy,
    )


@pytest.mark.parametrize("interpretation", ["causal_effect_estimate", "", None, 7])
def test_unknown_evidence_interpretation_fails_closed(interpretation: object) -> None:
    codes = _claim_codes("Observed spans differ between cohorts.", interpretation)

    assert codes == ["UNKNOWN_EVIDENCE_TYPE"]


def test_claim_is_supported_only_by_a_known_evidence_type_that_provides_it(
    tmp_path: Path,
) -> None:
    claim = "Group returns are the bottleneck behind long case spans."
    raw = json.loads(
        (Path(validator.__file__).parents[1] / "guardrails" / "policy.v1.json").read_text(
            encoding="utf-8"
        )
    )
    raw["claim_evidence"]["known_evidence_interpretations"].append("causal_effect_estimate")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    extended = load_policy(path)

    assert _claim_codes(claim, "observational_non_causal") == [ATTRIBUTION]
    assert _claim_codes(claim, "observational_non_causal", extended) == [ATTRIBUTION]
    assert _claim_codes(claim, "causal_effect_estimate", extended) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["claim_evidence"]["claim_types"][0]["frames"].append(r"\bno group\b"),
        lambda raw: raw["claim_evidence"]["claim_types"].append(
            raw["claim_evidence"]["claim_types"][0]
        ),
        lambda raw: raw["claim_evidence"].pop("known_evidence_interpretations"),
        lambda raw: raw.update(policy_schema_version="1.0.0"),
        lambda raw: raw["claim_evidence"]["claim_types"][0].update(reason_code="ATTRIBUTION"),
    ],
    ids=["frame-without-rel", "duplicate-type", "no-known-types", "old-schema", "bad-code"],
)
def test_malformed_claim_policy_refuses_to_load(tmp_path: Path, mutate: Any) -> None:
    from process_optimizer.guardrails import PolicyLoadError

    raw = json.loads(
        (Path(validator.__file__).parents[1] / "guardrails" / "policy.v1.json").read_text(
            encoding="utf-8"
        )
    )
    mutate(raw)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PolicyLoadError):
        load_policy(path)


def test_every_claim_reason_code_is_a_known_validator_code() -> None:
    from process_optimizer.guardrails import KNOWN_VALIDATOR_ERROR_CODES

    codes = {claim.reason_code for claim in ENGINE.policy.claim_evidence.claim_types}
    assert codes | {"UNKNOWN_EVIDENCE_TYPE"} <= KNOWN_VALIDATOR_ERROR_CODES
