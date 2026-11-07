"""Aggregate evidence contracts for future agent consumption."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BottleneckConfig(BaseModel):
    """Transparent thresholds for deterministic candidate detection."""

    model_config = ConfigDict(frozen=True)

    minimum_observations: int = Field(default=30, ge=2)
    duration_median_ratio_threshold: float = Field(default=2.0, gt=1.0)
    duration_p90_ratio_threshold: float = Field(default=1.5, gt=1.0)
    handoff_source_share_threshold: float = Field(default=0.20, gt=0.0, le=1.0)
    repeated_visit_rate_threshold: float = Field(default=0.20, gt=0.0, le=1.0)
    maximum_findings: int = Field(default=50, ge=1, le=500)


class TimePeriod(BaseModel):
    """Observed UTC event period covered by a finding."""

    model_config = ConfigDict(frozen=True)

    start_utc: str
    end_utc: str


class EvidenceReference(BaseModel):
    """Pointer to an aggregate, reproducible analysis artifact."""

    model_config = ConfigDict(frozen=True)

    artifact: str
    metric: str
    selector: dict[str, str]


class ProcessFinding(BaseModel):
    """One aggregate observation; never a confirmed cause."""

    model_config = ConfigDict(frozen=True)

    finding_id: str
    classification: Literal["observation_not_confirmed_cause"] = (
        "observation_not_confirmed_cause"
    )
    metric_name: str
    metric_definition: str
    affected_process_step: str | None = None
    affected_group: str | None = None
    affected_population: str
    observation_count: int = Field(ge=0)
    denominator: int = Field(ge=0)
    measured_values: dict[str, float | int]
    comparison_baseline: dict[str, float | int | str]
    time_period: TimePeriod
    evidence_references: list[EvidenceReference]
    limitations: list[str]
    ranking_score: float = Field(ge=0.0)

    @model_validator(mode="after")
    def reject_sensitive_identifiers(self) -> ProcessFinding:
        """Prevent case/event/resource identifiers from entering agent-facing evidence."""
        serialized = self.model_dump_json().lower()
        forbidden = ("original_event__org:resource", '"case_id"', '"event_id"')
        if any(token in serialized for token in forbidden):
            raise ValueError("Evidence contains a prohibited identifier field")
        return self


class EvidencePackage(BaseModel):
    """Serializable aggregate evidence boundary for future LangGraph agents."""

    model_config = ConfigDict(frozen=True)

    analysis_version: str
    dataset_sha256: str
    classification: Literal["aggregate_observations_not_root_causes"] = (
        "aggregate_observations_not_root_causes"
    )
    detector_config: BottleneckConfig
    baselines: dict[str, float | int | str]
    insufficient_sample_summary: dict[str, int]
    findings: list[ProcessFinding]

