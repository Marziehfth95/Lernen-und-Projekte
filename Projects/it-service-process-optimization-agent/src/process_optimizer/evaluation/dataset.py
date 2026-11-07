"""Small reproducible aggregate-evidence evaluation dataset."""

from process_optimizer.evaluation.schemas import EvaluationCaseV1


def evaluation_cases() -> tuple[EvaluationCaseV1, ...]:
    """Return versioned cases without embedding raw BPI13 records."""
    return (
        EvaluationCaseV1(
            case_id="evaluation_case_group_returns",
            scenario="group_returns",
            goal=(
                "Investigate the observed association between repeated group visits and "
                "observed case spans, use a what-if simulation, and propose only a safe "
                "approval-gated next step."
            ),
            expected_tools=(
                "compare_process_cohorts",
                "get_evidence_details",
                "simulate_group_transition_reduction",
            ),
            expected_final_decision="VALIDATED_RECOMMENDATIONS",
            prohibited_interpretations=(
                "caused longer",
                "guaranteed time savings",
                "guaranteed sla",
            ),
        ),
        EvaluationCaseV1(
            case_id="evaluation_case_completed_lifecycle",
            scenario="completed_lifecycle",
            goal=(
                "Investigate Completed-to-Completed while preserving lifecycle semantics and "
                "without treating repeated activity names as confirmed rework."
            ),
            expected_tools=("inspect_lifecycle_patterns", "get_evidence_details"),
            expected_final_decision="VALIDATED_RECOMMENDATIONS",
            prohibited_interpretations=(
                "represents confirmed rework",
                "multiple incident closures",
            ),
        ),
    )
