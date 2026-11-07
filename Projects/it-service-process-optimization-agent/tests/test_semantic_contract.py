"""Synthetic-only tests for Phase 3.5 semantics, evidence, and read-only tools."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from process_optimizer.process_mining.constants import (
    ACTIVITY,
    CASE_ID,
    EVENT_INDEX,
    GROUP,
    LIFECYCLE,
    TIMESTAMP,
)
from process_optimizer.process_mining.evidence_contract import (
    AggregateEvidenceReferenceV1,
    AggregatePopulationV1,
    DatasetSourceV1,
    EvidenceCountsV1,
    EvidenceFindingV1,
    MetricDefinitionV1,
    analysis_run_id,
)
from process_optimizer.process_mining.semantic_validation import (
    build_handoff_validation,
    build_lifecycle_patterns,
    build_transition_summaries,
)
from process_optimizer.tools.analytics import (
    AggregateAnalyticsTools,
    AggregateToolResult,
    BottleneckFindingsInput,
    CohortComparisonInput,
    EvidenceDetailsInput,
    GroupHandoffsInput,
    LifecyclePatternsInput,
    ProcessOverviewInput,
)


def test_lifecycle_patterns_keep_activity_and_lifecycle_separate() -> None:
    events = _semantic_events()

    patterns = build_lifecycle_patterns(events)
    completed = build_transition_summaries(events)[0]

    row = patterns.loc[
        (patterns["source_activity"] == "Completed")
        & (patterns["target_activity"] == "Completed")
    ].iloc[0]
    assert row["source_lifecycle"] == "Resolved"
    assert row["target_lifecycle"] == "Closed"
    assert row["event_transition_count"] == 1
    assert completed["event_transition_count"] == 1
    assert "does not establish" in completed["interpretation"]


def test_handoff_validation_separates_event_and_incident_counts() -> None:
    validation, comparison = build_handoff_validation(_semantic_events())

    assert validation["event_level_counts"]["group_change_events"] == 3
    assert validation["event_level_counts"]["repeated_group_visit_events"] == 1
    assert validation["event_level_counts"]["immediate_group_reversal_events"] == 1
    assert validation["incident_level_counts"]["incidents_with_repeated_group_visits"] == 1
    assert comparison["classification"] == "observational_association_not_causal"
    assert sum(row["incident_count"] for row in comparison["cohorts"]) == 2


def test_analysis_run_id_is_stable_and_configuration_sensitive() -> None:
    inputs = {"events": "a" * 64, "cases": "b" * 64}

    first = analysis_run_id("c" * 64, inputs, {"minimum_observations": 30})
    second = analysis_run_id("c" * 64, inputs, {"minimum_observations": 30})
    changed = analysis_run_id("c" * 64, inputs, {"minimum_observations": 40})

    assert first == second
    assert first != changed


def test_evidence_contract_is_immutable_and_rejects_sensitive_fields() -> None:
    finding = _evidence_finding()

    with pytest.raises(ValidationError):
        finding.finding_id = "finding_ffffffffffffffffffffffff"
    payload = finding.model_dump(mode="json")
    payload["aggregate_population"]["filters"] = {"case_id": "prohibited"}
    with pytest.raises(ValidationError, match="prohibited identifier"):
        EvidenceFindingV1.model_validate(payload)


def test_tool_inputs_bound_result_sizes_and_validate_identifiers() -> None:
    with pytest.raises(ValidationError):
        LifecyclePatternsInput(limit=101)
    with pytest.raises(ValidationError):
        EvidenceDetailsInput(identifier="case-123")

    with pytest.raises(ValidationError, match="prohibited identifier"):
        AggregateToolResult(
            returned_count=1,
            truncated=False,
            items=({"resource_identifier": "synthetic-resource"},),
        )


def test_read_only_tools_filter_only_precomputed_aggregates(tmp_path: Path) -> None:
    _write_tool_artifacts(tmp_path)
    # The tiny fixture counts are below k; small-group suppression is covered by
    # tests/test_guardrails.py, so this test disables it to check filtering only.
    tools = AggregateAnalyticsTools(tmp_path, small_group_threshold=1)

    overview = tools.get_process_overview(ProcessOverviewInput())
    findings = tools.get_bottleneck_findings(BottleneckFindingsInput(limit=1))
    handoffs = tools.analyze_group_handoffs(
        GroupHandoffsInput(source_group="G1", minimum_observations=2, limit=1)
    )
    lifecycle = tools.inspect_lifecycle_patterns(
        LifecyclePatternsInput(source_activity="Completed", limit=1)
    )
    cohorts = tools.compare_process_cohorts(CohortComparisonInput())
    evidence = tools.get_evidence_details(
        EvidenceDetailsInput(identifier="finding_aaaaaaaaaaaaaaaaaaaaaaaa")
    )

    assert overview.returned_count == 1
    assert findings.returned_count == 1
    assert handoffs.returned_count == 1
    assert lifecycle.returned_count == 1
    assert cohorts.items[0]["classification"] == "observational_association_not_causal"
    assert evidence.returned_count == 1
    serialized = (
        handoffs.model_dump_json()
        + lifecycle.model_dump_json()
        + evidence.model_dump_json()
    )
    assert "case_id" not in serialized
    assert "org:resource" not in serialized


def _semantic_events() -> pd.DataFrame:
    base = pd.Timestamp("2020-01-01T00:00:00Z")
    return pd.DataFrame(
        [
            ("c1", 0, "Completed", "Resolved", "G1", base),
            ("c1", 1, "Completed", "Closed", "G2", pd.Timestamp("2020-01-01T00:00:10Z")),
            ("c1", 2, "Accepted", "In Progress", "G1", pd.Timestamp("2020-01-01T00:00:20Z")),
            ("c2", 0, "Queued", "Awaiting Assignment", "G1", base),
            ("c2", 1, "Accepted", "In Progress", "G2", pd.Timestamp("2020-01-01T00:00:05Z")),
        ],
        columns=[CASE_ID, EVENT_INDEX, ACTIVITY, LIFECYCLE, GROUP, TIMESTAMP],
    )


def _evidence_finding() -> EvidenceFindingV1:
    return EvidenceFindingV1(
        finding_id="finding_aaaaaaaaaaaaaaaaaaaaaaaa",
        source_dataset=DatasetSourceV1(
            name="Synthetic unit-test fixture",
            source_url="https://example.invalid/synthetic",
            sha256="a" * 64,
        ),
        analysis_run_id="run_bbbbbbbbbbbbbbbbbbbbbbbb",
        analysis_configuration_version="test-v1",
        metric=MetricDefinitionV1(name="test_metric", definition="Test only.", unit="count"),
        aggregate_population=AggregatePopulationV1(label="synthetic aggregates"),
        counts=EvidenceCountsV1(count_level="incident", observation_count=2),
        measured_statistics={"median": 1.0},
        data_limitations=("Synthetic fixture only.",),
        sufficiency_status="context_only",
        evidence_references=(
            AggregateEvidenceReferenceV1(
                reference_id="evidence_ref_cccccccccccccccccccccccc",
                artifact="aggregate.parquet",
                artifact_sha256="d" * 64,
                selector={"activity": "Completed"},
                metric="test aggregate",
            ),
        ),
    )


def _write_tool_artifacts(path: Path) -> None:
    (path / "process_overview.json").write_text(
        json.dumps({"case_count": 2, "event_count": 5}), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {"source_group": "G1", "target_group": "G2", "observation_count": 3},
            {"source_group": "G2", "target_group": "G1", "observation_count": 1},
        ]
    ).to_parquet(path / "handoff_analysis.parquet", index=False)
    pd.DataFrame(
        [
            {
                "source_activity": "Completed",
                "target_activity": "Completed",
                "source_lifecycle": "Resolved",
                "target_lifecycle": "Closed",
                "event_transition_count": 3,
            }
        ]
    ).to_parquet(path / "lifecycle_patterns.parquet", index=False)
    comparison = {
        "classification": "observational_association_not_causal",
        "cohort_definition": "Synthetic test cohorts.",
        "cohorts": [
            {
                "cohort": "with_group_return",
                "incident_count": 1,
                "observed_case_span_seconds": {"median": 10.0},
            },
            {
                "cohort": "without_group_return",
                "incident_count": 1,
                "observed_case_span_seconds": {"median": 5.0},
            },
        ],
        "distributional_differences": {
            "observed_case_span_seconds": {"median_difference": 5.0, "median_ratio": 2.0}
        },
        "limitations": ["Synthetic fixture only."],
    }
    (path / "handoff_cohort_comparison.json").write_text(
        json.dumps(comparison), encoding="utf-8"
    )
    finding = _evidence_finding().model_dump(mode="json")
    (path / "evidence_contract.json").write_text(
        json.dumps({"findings": [finding]}), encoding="utf-8"
    )
