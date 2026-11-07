"""Synthetic-only tests for deterministic Phase 3 process metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from process_optimizer.process_mining.bottlenecks import detect_bottlenecks
from process_optimizer.process_mining.constants import (
    ACTIVITY,
    CASE_ID,
    EVENT_ID,
    EVENT_INDEX,
    GROUP,
    LIFECYCLE,
    ORGANIZATION_INVOLVED,
    PRODUCT,
    TIMESTAMP,
)
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.process_mining.metrics import (
    build_directly_follows,
    build_handoff_analysis,
    build_process_overview,
    build_variants,
    calculate_case_spans,
    order_events,
)
from process_optimizer.process_mining.models import BottleneckConfig, TimePeriod
from process_optimizer.process_mining.pipeline import run_process_mining


def test_event_ordering_uses_event_index_for_tied_timestamps() -> None:
    _, events = _synthetic_frames()
    shuffled = events.sample(frac=1, random_state=7).reset_index(drop=True)

    ordered = order_events(shuffled)
    variants = build_variants(shuffled)

    first_case = ordered.loc[ordered[CASE_ID] == "synthetic-case-1", ACTIVITY].tolist()
    assert first_case == ["A", "B", "A"]
    assert variants.iloc[0]["case_count"] == 1
    sequences = {tuple(value) for value in variants["activity_sequence"]}
    assert ("A", "B", "A") in sequences


def test_single_event_case_has_zero_observed_span() -> None:
    _, events = _synthetic_frames()

    spans = calculate_case_spans(events).set_index(CASE_ID)

    assert spans.loc["synthetic-case-3", "observed_case_span_seconds"] == 0.0


def test_throughput_is_last_minus_first_observed_timestamp() -> None:
    cases, events = _synthetic_frames()

    overview = build_process_overview(cases, events)
    spans = calculate_case_spans(events).set_index(CASE_ID)

    assert spans.loc["synthetic-case-1", "observed_case_span_seconds"] == 10.0
    assert spans.loc["synthetic-case-2", "observed_case_span_seconds"] == 20.0
    assert overview["observed_case_span_seconds"]["median"] == 10.0


def test_process_variant_frequencies_are_exact() -> None:
    _, events = _synthetic_frames()

    variants = build_variants(events)

    observed = {
        tuple(row.activity_sequence): row.case_count for row in variants.itertuples(index=False)
    }
    assert observed == {("A", "B", "A"): 1, ("A", "B"): 1, ("C",): 1}
    assert variants["coverage"].sum() == 1.0


def test_directly_follows_counts_and_elapsed_distributions() -> None:
    _, events = _synthetic_frames()

    directly_follows = build_directly_follows(events).set_index(
        ["source_activity", "target_activity"]
    )

    assert directly_follows.loc[("A", "B"), "observation_count"] == 2
    assert directly_follows.loc[("A", "B"), "elapsed_seconds_median"] == 10.0
    assert directly_follows.loc[("B", "A"), "observation_count"] == 1


def test_group_handoffs_include_returns_to_visited_groups() -> None:
    _, events = _synthetic_frames()

    result = build_handoff_analysis(events)
    handoffs = result.transitions.set_index(["source_group", "target_group"])

    assert handoffs.loc[("G1", "G2"), "observation_count"] == 2
    assert handoffs.loc[("G2", "G1"), "repeated_visit_count"] == 1
    assert result.same_group_consecutive_event_count == 0


def test_missing_group_values_are_unclassified_not_imputed() -> None:
    _, events = _synthetic_frames()
    events.loc[events[EVENT_ID] == "synthetic-event-5", GROUP] = None

    result = build_handoff_analysis(events)

    assert result.unclassified_missing_group_count == 1
    assert not result.transitions["source_group"].isna().any()
    assert not result.transitions["target_group"].isna().any()


def test_bottleneck_ranking_and_minimum_sample_size() -> None:
    directly_follows = _bottleneck_dfg()
    handoffs = _empty_handoffs_for_test()
    config = BottleneckConfig(minimum_observations=30, maximum_findings=10)

    package = detect_bottlenecks(
        directly_follows,
        handoffs,
        {"observation_count": 200, "median": 10.0, "p90": 100.0},
        dataset_sha256="a" * 64,
        time_period=TimePeriod(
            start_utc="2020-01-01T00:00:00+00:00",
            end_utc="2020-01-02T00:00:00+00:00",
        ),
        config=config,
    )

    assert [finding.affected_process_step for finding in package.findings] == ["Slow -> End"]
    assert package.findings[0].measured_values["median_to_baseline_ratio"] == 3.0
    assert package.insufficient_sample_summary["directly_follows_patterns_below_minimum"] == 1


def test_evidence_package_is_stable_and_contains_no_case_or_resource_identifiers() -> None:
    config = BottleneckConfig(minimum_observations=30)
    arguments = {
        "directly_follows": _bottleneck_dfg(),
        "handoffs": _empty_handoffs_for_test(),
        "duration_baseline": {"observation_count": 200, "median": 10.0, "p90": 100.0},
        "dataset_sha256": "b" * 64,
        "time_period": TimePeriod(
            start_utc="2020-01-01T00:00:00+00:00",
            end_utc="2020-01-02T00:00:00+00:00",
        ),
        "config": config,
    }

    first = detect_bottlenecks(**arguments)
    second = detect_bottlenecks(**arguments)
    serialized = first.model_dump_json()

    assert first.findings[0].finding_id == second.findings[0].finding_id
    assert first.findings[0].classification == "observation_not_confirmed_cause"
    assert "original_event__org:resource" not in serialized
    assert '"case_id"' not in serialized
    assert '"event_id"' not in serialized


def test_complete_analysis_is_reproducible(tmp_path: Path) -> None:
    cases, events = _synthetic_frames()
    input_dir = tmp_path / "phase2"
    input_dir.mkdir()
    _write_phase2_inputs(input_dir, cases, events)

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = run_process_mining(input_dir, first_dir, BottleneckConfig(minimum_observations=2))
    second = run_process_mining(input_dir, second_dir, BottleneckConfig(minimum_observations=2))

    assert first == second.__class__(
        output_dir=first_dir,
        case_count=second.case_count,
        event_count=second.event_count,
        variant_count=second.variant_count,
        finding_count=second.finding_count,
        semantic_pattern_count=second.semantic_pattern_count,
        evidence_contract_finding_count=second.evidence_contract_finding_count,
    )
    filenames = [
        "process_overview.json",
        "process_variants.parquet",
        "directly_follows.parquet",
        "handoff_analysis.parquet",
        "bottleneck_findings.json",
        "process_mining_report.json",
        "lifecycle_patterns.parquet",
        "handoff_cohort_comparison.json",
        "semantic_validation_report.json",
        "evidence_contract.json",
    ]
    assert {
        name: _sha256(first_dir / name) for name in filenames
    } == {name: _sha256(second_dir / name) for name in filenames}
    contract = EvidenceContractV1.model_validate_json(
        (first_dir / "evidence_contract.json").read_text(encoding="utf-8")
    )
    for finding in contract.findings:
        for reference in finding.evidence_references:
            assert _sha256(first_dir / reference.artifact) == reference.artifact_sha256
    serialized = contract.model_dump_json().lower()
    assert '"case_id"' not in serialized
    assert '"event_id"' not in serialized
    assert "org:resource" not in serialized


def _synthetic_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    cases = pd.DataFrame(
        {
            CASE_ID: ["synthetic-case-1", "synthetic-case-2", "synthetic-case-3"],
            "event_count": [3, 2, 1],
        }
    )
    base = pd.Timestamp("2020-01-01T00:00:00Z")
    records = [
        ("synthetic-event-2", "synthetic-case-1", 1, "B", "L2", "G2", base, "P1", "O1"),
        ("synthetic-event-1", "synthetic-case-1", 0, "A", "L1", "G1", base, "P1", "O1"),
        (
            "synthetic-event-3",
            "synthetic-case-1",
            2,
            "A",
            "L1",
            "G1",
            pd.Timestamp("2020-01-01T00:00:10Z"),
            "P1",
            "O2",
        ),
        ("synthetic-event-4", "synthetic-case-2", 0, "A", "L1", "G1", base, "P2", "O1"),
        (
            "synthetic-event-5",
            "synthetic-case-2",
            1,
            "B",
            "L2",
            "G2",
            pd.Timestamp("2020-01-01T00:00:20Z"),
            "P2",
            "O1",
        ),
        ("synthetic-event-6", "synthetic-case-3", 0, "C", "L3", "G3", base, "P3", "O3"),
    ]
    events = pd.DataFrame(
        records,
        columns=[
            EVENT_ID,
            CASE_ID,
            EVENT_INDEX,
            ACTIVITY,
            LIFECYCLE,
            GROUP,
            TIMESTAMP,
            PRODUCT,
            ORGANIZATION_INVOLVED,
        ],
    )
    return cases, events


def _bottleneck_dfg() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_activity": "Slow",
                "target_activity": "End",
                "observation_count": 50,
                "elapsed_seconds_median": 30.0,
                "elapsed_seconds_p75": 80.0,
                "elapsed_seconds_p90": 160.0,
            },
            {
                "source_activity": "Small",
                "target_activity": "End",
                "observation_count": 29,
                "elapsed_seconds_median": 100.0,
                "elapsed_seconds_p75": 150.0,
                "elapsed_seconds_p90": 250.0,
            },
        ]
    )


def _empty_handoffs_for_test() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_group",
            "target_group",
            "observation_count",
            "repeated_visit_count",
            "repeated_visit_rate",
            "source_handoff_share",
            "elapsed_seconds_median",
            "elapsed_seconds_p75",
            "elapsed_seconds_p90",
        ]
    )


def _write_phase2_inputs(input_dir: Path, cases: pd.DataFrame, events: pd.DataFrame) -> None:
    paths = {
        "cases": input_dir / "cases.parquet",
        "events": input_dir / "events.parquet",
        "schema_profile": input_dir / "schema_profile.json",
        "data_quality_report": input_dir / "data_quality_report.json",
    }
    cases.to_parquet(paths["cases"], index=False)
    events.to_parquet(paths["events"], index=False)
    schema = {
        "event": {"parquet_schema": {column: str(dtype) for column, dtype in events.dtypes.items()}}
    }
    paths["schema_profile"].write_text(json.dumps(schema), encoding="utf-8")
    paths["data_quality_report"].write_text(
        json.dumps({"overall_status": "pass"}), encoding="utf-8"
    )
    manifest = {
        "pipeline_version": "synthetic-test",
        "source": {"sha256": "c" * 64},
        "outputs": {
            name: {"filename": path.name, "sha256": _sha256(path)}
            for name, path in paths.items()
        },
        "record_counts": {"cases": len(cases), "events": len(events)},
        "validation_results": {"overall_status": "pass"},
        "discovered_schema": schema,
    }
    (input_dir / "ingestion_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
