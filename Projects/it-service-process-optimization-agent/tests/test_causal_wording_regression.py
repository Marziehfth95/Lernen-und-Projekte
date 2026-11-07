"""Regression cases from the first real GPT-4.1-mini Hosted Agent run (2026-10-31, v5).

The optimizer's only recommendation was blocked with UNSUPPORTED_CAUSAL_STATEMENT. The
matched span ("uncertainty remains due to lack of causal evidence") is epistemic, but the
same field promised a "potential reduction in case span by minimizing inefficient repeated
group visits", an intervention effect and an inefficiency that the observational finding
explicitly does not establish. The block stands; the fix is prompt wording (v3), not a
looser rule. Texts are the aggregate-only recommendation fields from that run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.test_agent_workflow import (  # noqa: F401 - pytest fixture re-export
    RecordingStructuredChatModel,
    _foundry_provider,
    analysis_dir,
)

from process_optimizer.agents.provider import (
    PROMPT_VERSION,
    DeterministicMockProvider,
    ModelProviderError,
)
from process_optimizer.agents.schemas import EvidenceValidationResult, RecommendationCandidate
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.guardrails import Decision, GuardrailEngine

ENGINE = GuardrailEngine()
FINDING = "finding_0123456789abcdef0000a001"

BLOCKED_RUN_RECOMMENDATION: dict[str, Any] = {
    "recommendation_id": "recommendation_0123456789abcdef0000b002",
    "problem_statement": (
        "Incidents with repeated group visits exhibit a substantially longer observed case "
        "span compared to those without such visits, with a median span ratio of "
        "approximately 1.56."
    ),
    "evidence_ids": [FINDING],
    "observed_numerical_facts": [
        {
            "statement": "Median ratio of observed case spans comparing incidents with "
            "repeated group visits to those without is approximately 1.56.",
            "evidence_id": FINDING,
            "field_path": "measured_statistics.span_median_ratio",
            "value": 1.5555561985596709,
            "unit": "ratio",
        }
    ],
    "hypotheses": [
        "Repeated group visits may reflect process complexity or valid routing rather than "
        "inefficiency.",
        "Longer observed case spans may be influenced by factors not adjusted for like "
        "product, impact, organization, or calendar time.",
        "Observed case span measure may not reflect active processing or resolution time "
        "accurately.",
    ],
    "data_limitations": [
        "Cohorts differ in event volume and process complexity and are observational "
        "without causal inference.",
        "No adjustments for product, impact, organization, censoring, or calendar time "
        "were made.",
        "Repeated group visits can be part of valid routing, not necessarily indicating "
        "inefficiency.",
        "Observed case span is not verified as resolution time or active processing time.",
    ],
    "suggested_process_improvement": (
        "Conduct a detailed root-cause analysis to distinguish between necessary repeated "
        "group visits and potentially avoidable ones, considering additional factors like "
        "product type and impact severity before enacting group-transition reduction "
        "interventions."
    ),
    "expected_outcome_with_uncertainty": (
        "Potential reduction in case span by minimizing inefficient repeated group visits, "
        "though uncertainty remains due to lack of causal evidence and possible valid "
        "routing cases."
    ),
    "proposed_follow_up_kpi": (
        "Percentage of incidents with repeated group visits, and median observed case span "
        "for incidents stratified by repetition status."
    ),
    "implementation_risk": (
        "Risk of disrupting valid routing leading to unintended delays or misrouting if "
        "repeated group visits are reduced without proper cause analysis."
    ),
    "simulation": None,
    "status": "INSUFFICIENT_EVIDENCE",
    "requires_human_approval": True,
}

DESCRIPTIVE_OUTCOME = (
    "A stratified review may identify which repeated group visits are avoidable and which "
    "reflect valid routing; no reduction in observed case span is promised from the current "
    "observational evidence."
)


def _candidate(**update: Any) -> RecommendationCandidate:
    return RecommendationCandidate.model_validate({**BLOCKED_RUN_RECOMMENDATION, **update})


def _check(candidate: RecommendationCandidate) -> Any:
    # The validator accepted this recommendation in the run (valid, no error codes).
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=True,
        validated_evidence_ids=candidate.evidence_ids,
        error_codes=(),
        notes=(),
    )
    return ENGINE.check_recommendations(
        recommendations=(candidate,), validation_results=(validation,)
    )


def test_original_blocked_recommendation_is_still_blocked() -> None:
    result = _check(_candidate())

    assert result.decision == Decision.BLOCK
    # CA-01 still fires on "due to"; CA-04..06 now also name the actual defects.
    assert result.reason_codes == (
        "UNSUPPORTED_CAUSAL_STATEMENT",
        "UNSUPPORTED_INTERVENTION_CLAIM",
        "UNSUPPORTED_INEFFICIENCY_CLAIM",
        "UNQUALIFIED_EXPECTED_OUTCOME",
    )


def test_evidence_grounded_descriptive_version_requires_approval() -> None:
    result = _check(_candidate(expected_outcome_with_uncertainty=DESCRIPTIVE_OUTCOME))

    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.reason_codes == ()


def test_labelled_causal_hypothesis_requires_approval() -> None:
    candidate = _candidate(
        expected_outcome_with_uncertainty=DESCRIPTIVE_OUTCOME,
        hypotheses=(
            "Hypothesis: repeated group visits could contribute to longer observed case "
            "spans; this is not established by the observational evidence.",
        ),
    )

    assert _check(candidate).decision == Decision.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    ("field", "text", "code"),
    [
        ("problem_statement",
         "Longer observed case spans are due to repeated group visits.",
         "UNSUPPORTED_CAUSAL_STATEMENT"),
        ("expected_outcome_with_uncertainty",
         "Removing repeated group visits will reduce the observed case span.",
         "UNSUPPORTED_CAUSAL_STATEMENT"),
        ("suggested_process_improvement",
         "Cut repeated group visits, since they drive the longer spans.",
         "UNSUPPORTED_CAUSAL_STATEMENT"),
        ("hypotheses",
         ("Repeated group visits drive the longer observed case spans.",),
         "UNSUPPORTED_CAUSAL_CONCLUSION"),
    ],
)
def test_unsupported_firm_causal_claims_are_blocked(field: str, text: Any, code: str) -> None:
    candidate = _candidate(
        **{"expected_outcome_with_uncertainty": DESCRIPTIVE_OUTCOME, field: text}
    )
    result = _check(candidate)

    assert result.decision == Decision.BLOCK
    assert code in result.reason_codes


def test_rejected_validation_still_blocks_the_descriptive_version() -> None:
    candidate = _candidate(expected_outcome_with_uncertainty=DESCRIPTIVE_OUTCOME)
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=False,
        validated_evidence_ids=(),
        error_codes=(),
        notes=(),
    )
    result = ENGINE.check_recommendations(
        recommendations=(candidate,), validation_results=(validation,)
    )

    assert result.decision == Decision.BLOCK
    assert "VALIDATION_FAILED" in result.reason_codes


def test_optimizer_prompt_v3_requires_descriptive_noncausal_wording(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    model = RecordingStructuredChatModel()
    provider = _foundry_provider(monkeypatch, model)
    evidence = InvestigationRunner(analysis_dir, DeterministicMockProvider())._evidence  # noqa: SLF001

    with pytest.raises(ModelProviderError):  # the fake model returns no parsed output
        provider.propose_optimizations("goal", "group_returns", (), evidence, ())

    rule = model.user_payloads[0]["trusted_aggregate_context"]["wording_rule"]
    assert PROMPT_VERSION == "phase5-prompts-v3"
    for phrase in (
        "Observed associations",
        "Hypotheses",
        "Intervention effects",
        "simulations are hypothetical",
        "due to",
        "will reduce",
        "no reduction is promised",
        "savings",
        "SLA improvement",
        "inefficient",
    ):
        assert phrase in rule
