"""Versioned schemas for transparent what-if calculations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SIMULATION_SCHEMA_VERSION = "1.0.0"
SIMULATION_ENGINE_VERSION = "phase5-what-if-v1"
FindingId = Annotated[str, Field(pattern=r"^finding_[0-9a-f]{24}$")]


class FrozenModel(BaseModel):
    """Immutable strict model for simulation inputs and outputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScenarioAssumptionV1(FrozenModel):
    """Explicit assumptions for one non-causal scenario."""

    name: Literal["conservative", "moderate", "optimistic"]
    eligible_event_share: float = Field(ge=0.0, le=1.0)
    assumed_reduction_share: float = Field(ge=0.0, le=1.0)


def default_scenario_assumptions() -> tuple[ScenarioAssumptionV1, ...]:
    """Return deliberately bounded defaults; these are assumptions, not estimates."""
    return (
        ScenarioAssumptionV1(
            name="conservative", eligible_event_share=0.10, assumed_reduction_share=0.10
        ),
        ScenarioAssumptionV1(
            name="moderate", eligible_event_share=0.25, assumed_reduction_share=0.30
        ),
        ScenarioAssumptionV1(
            name="optimistic", eligible_event_share=0.50, assumed_reduction_share=0.50
        ),
    )


class SimulationRequestV1(FrozenModel):
    """Validated request selecting evidence-backed handoff findings."""

    schema_version: Literal["1.0.0"] = SIMULATION_SCHEMA_VERSION
    analysis_run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    selected_finding_ids: tuple[FindingId, ...] = Field(min_length=1, max_length=20)
    assumptions: tuple[ScenarioAssumptionV1, ...] = Field(
        default_factory=default_scenario_assumptions,
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_unique_inputs(self) -> SimulationRequestV1:
        if len(set(self.selected_finding_ids)) != len(self.selected_finding_ids):
            raise ValueError("selected_finding_ids must be unique")
        expected = {"conservative", "moderate", "optimistic"}
        actual = {item.name for item in self.assumptions}
        if actual != expected or len(actual) != len(self.assumptions):
            raise ValueError("assumptions must contain each named scenario exactly once")
        ordered = {"conservative": 0, "moderate": 1, "optimistic": 2}
        effectiveness = [
            item.eligible_event_share * item.assumed_reduction_share
            for item in sorted(self.assumptions, key=lambda item: ordered[item.name])
        ]
        if effectiveness != sorted(effectiveness):
            raise ValueError("scenario effects must be monotonic from conservative to optimistic")
        return self


class SimulationEvidenceReferenceV1(FrozenModel):
    finding_id: str | None = None
    reference_id: str
    artifact: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric: str


class SimulationBaselineV1(FrozenModel):
    selected_transition_count_level: Literal["event_transition"] = "event_transition"
    selected_group_transition_events: int = Field(ge=0)
    selected_repeated_group_visit_events: int = Field(ge=0)
    all_group_change_events: int = Field(ge=0)
    all_repeated_group_visit_events: int = Field(ge=0)
    incident_count_level: Literal["incident"] = "incident"
    incidents_with_repeated_group_visits: int = Field(ge=0)
    total_incidents: int = Field(gt=0)


class EstimabilityV1(FrozenModel):
    status: Literal["ESTIMATED", "NOT_ESTIMABLE"]
    value: float | None = None
    unit: str
    explanation: str

    @model_validator(mode="after")
    def validate_value(self) -> EstimabilityV1:
        if self.status == "NOT_ESTIMABLE" and self.value is not None:
            raise ValueError("NOT_ESTIMABLE values must be null")
        if self.status == "ESTIMATED" and self.value is None:
            raise ValueError("ESTIMATED values require a value")
        return self


class ScenarioResultV1(FrozenModel):
    name: Literal["conservative", "moderate", "optimistic"]
    eligible_event_share_assumption: float = Field(ge=0.0, le=1.0)
    reduction_share_assumption: float = Field(ge=0.0, le=1.0)
    combined_hypothetical_reduction_share: float = Field(ge=0.0, le=1.0)
    hypothetical_repeated_visit_events_avoided: EstimabilityV1
    hypothetical_repeated_visit_events_remaining: EstimabilityV1
    affected_incident_count: EstimabilityV1
    elapsed_time_impact: EstimabilityV1
    sla_impact: EstimabilityV1


class SensitivityAnalysisV1(FrozenModel):
    minimum_hypothetical_events_avoided: float | None
    maximum_hypothetical_events_avoided: float | None
    primary_drivers: tuple[str, ...]
    interpretation: str


class SimulationResultV1(FrozenModel):
    """Aggregate what-if result; never a prediction or measured intervention effect."""

    schema_version: Literal["1.0.0"] = SIMULATION_SCHEMA_VERSION
    engine_version: Literal["phase5-what-if-v1"] = SIMULATION_ENGINE_VERSION
    simulation_id: str = Field(pattern=r"^simulation_[0-9a-f]{24}$")
    analysis_run_id: str
    status: Literal["ESTIMATED", "NOT_ESTIMABLE"]
    classification: Literal["hypothetical_non_causal"] = "hypothetical_non_causal"
    intervention_hypothesis: str
    baseline: SimulationBaselineV1 | None
    scenarios: tuple[ScenarioResultV1, ...]
    sensitivity_analysis: SensitivityAnalysisV1
    evidence_references: tuple[SimulationEvidenceReferenceV1, ...]
    uncertainty_statements: tuple[str, ...]
    not_estimable_reasons: tuple[str, ...] = ()
