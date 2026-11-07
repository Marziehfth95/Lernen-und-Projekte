"""Phase 8A local demo launcher: full approve/reject/idempotency walkthrough."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from process_optimizer.demo.launcher import (
    DemoError,
    reset_demo_database,
    run_full_demo,
    verify_governed_aggregates,
)
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
from process_optimizer.tools.manifest import build_aggregate_manifest

RUN_ID = "run_aaaaaaaaaaaaaaaaaaaaaaaa"
GROUP_FINDING = "finding_111111111111111111111111"
LIFECYCLE_FINDING = "finding_222222222222222222222222"
HANDOFF_FINDING = "finding_333333333333333333333333"


def _reference(digit: str) -> AggregateEvidenceReferenceV1:
    return AggregateEvidenceReferenceV1(
        reference_id=f"evidence_ref_{digit * 24}",
        artifact="synthetic.json",
        artifact_sha256=digit * 64,
        selector={"fixture": "synthetic"},
        metric="synthetic aggregate",
    )


def _contract() -> EvidenceContractV1:
    source = DatasetSourceV1(
        name="Synthetic aggregate demo-launcher fixture",
        source_url="https://example.invalid/synthetic",
        sha256="a" * 64,
    )
    group = EvidenceFindingV1(
        finding_id=GROUP_FINDING,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(
            name="observed_span_association_with_group_returns",
            definition="Synthetic aggregate comparison.",
            unit="incidents_and_seconds",
        ),
        aggregate_population=AggregatePopulationV1(label="synthetic cohorts"),
        counts=EvidenceCountsV1(
            count_level="incident", observation_count=1500, numerator=1500, denominator=7500
        ),
        measured_statistics={
            "with_return_incident_count": 1500,
            "without_return_incident_count": 6000,
            "with_return_span_median_seconds": 1209600.5,
            "without_return_span_median_seconds": 777600.0,
            "span_median_ratio": 1.5555561985596709,
        },
        baseline_or_comparison=ComparisonGroupV1(
            label="without returns", statistics={"incident_count": 6000}
        ),
        data_limitations=("Synthetic aggregate fixture for tests only.",),
        sufficiency_status="sufficient",
        evidence_references=(_reference("1"),),
    )
    lifecycle = EvidenceFindingV1(
        finding_id=LIFECYCLE_FINDING,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(
            name="lifecycle_decomposition_of_activity_transition",
            definition="Synthetic aggregate lifecycle decomposition.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(
            label="synthetic transitions",
            filters={"source_activity": "Completed", "target_activity": "Completed"},
        ),
        counts=EvidenceCountsV1(
            count_level="event_transition",
            observation_count=5800,
            numerator=5760,
            denominator=5800,
        ),
        measured_statistics={
            "same_group_event_count": 5790,
            "incident_count": 5575,
            "dominant_source_lifecycle": "Resolved",
            "dominant_target_lifecycle": "Closed",
        },
        baseline_or_comparison=ComparisonGroupV1(
            label="dominant lifecycle", statistics={"event_transition_count": 5760}
        ),
        data_limitations=("Synthetic aggregate fixture for tests only.",),
        sufficiency_status="context_only",
        evidence_references=(_reference("2"),),
    )
    handoff = EvidenceFindingV1(
        finding_id=HANDOFF_FINDING,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(
            name="high_frequency_or_repeated_group_handoff",
            definition="Synthetic aggregate handoff observation.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(
            label="synthetic group changes",
            filters={"group_transition": "G1 -> G2"},
        ),
        counts=EvidenceCountsV1(
            count_level="event_transition",
            observation_count=92,
            denominator=9500,
        ),
        measured_statistics={
            "repeated_visit_count": 92,
            "repeated_visit_rate": 1.0,
        },
        data_limitations=("Synthetic aggregate fixture for tests only.",),
        sufficiency_status="sufficient",
        evidence_references=(_reference("3"),),
    )
    return EvidenceContractV1(
        analysis_run_id=RUN_ID,
        analysis_configuration_version="test-v1",
        source_dataset=source,
        findings=(group, lifecycle, handoff),
    )


@pytest.fixture
def analysis_dir(tmp_path: Path) -> Path:
    """A complete, verifiable, synthetic governed aggregate package."""
    contract = _contract()
    (tmp_path / "evidence_contract.json").write_text(
        contract.model_dump_json(indent=2), encoding="utf-8"
    )
    (tmp_path / "process_overview.json").write_text(
        json.dumps({"case_count": 7500, "event_count": 60000}), encoding="utf-8"
    )
    comparison = {
        "classification": "observational_association_not_causal",
        "cohort_definition": "Synthetic aggregate fixture mirroring validated fields.",
        "cohorts": [
            {
                "cohort": "without_group_return",
                "incident_count": 6000,
                "observed_case_span_seconds": {"median": 777600.0},
            },
            {
                "cohort": "with_group_return",
                "incident_count": 1500,
                "observed_case_span_seconds": {"median": 1209600.5},
            },
        ],
        "distributional_differences": {
            "observed_case_span_seconds": {
                "median_difference": 432000.5,
                "median_ratio": 1.5555561985596709,
            }
        },
        "limitations": ["Synthetic aggregate fixture for tests only."],
    }
    (tmp_path / "handoff_cohort_comparison.json").write_text(
        json.dumps(comparison), encoding="utf-8"
    )
    semantic = {
        "analysis_run_id": RUN_ID,
        "source_dataset_sha256": "a" * 64,
        "handoff_validation": {
            "event_level_counts": {"repeated_group_visit_events": 3300},
            "incident_level_counts": {
                "incidents_with_repeated_group_visits": 1500,
                "total_incidents": 7500,
            },
        },
    }
    (tmp_path / "semantic_validation_report.json").write_text(
        json.dumps(semantic), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {
                "source_activity": "Completed",
                "target_activity": "Completed",
                "source_lifecycle": "Resolved",
                "target_lifecycle": "Closed",
                "source_group": "G1",
                "target_group": "G1",
                "event_transition_count": 5760,
            }
        ]
    ).to_parquet(tmp_path / "lifecycle_patterns.parquet", index=False)
    pd.DataFrame(
        [{"source_group": "G1", "target_group": "G2", "observation_count": 2}]
    ).to_parquet(tmp_path / "handoff_analysis.parquet", index=False)
    manifest = build_aggregate_manifest(tmp_path)
    (tmp_path / "aggregate_manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return tmp_path


def test_verify_governed_aggregates_requires_manifest(tmp_path: Path) -> None:
    with pytest.raises(DemoError, match="No aggregate manifest"):
        verify_governed_aggregates(tmp_path)


def test_verify_governed_aggregates_passes_on_valid_package(analysis_dir: Path) -> None:
    evidence = verify_governed_aggregates(analysis_dir)
    assert evidence.analysis_run_id == RUN_ID


def test_reset_demo_database_refuses_non_demo_paths(tmp_path: Path) -> None:
    with pytest.raises(DemoError, match="isolated \\*demo\\*"):
        reset_demo_database(tmp_path / "workflow.sqlite3")


def test_reset_demo_database_deletes_only_the_demo_file(tmp_path: Path) -> None:
    demo_db = tmp_path / "demo.sqlite3"
    demo_db.write_bytes(b"not a real sqlite file")
    assert reset_demo_database(demo_db) is True
    assert not demo_db.exists()
    assert reset_demo_database(demo_db) is False


def test_full_demo_walkthrough_approve_reject_idempotency_and_self_approval_block(
    analysis_dir: Path, tmp_path: Path
) -> None:
    database_path = tmp_path / "state" / "demo.sqlite3"
    summary = run_full_demo(analysis_dir, database_path, reset=True)

    step_by_name = {step["step"]: step for step in summary["steps"]}

    assert step_by_name["verify_governed_aggregates"]["analysis_run_id"] == RUN_ID
    assert step_by_name["self_approval_attempt_by_submitter"]["blocked_by_backend"] is True
    assert step_by_name["approve_recommendation"]["state"] == "EXECUTED"
    assert step_by_name["idempotent_decision_replay"]["state_matches"] is True
    assert step_by_name["idempotent_decision_replay"]["ticket_count_after_approval"] == 1
    assert step_by_name["reject_recommendation_no_ticket"]["state"] == "REJECTED"
    assert step_by_name["reject_recommendation_no_ticket"]["ticket_count_unchanged"] is True

    assert summary["approved_ticket_count"] == 1
    assert summary["final_ticket_count"] == 1
    assert summary["mode"] == "LOCAL_DEMO_NO_AZURE_NO_PAID_MODEL_NO_EXTERNAL_ACTION"

    audit_step = step_by_name["notifications_and_audit"]
    assert audit_step["failed"] == 0
    assert audit_step["audit_event_count"] > 0


def test_full_demo_is_repeatable_with_explicit_reset(
    analysis_dir: Path, tmp_path: Path
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    first = run_full_demo(analysis_dir, database_path, reset=True)
    second = run_full_demo(analysis_dir, database_path, reset=True)

    assert first["final_ticket_count"] == second["final_ticket_count"] == 1


# --- Bounded real-model end-to-end path, exercised with fake models only (no paid call) ---

from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

from process_optimizer.agents.provider import (  # noqa: E402
    DeterministicMockProvider,
    FoundryModelProvider,
)
from process_optimizer.agents.schemas import RecommendationCandidate  # noqa: E402
from process_optimizer.demo import real_model  # noqa: E402

_REAL_ENV = {
    "ALLOW_PAID_MODEL_CALLS": "true",
    "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/p",
    "MODEL_DEPLOYMENT_NAME": "gpt-4.1-mini",
    "MODEL_MAX_RETRIES": "0",
    "MODEL_MAX_CALLS": "8",
    "MODEL_MAX_OUTPUT_TOKENS": "2048",
    "MODEL_TPM_BUDGET": "15000",
}


class _ServiceLabelledProvider(DeterministicMockProvider):
    """Valid structured outputs labelled as if a service had answered (test double only)."""

    mode = "real_model"

    def __init__(self, response_model: str | None = "gpt-4.1-mini-2025-04-14") -> None:
        self.invocation_records: list[dict[str, Any]] = []
        self._response_model = response_model

    def _note(self, role: str) -> None:
        # Like the Foundry provider: report service usage for the run token budget
        # (about the size observed in the real runs of 2026-10-18).
        from process_optimizer.agents.provider import _report_usage

        _report_usage(3_000)
        self.invocation_records.append(
            {"role": role, "response_model": self._response_model, "error_category": None}
        )

    def select_findings(self, *args: Any, **kwargs: Any) -> Any:
        self._note("Process Analyst")
        return super().select_findings(*args, **kwargs)

    def plan_investigation(self, *args: Any, **kwargs: Any) -> Any:
        self._note("Root Cause Investigator")
        return super().plan_investigation(*args, **kwargs)

    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        self._note("Optimization Agent")
        return super().propose_optimizations(*args, **kwargs)


class _FabricatingProvider(_ServiceLabelledProvider):
    """Returns a recommendation whose first cited number is fabricated."""

    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        output = super().propose_optimizations(*args, **kwargs)
        if not output.recommendations:  # first call requests the simulation
            return output
        payload = output.recommendations[0].model_dump(mode="json")
        payload["observed_numerical_facts"][0]["value"] += 1000
        corrupted = RecommendationCandidate.model_validate(payload)
        return output.model_copy(update={"recommendations": (corrupted,)})


def test_real_model_limits_refuse_unbounded_configuration() -> None:
    assert real_model.check_limits(dict(_REAL_ENV)) == []
    assert real_model.MAX_MODEL_CALLS == 8
    for key, value in (
        ("ALLOW_PAID_MODEL_CALLS", "false"),
        ("MODEL_MAX_RETRIES", "2"),
        ("MODEL_MAX_CALLS", "9"),
        ("MODEL_MAX_OUTPUT_TOKENS", "4096"),
        ("MODEL_TPM_BUDGET", "20000"),
    ):
        assert real_model.check_limits({**_REAL_ENV, key: value}), key
    assert len(real_model.check_limits({})) == 7


def test_real_model_cli_refuses_without_opt_in_and_never_builds_a_model(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_build(timeout_seconds: float) -> Any:
        raise AssertionError("model must not be constructed")

    monkeypatch.setattr(FoundryModelProvider, "_build_model", staticmethod(fail_build))
    for key in _REAL_ENV:
        monkeypatch.delenv(key, raising=False)
    code = real_model.main(
        [
            "--analysis-dir", str(analysis_dir),
            "--expected-analysis-run-id", RUN_ID,
            "--allow-paid-model-call",
        ]
    )
    assert code == 2


def test_real_model_e2e_valid_output_reaches_pending_approval_without_ticket(
    analysis_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    evidence = real_model.run_real_model_e2e(
        _ServiceLabelledProvider(), analysis_dir, tmp_path / "real-demo.sqlite3", RUN_ID
    )

    assert evidence["execution_label"] == "REAL_MODEL_OUTPUT"
    assert evidence["genuine_model_response"] is True
    assert evidence["graph"]["all_required_nodes_visited"] is True
    assert evidence["graph"]["final_decision"] == "VALIDATED_RECOMMENDATIONS"
    assert evidence["workflow"]["submitted"] is True
    assert evidence["workflow"]["state"] == "PENDING_APPROVAL"
    assert evidence["workflow"]["self_approval_blocked"] is True
    assert evidence["workflow"]["ticket_count"] == 0
    assert "prompt" not in json.dumps(evidence["model_invocations"]).lower()


def test_real_model_e2e_without_service_model_name_is_not_genuine(
    analysis_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    evidence = real_model.run_real_model_e2e(
        _ServiceLabelledProvider(response_model=None),
        analysis_dir,
        tmp_path / "real-demo.sqlite3",
        RUN_ID,
    )
    assert evidence["execution_label"] == "REAL_MODEL_OUTPUT"
    assert evidence["genuine_model_response"] is False


def test_real_model_e2e_fabricated_output_is_rejected_and_creates_no_ticket(
    analysis_dir: Path, tmp_path: Path
) -> None:
    evidence = real_model.run_real_model_e2e(
        _FabricatingProvider(), analysis_dir, tmp_path / "real-demo.sqlite3", RUN_ID
    )

    assert evidence["graph"]["final_decision"] != "VALIDATED_RECOMMENDATIONS"
    assert "FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM" in json.dumps(evidence["validation"])
    assert evidence["workflow"] == {
        "submitted": False,
        "reason": "SERVER_REVALIDATION_REJECTED",
        "ticket_count": 0,
    }


def test_real_model_e2e_invalid_structured_output_fails_safely(
    analysis_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class InvalidModel:
        invocations = 0

        def with_structured_output(self, schema: Any, method: str, include_raw: bool) -> Any:
            return self

        def invoke(self, messages: Any, config: Any) -> dict[str, Any]:
            InvalidModel.invocations += 1
            return {"parsed": None, "raw": SimpleNamespace(response_metadata={})}

    monkeypatch.setattr(
        FoundryModelProvider, "_build_model", staticmethod(lambda timeout_seconds: InvalidModel())
    )
    evidence = real_model.run_real_model_e2e(
        FoundryModelProvider(), analysis_dir, tmp_path / "real-demo.sqlite3", RUN_ID
    )

    assert InvalidModel.invocations == 1
    assert evidence["graph"]["errors"] == ["MODEL_OUTPUT_INVALID"]
    assert evidence["model_invocations"]["failed_attempts"] == 1
    assert evidence["workflow"] == {
        "submitted": False,
        "reason": "NO_RECOMMENDATION",
        "ticket_count": 0,
    }


def test_foundry_provider_records_service_reported_model(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from process_optimizer.agents.schemas import AnalystOutput, EvidenceSelection
    from process_optimizer.agents.workflow import InvestigationRunner

    evidence = InvestigationRunner(analysis_dir, DeterministicMockProvider())._evidence
    parsed = AnalystOutput(
        selected_findings=(
            EvidenceSelection(finding_id=evidence.findings[0].finding_id, rationale="test"),
        )
    )

    class ServiceModel:
        def with_structured_output(self, schema: Any, method: str, include_raw: bool) -> Any:
            return self

        def invoke(self, messages: Any, config: Any) -> dict[str, Any]:
            raw = SimpleNamespace(
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={
                    "model_name": "gpt-4.1-mini-2025-04-14",
                    "system_fingerprint": "fp_test",
                },
            )
            return {"parsed": parsed, "raw": raw}

    monkeypatch.setattr(
        FoundryModelProvider, "_build_model", staticmethod(lambda timeout_seconds: ServiceModel())
    )
    provider = FoundryModelProvider()
    provider.select_findings("goal", "group_returns", evidence)

    record = provider.invocation_records[0]
    assert record["response_model"] == "gpt-4.1-mini-2025-04-14"
    assert record["system_fingerprint"] == "fp_test"
    assert record["token_usage"]["total_tokens"] == 15
