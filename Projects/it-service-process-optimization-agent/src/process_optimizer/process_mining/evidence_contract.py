"""Immutable, versioned aggregate evidence contract for future agent access."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from process_optimizer.process_mining.models import EvidencePackage

EVIDENCE_SCHEMA_VERSION = "1.0.0"
ANALYSIS_CONFIGURATION_VERSION = "phase3.5-default-v1"
DATASET_NAME = "BPI Challenge 2013 Incidents"
DATASET_URL = "https://data.4tu.nl/datasets/0fc5c579-e544-4fab-9143-fab1f5192432"


class ImmutableModel(BaseModel):
    """Base for immutable contract records."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetSourceV1(ImmutableModel):
    """Provenance for the dataset underlying an evidence package."""

    name: str
    source_url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MetricDefinitionV1(ImmutableModel):
    """Machine-readable metric semantics."""

    name: str
    definition: str
    unit: str


class AggregatePopulationV1(ImmutableModel):
    """Aggregate population description without record identifiers."""

    label: str
    filters: dict[str, str] = Field(default_factory=dict)


class EvidenceCountsV1(ImmutableModel):
    """Counts with explicit level and optional ratio components."""

    count_level: Literal["event_transition", "incident", "aggregate_pattern"]
    observation_count: int = Field(ge=0)
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ratio(self) -> EvidenceCountsV1:
        if self.numerator is not None and self.denominator is None:
            raise ValueError("A numerator requires a denominator")
        if (
            self.numerator is not None
            and self.denominator is not None
            and self.numerator > self.denominator
        ):
            raise ValueError("The numerator cannot exceed the denominator")
        return self


class ComparisonGroupV1(ImmutableModel):
    """Named baseline or comparison group."""

    label: str
    statistics: dict[str, float | int | str | None]


class AggregateEvidenceReferenceV1(ImmutableModel):
    """Stable pointer to one row or section in a precomputed aggregate artifact."""

    reference_id: str = Field(pattern=r"^evidence_ref_[0-9a-f]{24}$")
    artifact: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector: dict[str, str]
    metric: str


class EvidenceFindingV1(ImmutableModel):
    """One aggregate, non-causal observation for future agent consumption."""

    schema_version: Literal["1.0.0"] = EVIDENCE_SCHEMA_VERSION
    finding_id: str
    source_dataset: DatasetSourceV1
    analysis_run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    analysis_configuration_version: str
    metric: MetricDefinitionV1
    aggregate_population: AggregatePopulationV1
    counts: EvidenceCountsV1
    measured_statistics: dict[str, float | int | str | None]
    baseline_or_comparison: ComparisonGroupV1 | None = None
    data_limitations: tuple[str, ...]
    sufficiency_status: Literal["sufficient", "insufficient", "context_only"]
    interpretation: Literal["observational_non_causal"] = "observational_non_causal"
    evidence_references: tuple[AggregateEvidenceReferenceV1, ...]

    @model_validator(mode="after")
    def reject_sensitive_identifiers(self) -> EvidenceFindingV1:
        serialized = self.model_dump_json().lower()
        forbidden = ('"case_id"', '"event_id"', "org:resource", "resource_identifier")
        if any(token in serialized for token in forbidden):
            raise ValueError("Evidence contains a prohibited identifier field")
        return self


class EvidenceContractV1(ImmutableModel):
    """Versioned collection of evidence findings for one deterministic run."""

    schema_version: Literal["1.0.0"] = EVIDENCE_SCHEMA_VERSION
    analysis_run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    analysis_configuration_version: str
    source_dataset: DatasetSourceV1
    contract_interpretation: Literal["aggregate_observations_not_confirmed_causes"] = (
        "aggregate_observations_not_confirmed_causes"
    )
    findings: tuple[EvidenceFindingV1, ...]


def analysis_run_id(
    dataset_sha256: str,
    verified_input_hashes: dict[str, str],
    configuration: dict[str, Any],
) -> str:
    """Derive a stable run identity from governed inputs and deterministic configuration."""
    payload = {
        "dataset_sha256": dataset_sha256,
        "verified_input_hashes": verified_input_hashes,
        "analysis_configuration_version": ANALYSIS_CONFIGURATION_VERSION,
        "configuration": configuration,
    }
    return f"run_{_digest(payload)[:24]}"


