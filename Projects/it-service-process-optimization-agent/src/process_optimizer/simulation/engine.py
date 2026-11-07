"""Deterministic what-if calculations over verified aggregate evidence only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.simulation.schemas import (
    EstimabilityV1,
    ScenarioResultV1,
    SensitivityAnalysisV1,
    SimulationBaselineV1,
    SimulationEvidenceReferenceV1,
    SimulationRequestV1,
    SimulationResultV1,
)

_HANDOFF_METRIC = "high_frequency_or_repeated_group_handoff"


class WhatIfSimulationEngine:
    """Evaluate assumptions without editing or reinterpreting source records."""

    def __init__(self, analysis_dir: Path) -> None:
        self._analysis_dir = Path(analysis_dir)
        self._contract_path = self._analysis_dir / "evidence_contract.json"
        self._semantic_path = self._analysis_dir / "semantic_validation_report.json"
        self._contract = EvidenceContractV1.model_validate_json(
            self._contract_path.read_text(encoding="utf-8")
        )
        semantic = json.loads(self._semantic_path.read_text(encoding="utf-8"))
        if not isinstance(semantic, dict):
            raise ValueError("Semantic validation report must contain an object")
        self._semantic = semantic

    @property
    def analysis_run_id(self) -> str:
        return self._contract.analysis_run_id

    def eligible_handoff_finding_ids(self) -> tuple[str, ...]:
        """Return deterministic evidence-backed candidates ordered by repeat count."""
        findings = [
            finding
            for finding in self._contract.findings
            if finding.metric.name == _HANDOFF_METRIC
            and isinstance(finding.measured_statistics.get("repeated_visit_count"), int | float)
        ]
        findings.sort(
            key=lambda finding: (
                -float(finding.measured_statistics["repeated_visit_count"]),
                finding.finding_id,
            )
        )
        return tuple(finding.finding_id for finding in findings)

    def simulate(self, request: SimulationRequestV1) -> SimulationResultV1:
        """Calculate assumed event-count effects and refuse unsupported impact estimates."""
        simulation_id = _simulation_id(request)
        reasons = self._validate_sources(request)
        findings_by_id = {finding.finding_id: finding for finding in self._contract.findings}
        selected = [findings_by_id.get(finding_id) for finding_id in request.selected_finding_ids]
        if any(finding is None for finding in selected):
            reasons.append("At least one selected finding is absent from Evidence Contract V1.")
        typed_selected = [finding for finding in selected if finding is not None]
        if any(finding.metric.name != _HANDOFF_METRIC for finding in typed_selected):
            reasons.append("Selected findings must describe verified aggregate group handoffs.")

        repeated_counts = [
            finding.measured_statistics.get("repeated_visit_count")
            for finding in typed_selected
        ]
        if any(not isinstance(value, int | float) for value in repeated_counts):
            reasons.append("Selected findings lack repeated-visit event counts.")
        elif any(
            float(value) < 0 or not float(value).is_integer()
            for value in repeated_counts
        ):
            reasons.append("Repeated-visit event counts must be non-negative integers.")
        denominators = {finding.counts.denominator for finding in typed_selected}
        if None in denominators or len(denominators) != 1:
            reasons.append("Selected findings do not share one verified event-level denominator.")

        semantic_counts = self._semantic_counts(reasons)
        if reasons:
            return self._not_estimable(request, simulation_id, tuple(reasons))

        selected_repeated = int(sum(float(value) for value in repeated_counts))
        selected_transitions = sum(finding.counts.observation_count for finding in typed_selected)
        all_group_changes = int(next(iter(denominators)))
        all_repeated = semantic_counts["all_repeated"]
        if selected_repeated > all_repeated:
            return self._not_estimable(
                request,
                simulation_id,
                ("Selected repeated-visit events exceed the verified global count.",),
            )

        baseline = SimulationBaselineV1(
            selected_group_transition_events=selected_transitions,
            selected_repeated_group_visit_events=selected_repeated,
            all_group_change_events=all_group_changes,
            all_repeated_group_visit_events=all_repeated,
            incidents_with_repeated_group_visits=semantic_counts["incidents_with_returns"],
            total_incidents=semantic_counts["total_incidents"],
        )
        scenarios = tuple(
            _scenario_result(assumption, selected_repeated)
            for assumption in request.assumptions
        )
        avoided = [
            item.hypothetical_repeated_visit_events_avoided.value
            for item in scenarios
            if item.hypothetical_repeated_visit_events_avoided.value is not None
        ]
        return SimulationResultV1(
            simulation_id=simulation_id,
            analysis_run_id=request.analysis_run_id,
            status="ESTIMATED",
            intervention_hypothesis=(
                "If an approved intervention makes a stated share of selected repeated group-"
                "visit events eligible and achieves the stated reduction share, the arithmetic "
                "event count would change as shown. This is not a causal prediction."
            ),
            baseline=baseline,
            scenarios=scenarios,
            sensitivity_analysis=SensitivityAnalysisV1(
                minimum_hypothetical_events_avoided=min(avoided),
                maximum_hypothetical_events_avoided=max(avoided),
                primary_drivers=(
                    "eligible_event_share assumption",
                    "assumed_reduction_share",
                    "selected repeated-group-visit event baseline",
                ),
                interpretation=(
                    "The range reflects assumption choices only; it is not a confidence or "
                    "prediction interval."
                ),
            ),
            evidence_references=self._evidence_references(typed_selected),
            uncertainty_statements=_uncertainty_statements(),
        )

    def _validate_sources(self, request: SimulationRequestV1) -> list[str]:
        reasons: list[str] = []
        if request.analysis_run_id != self._contract.analysis_run_id:
            reasons.append("The requested analysis run does not match Evidence Contract V1.")
        if self._semantic.get("analysis_run_id") != self._contract.analysis_run_id:
            reasons.append("The semantic report and evidence contract run IDs do not match.")
        if self._semantic.get("source_dataset_sha256") != self._contract.source_dataset.sha256:
            reasons.append("The semantic report and evidence contract dataset hashes do not match.")
        return reasons

    def _semantic_counts(self, reasons: list[str]) -> dict[str, int]:
        try:
            handoff = self._semantic["handoff_validation"]
            event_counts = handoff["event_level_counts"]
            incident_counts = handoff["incident_level_counts"]
            values = {
                "all_repeated": int(event_counts["repeated_group_visit_events"]),
                "incidents_with_returns": int(
                    incident_counts["incidents_with_repeated_group_visits"]
                ),
                "total_incidents": int(incident_counts["total_incidents"]),
            }
        except (KeyError, TypeError, ValueError):
            reasons.append("Verified semantic population counts are unavailable.")
            return {"all_repeated": 0, "incidents_with_returns": 0, "total_incidents": 0}
        if min(values.values()) < 0 or values["total_incidents"] == 0:
            reasons.append("Verified semantic population counts are invalid.")
        return values

    def _evidence_references(
        self, findings: list[Any]
    ) -> tuple[SimulationEvidenceReferenceV1, ...]:
        references = [
            SimulationEvidenceReferenceV1(
                finding_id=finding.finding_id,
                reference_id=reference.reference_id,
                artifact=reference.artifact,
                artifact_sha256=reference.artifact_sha256,
                metric=reference.metric,
            )
            for finding in findings
            for reference in finding.evidence_references
        ]
        semantic_hash = hashlib.sha256(self._semantic_path.read_bytes()).hexdigest()
        references.append(
            SimulationEvidenceReferenceV1(
                reference_id=f"simulation_ref_{semantic_hash[:24]}",
                artifact=self._semantic_path.name,
                artifact_sha256=semantic_hash,
                metric="verified event-level and incident-level handoff population counts",
            )
        )
        return tuple(references)

    def _not_estimable(
        self,
        request: SimulationRequestV1,
        simulation_id: str,
        reasons: tuple[str, ...],
    ) -> SimulationResultV1:
        return SimulationResultV1(
            simulation_id=simulation_id,
            analysis_run_id=request.analysis_run_id,
            status="NOT_ESTIMABLE",
            intervention_hypothesis=(
                "A hypothetical reduction was requested, but verified aggregate inputs were "
                "insufficient for a numerical event-count calculation."
            ),
            baseline=None,
            scenarios=(),
            sensitivity_analysis=SensitivityAnalysisV1(
                minimum_hypothetical_events_avoided=None,
                maximum_hypothetical_events_avoided=None,
                primary_drivers=(),
                interpretation="No sensitivity range is produced without a verified baseline.",
            ),
            evidence_references=(),
            uncertainty_statements=_uncertainty_statements(),
            not_estimable_reasons=reasons,
        )


def _scenario_result(assumption: Any, selected_repeated: int) -> ScenarioResultV1:
    combined = round(
        assumption.eligible_event_share * assumption.assumed_reduction_share, 6
    )
    avoided = round(selected_repeated * combined, 3)
    remaining = round(selected_repeated - avoided, 3)
    return ScenarioResultV1(
        name=assumption.name,
        eligible_event_share_assumption=assumption.eligible_event_share,
        reduction_share_assumption=assumption.assumed_reduction_share,
        combined_hypothetical_reduction_share=combined,
        hypothetical_repeated_visit_events_avoided=EstimabilityV1(
            status="ESTIMATED",
            value=avoided,
            unit="expected event_transitions under assumptions",
            explanation=(
                "selected repeated-visit events × eligible share × assumed reduction share"
            ),
        ),
        hypothetical_repeated_visit_events_remaining=EstimabilityV1(
            status="ESTIMATED",
            value=remaining,
            unit="expected event_transitions under assumptions",
            explanation="baseline selected repeated-visit events minus hypothetical avoided events",
        ),
        affected_incident_count=EstimabilityV1(
            status="NOT_ESTIMABLE",
            unit="incidents",
            explanation=(
                "Aggregate transition findings do not identify overlap across incidents or map "
                "avoided events to distinct incidents."
            ),
        ),
        elapsed_time_impact=EstimabilityV1(
            status="NOT_ESTIMABLE",
            unit="seconds",
            explanation=(
                "No causal model shows that removing a handoff removes its observed interval or "
                "changes end-to-end case span."
            ),
        ),
        sla_impact=EstimabilityV1(
            status="NOT_ESTIMABLE",
            unit="SLA outcomes",
            explanation="The dataset contains no verified SLA target or breach definition.",
        ),
    )


def _uncertainty_statements() -> tuple[str, ...]:
    return (
        "Scenario rates are user-configurable assumptions, not effects measured in the event log.",
        "Repeated group visits can represent legitimate routing or incident complexity.",
        "Event-level reductions must not be presented as incident-level reductions.",
        "Removing a handoff does not imply removal of its full observed elapsed interval.",
        "No guaranteed time saving, SLA improvement, or business outcome is estimated.",
    )


def _simulation_id(request: SimulationRequestV1) -> str:
    payload = request.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"simulation_{hashlib.sha256(encoded).hexdigest()[:24]}"
