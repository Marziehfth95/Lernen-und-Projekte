"""Deterministic tests for the Phase 5 hypothetical simulation engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from process_optimizer.process_mining.evidence_contract import (
    AggregateEvidenceReferenceV1,
    AggregatePopulationV1,
    DatasetSourceV1,
    EvidenceContractV1,
    EvidenceCountsV1,
    EvidenceFindingV1,
    MetricDefinitionV1,
)
from process_optimizer.simulation import (
    ScenarioAssumptionV1,
    SimulationRequestV1,
    WhatIfSimulationEngine,
)

RUN_ID = "run_aaaaaaaaaaaaaaaaaaaaaaaa"
FINDING_ID = "finding_bbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def simulation_dir(tmp_path: Path) -> Path:
    _write_simulation_artifacts(tmp_path, repeated_visit_count=100)
    return tmp_path


def test_default_scenarios_use_event_level_formula_and_refuse_time_claims(
    simulation_dir: Path,
) -> None:
    engine = WhatIfSimulationEngine(simulation_dir)
    result = engine.simulate(_request())

    assert result.status == "ESTIMATED"
    assert result.classification == "hypothetical_non_causal"
    assert result.baseline is not None
    assert result.baseline.selected_repeated_group_visit_events == 100
    assert result.baseline.all_repeated_group_visit_events == 300
    assert result.baseline.incidents_with_repeated_group_visits == 50
    assert result.baseline.total_incidents == 500
    assert [
        scenario.hypothetical_repeated_visit_events_avoided.value
        for scenario in result.scenarios
    ] == [1.0, 7.5, 25.0]
    assert all(
        scenario.elapsed_time_impact.status == "NOT_ESTIMABLE"
        for scenario in result.scenarios
    )
    assert all(scenario.sla_impact.status == "NOT_ESTIMABLE" for scenario in result.scenarios)
    assert all(
        scenario.affected_incident_count.status == "NOT_ESTIMABLE"
        for scenario in result.scenarios
    )


def test_simulation_is_deterministic_and_references_evidence(simulation_dir: Path) -> None:
    engine = WhatIfSimulationEngine(simulation_dir)

    first = engine.simulate(_request())
    second = engine.simulate(_request())

    assert first.model_dump_json() == second.model_dump_json()
    assert first.simulation_id == second.simulation_id
    assert {reference.artifact for reference in first.evidence_references} == {
        "handoff_analysis.parquet",
        "semantic_validation_report.json",
    }


def test_configurable_assumptions_drive_sensitivity(simulation_dir: Path) -> None:
    assumptions = (
        ScenarioAssumptionV1(
            name="conservative", eligible_event_share=0.20, assumed_reduction_share=0.10
        ),
        ScenarioAssumptionV1(
            name="moderate", eligible_event_share=0.40, assumed_reduction_share=0.25
        ),
        ScenarioAssumptionV1(
            name="optimistic", eligible_event_share=0.80, assumed_reduction_share=0.50
        ),
    )
    result = WhatIfSimulationEngine(simulation_dir).simulate(
        _request(assumptions=assumptions)
    )

    assert result.sensitivity_analysis.minimum_hypothetical_events_avoided == 2.0
    assert result.sensitivity_analysis.maximum_hypothetical_events_avoided == 40.0
    assert "not a confidence" in result.sensitivity_analysis.interpretation


def test_invalid_simulation_parameters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ScenarioAssumptionV1(
            name="moderate", eligible_event_share=1.1, assumed_reduction_share=0.5
        )
    with pytest.raises(ValidationError, match="exactly once"):
        SimulationRequestV1(
            analysis_run_id=RUN_ID,
            selected_finding_ids=(FINDING_ID,),
            assumptions=(
                ScenarioAssumptionV1(
                    name="conservative",
                    eligible_event_share=0.1,
                    assumed_reduction_share=0.1,
                ),
            )
            * 3,
        )


def test_missing_verified_baseline_returns_not_estimable(tmp_path: Path) -> None:
    _write_simulation_artifacts(tmp_path, repeated_visit_count=None)
    result = WhatIfSimulationEngine(tmp_path).simulate(_request())

    assert result.status == "NOT_ESTIMABLE"
    assert result.baseline is None
    assert not result.scenarios
    assert any("lack repeated-visit" in reason for reason in result.not_estimable_reasons)


def test_mismatched_analysis_run_returns_not_estimable(simulation_dir: Path) -> None:
    result = WhatIfSimulationEngine(simulation_dir).simulate(
        SimulationRequestV1(
            analysis_run_id="run_cccccccccccccccccccccccc",
            selected_finding_ids=(FINDING_ID,),
        )
    )

    assert result.status == "NOT_ESTIMABLE"
    assert any("does not match" in reason for reason in result.not_estimable_reasons)


def _request(
    assumptions: tuple[ScenarioAssumptionV1, ...] | None = None,
) -> SimulationRequestV1:
    values = {
        "analysis_run_id": RUN_ID,
        "selected_finding_ids": (FINDING_ID,),
    }
    if assumptions is not None:
        values["assumptions"] = assumptions
    return SimulationRequestV1.model_validate(values)


def _write_simulation_artifacts(path: Path, repeated_visit_count: int | None) -> None:
    source = DatasetSourceV1(
        name="Synthetic aggregate simulation fixture",
        source_url="https://example.invalid/synthetic",
        sha256="d" * 64,
    )
    statistics: dict[str, float | int | str | None] = {"repeated_visit_rate": 0.5}
    if repeated_visit_count is not None:
        statistics["repeated_visit_count"] = repeated_visit_count
    finding = EvidenceFindingV1(
        finding_id=FINDING_ID,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="synthetic-v1",
        metric=MetricDefinitionV1(
            name="high_frequency_or_repeated_group_handoff",
            definition="Synthetic aggregate handoff observation.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(label="synthetic transition"),
        counts=EvidenceCountsV1(
            count_level="event_transition", observation_count=200, denominator=1000
        ),
        measured_statistics=statistics,
        data_limitations=("Synthetic fixture only.",),
        sufficiency_status="sufficient",
        evidence_references=(
            AggregateEvidenceReferenceV1(
                reference_id="evidence_ref_eeeeeeeeeeeeeeeeeeeeeeee",
                artifact="handoff_analysis.parquet",
                artifact_sha256="f" * 64,
                selector={"fixture": "synthetic"},
                metric="synthetic repeated handoff",
            ),
        ),
    )
    contract = EvidenceContractV1(
        analysis_run_id=RUN_ID,
        analysis_configuration_version="synthetic-v1",
        source_dataset=source,
        findings=(finding,),
    )
    (path / "evidence_contract.json").write_text(
        contract.model_dump_json(indent=2), encoding="utf-8"
    )
    semantic = {
        "analysis_run_id": RUN_ID,
        "source_dataset_sha256": "d" * 64,
        "handoff_validation": {
            "event_level_counts": {"repeated_group_visit_events": 300},
            "incident_level_counts": {
                "incidents_with_repeated_group_visits": 50,
                "total_incidents": 500,
            },
        },
    }
    (path / "semantic_validation_report.json").write_text(
        json.dumps(semantic), encoding="utf-8"
    )