def build_evidence_contract(
    package: EvidencePackage,
    transition_summaries: list[dict[str, Any]],
    cohort_comparison: dict[str, Any],
    *,
    run_id: str,
    artifact_hashes: dict[str, str],
) -> EvidenceContractV1:
    """Enrich Phase 3 observations and Phase 3.5 validations into contract V1."""
    source = DatasetSourceV1(
        name=DATASET_NAME,
        source_url=DATASET_URL,
        sha256=package.dataset_sha256,
    )
    findings: list[EvidenceFindingV1] = []
    for finding in package.findings:
        references = tuple(
            _reference(
                run_id,
                reference.artifact,
                artifact_hashes[reference.artifact],
                reference.metric,
                reference.selector,
            )
            for reference in finding.evidence_references
        )
        count_level: Literal["event_transition", "incident", "aggregate_pattern"] = (
            "event_transition"
        )
        findings.append(
            EvidenceFindingV1(
                finding_id=finding.finding_id,
                source_dataset=source,
                analysis_run_id=run_id,
                analysis_configuration_version=ANALYSIS_CONFIGURATION_VERSION,
                metric=MetricDefinitionV1(
                    name=finding.metric_name,
                    definition=finding.metric_definition,
                    unit="seconds_and_counts",
                ),
                aggregate_population=AggregatePopulationV1(
                    label=finding.affected_population,
                    filters={
                        key: value
                        for key, value in {
                            "process_step": finding.affected_process_step,
                            "group_transition": finding.affected_group,
                        }.items()
                        if value is not None
                    },
                ),
                counts=EvidenceCountsV1(
                    count_level=count_level,
                    observation_count=finding.observation_count,
                    denominator=finding.denominator,
                ),
                measured_statistics=finding.measured_values,
                baseline_or_comparison=ComparisonGroupV1(
                    label=str(finding.comparison_baseline.get("name", "configured baseline")),
                    statistics={
                        key: value
                        for key, value in finding.comparison_baseline.items()
                        if key != "name"
                    },
                ),
                data_limitations=tuple(finding.limitations),
                sufficiency_status="sufficient",
                evidence_references=references,
            )
        )

    for summary in transition_summaries:
        if int(summary["event_transition_count"]) == 0:
            continue
        findings.append(
            _lifecycle_finding(
                summary,
                source=source,
                run_id=run_id,
                artifact_hash=artifact_hashes["lifecycle_patterns.parquet"],
            )
        )
    findings.append(
        _cohort_finding(
            cohort_comparison,
            source=source,
            run_id=run_id,
            artifact_hash=artifact_hashes["handoff_cohort_comparison.json"],
        )
    )
    findings.sort(key=lambda item: item.finding_id)
    return EvidenceContractV1(
        analysis_run_id=run_id,
        analysis_configuration_version=ANALYSIS_CONFIGURATION_VERSION,
        source_dataset=source,
        findings=tuple(findings),
    )


def _lifecycle_finding(
    summary: dict[str, Any],
    *,
    source: DatasetSourceV1,
    run_id: str,
    artifact_hash: str,
) -> EvidenceFindingV1:
    source_activity = str(summary["source_activity"])
    target_activity = str(summary["target_activity"])
    transition = f"{source_activity} -> {target_activity}"
    dominant = summary["lifecycle_patterns"][0]
    finding_id = f"finding_{_digest({'run': run_id, 'semantic_transition': transition})[:24]}"
    selector = {"source_activity": source_activity, "target_activity": target_activity}
    reference = _reference(
        run_id,
        "lifecycle_patterns.parquet",
        artifact_hash,
        "activity/lifecycle/group transition distribution",
        selector,
    )
    return EvidenceFindingV1(
        finding_id=finding_id,
        source_dataset=source,
        analysis_run_id=run_id,
        analysis_configuration_version=ANALYSIS_CONFIGURATION_VERSION,
        metric=MetricDefinitionV1(
            name="lifecycle_decomposition_of_activity_transition",
            definition=(
                "Consecutive event transitions decomposed by unchanged activity, lifecycle, "
                "and group attributes."
            ),
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(
            label="events containing the selected consecutive activity transition",
            filters=selector,
        ),
        counts=EvidenceCountsV1(
            count_level="event_transition",
            observation_count=int(summary["event_transition_count"]),
            numerator=int(dominant["event_transition_count"]),
            denominator=int(summary["event_transition_count"]),
        ),
        measured_statistics={
            "incident_count": int(summary["incident_count"]),
            "same_group_event_count": int(summary["same_group_event_count"]),
            "group_change_event_count": int(summary["group_change_event_count"]),
            "elapsed_seconds_median": summary["elapsed_seconds"]["median"],
            "elapsed_seconds_p75": summary["elapsed_seconds"]["p75"],
            "elapsed_seconds_p90": summary["elapsed_seconds"]["p90"],
            "dominant_source_lifecycle": str(dominant["source_lifecycle"]),
            "dominant_target_lifecycle": str(dominant["target_lifecycle"]),
        },
        baseline_or_comparison=ComparisonGroupV1(
            label="dominant lifecycle pair within the selected activity transition",
            statistics={
                "event_transition_count": int(dominant["event_transition_count"]),
                "incident_count": int(dominant["incident_count"]),
            },
        ),
        data_limitations=(
            "Lifecycle labels are preserved source values and are not mapped to business outcomes.",
            "Repeated Completed activities do not establish repeated closure or rework.",
            "Elapsed time includes waiting and inactive time.",
        ),
        sufficiency_status="context_only",
        evidence_references=(reference,),
    )


def _cohort_finding(
    comparison: dict[str, Any],
    *,
    source: DatasetSourceV1,
    run_id: str,
    artifact_hash: str,
) -> EvidenceFindingV1:
    cohorts = {row["cohort"]: row for row in comparison["cohorts"]}
    with_return = cohorts["with_group_return"]
    without_return = cohorts["without_group_return"]
    finding_id = f"finding_{_digest({'run': run_id, 'cohort': 'group_returns'})[:24]}"
    reference = _reference(
        run_id,
        "handoff_cohort_comparison.json",
        artifact_hash,
        "incident cohorts with and without repeated group visits",
        {"cohort_dimension": "has_repeated_group_visit"},
    )
    return EvidenceFindingV1(
        finding_id=finding_id,
        source_dataset=source,
        analysis_run_id=run_id,
        analysis_configuration_version=ANALYSIS_CONFIGURATION_VERSION,
        metric=MetricDefinitionV1(
            name="observed_span_association_with_group_returns",
            definition=(
                "Difference in observed case-span distributions between incident cohorts "
                "with and without a repeated group visit."
            ),
            unit="incidents_and_seconds",
        ),
        aggregate_population=AggregatePopulationV1(
            label="all incidents partitioned by repeated group-visit presence"
        ),
        counts=EvidenceCountsV1(
            count_level="incident",
            observation_count=int(with_return["incident_count"]),
            numerator=int(with_return["incident_count"]),
            denominator=int(with_return["incident_count"] + without_return["incident_count"]),
        ),
        measured_statistics={
            "with_return_incident_count": int(with_return["incident_count"]),
            "without_return_incident_count": int(without_return["incident_count"]),
            "with_return_span_median_seconds": with_return["observed_case_span_seconds"]["median"],
            "without_return_span_median_seconds": without_return[
                "observed_case_span_seconds"
            ]["median"],
            "span_median_ratio": comparison["distributional_differences"][
                "observed_case_span_seconds"
            ]["median_ratio"],
        },
        baseline_or_comparison=ComparisonGroupV1(
            label="incidents without repeated group visits",
            statistics={
                "incident_count": int(without_return["incident_count"]),
                "observed_case_span_median_seconds": without_return[
                    "observed_case_span_seconds"
                ]["median"],
            },
        ),
        data_limitations=tuple(comparison["limitations"]),
        sufficiency_status="sufficient",
        evidence_references=(reference,),
    )


def _reference(
    run_id: str,
    artifact: str,
    artifact_sha256: str,
    metric: str,
    selector: dict[str, str],
) -> AggregateEvidenceReferenceV1:
    payload = {
        "run_id": run_id,
        "artifact": artifact,
        "artifact_sha256": artifact_sha256,
        "metric": metric,
        "selector": selector,
    }
    return AggregateEvidenceReferenceV1(
        reference_id=f"evidence_ref_{_digest(payload)[:24]}",
        artifact=artifact,
        artifact_sha256=artifact_sha256,
        selector=selector,
        metric=metric,
    )


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
