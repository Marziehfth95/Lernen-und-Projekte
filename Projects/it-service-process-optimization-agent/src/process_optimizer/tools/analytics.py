"""Read-only, bounded interfaces over precomputed aggregate analytics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from process_optimizer.guardrails.policy import load_policy
from process_optimizer.guardrails.suppression import suppress_small_groups
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.simulation import SimulationRequestV1, WhatIfSimulationEngine

MAX_TOOL_RESULTS = 100
MAX_TOOL_PAYLOAD_BYTES = 256 * 1024


class ToolInput(BaseModel):
    """Immutable base for validated tool parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProcessOverviewInput(ToolInput):
    """No filters are supported for the dataset-wide overview."""


class BottleneckFindingsInput(ToolInput):
    metric_name: str | None = Field(default=None, min_length=1, max_length=100)
    sufficiency_status: Literal["sufficient", "insufficient", "context_only"] | None = None
    limit: int = Field(default=20, ge=1, le=MAX_TOOL_RESULTS)


class GroupHandoffsInput(ToolInput):
    source_group: str | None = Field(default=None, min_length=1, max_length=100)
    target_group: str | None = Field(default=None, min_length=1, max_length=100)
    minimum_observations: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=MAX_TOOL_RESULTS)


class CohortComparisonInput(ToolInput):
    metric: Literal[
        "observed_case_span_seconds",
        "event_count",
        "group_change_event_count",
        "repeated_activity_event_count",
        "immediate_group_reversal_event_count",
    ] = "observed_case_span_seconds"


class LifecyclePatternsInput(ToolInput):
    source_activity: str | None = Field(default=None, min_length=1, max_length=100)
    target_activity: str | None = Field(default=None, min_length=1, max_length=100)
    source_lifecycle: str | None = Field(default=None, min_length=1, max_length=100)
    target_lifecycle: str | None = Field(default=None, min_length=1, max_length=100)
    minimum_observations: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=MAX_TOOL_RESULTS)


class EvidenceDetailsInput(ToolInput):
    identifier: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^(finding|evidence_ref)_[0-9a-f]{24}$",
    )


class AggregateToolResult(BaseModel):
    """JSON-safe response containing only bounded aggregate records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["1.0.0"] = "1.0.0"
    returned_count: int = Field(ge=0)
    truncated: bool
    items: tuple[dict[str, Any], ...]

    @model_validator(mode="after")
    def enforce_privacy_and_bound(self) -> AggregateToolResult:
        if len(self.items) > MAX_TOOL_RESULTS:
            raise ValueError("Tool response exceeds the maximum result size")
        serialized = self.model_dump_json().lower()
        if len(serialized.encode("utf-8")) > MAX_TOOL_PAYLOAD_BYTES:
            raise ValueError("Tool response exceeds the maximum serialized size")
        prohibited = (
            '"case_id"',
            '"event_id"',
            "org:resource",
            '"resource_id"',
            '"resource_identifier"',
        )
        if any(value in serialized for value in prohibited):
            raise ValueError("Tool response contains a prohibited identifier")
        return self


class AggregateAnalyticsTools:
    """Allow-listed readers; never load raw events or accept arbitrary paths."""

    def __init__(
        self,
        analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
        *,
        small_group_threshold: int | None = None,
    ) -> None:
        self._analysis_dir = Path(analysis_dir)
        self._small_group_threshold = (
            small_group_threshold
            if small_group_threshold is not None
            else load_policy().small_group_threshold
        )

    def get_process_overview(self, request: ProcessOverviewInput) -> AggregateToolResult:
        """Return the one dataset-wide overview."""
        del request
        return self._result([self._read_json("process_overview.json")], limit=1)

    def get_bottleneck_findings(
        self, request: BottleneckFindingsInput
    ) -> AggregateToolResult:
        """Return bounded evidence-contract findings, optionally filtered by metric."""
        contract = self._read_json("evidence_contract.json")
        findings = contract["findings"]
        if request.metric_name is not None:
            findings = [
                finding
                for finding in findings
                if finding["metric"]["name"] == request.metric_name
            ]
        if request.sufficiency_status is not None:
            findings = [
                finding
                for finding in findings
                if finding["sufficiency_status"] == request.sufficiency_status
            ]
        return self._result(findings, request.limit)

    def analyze_group_handoffs(self, request: GroupHandoffsInput) -> AggregateToolResult:
        """Read aggregate handoff rows with strict filters and result limits."""
        frame = self._read_parquet("handoff_analysis.parquet")
        frame = frame.loc[frame["observation_count"] >= request.minimum_observations]
        if request.source_group is not None:
            frame = frame.loc[frame["source_group"] == request.source_group]
        if request.target_group is not None:
            frame = frame.loc[frame["target_group"] == request.target_group]
        return self._result(_records(frame), request.limit)

    def compare_process_cohorts(
        self, request: CohortComparisonInput
    ) -> AggregateToolResult:
        """Return the precomputed group-return cohort comparison for one metric."""
        comparison = self._read_json("handoff_cohort_comparison.json")
        item = {
            "classification": comparison["classification"],
            "cohort_definition": comparison["cohort_definition"],
            "metric": request.metric,
            "cohorts": [
                {
                    "cohort": cohort["cohort"],
                    "incident_count": cohort["incident_count"],
                    "distribution": cohort[request.metric],
                }
                for cohort in comparison["cohorts"]
            ],
            "distributional_difference": comparison["distributional_differences"][
                request.metric
            ],
            "limitations": comparison["limitations"],
        }
        return self._result([item], limit=1)

    def inspect_lifecycle_patterns(
        self, request: LifecyclePatternsInput
    ) -> AggregateToolResult:
        """Read aggregate lifecycle patterns without raw timestamps or identifiers."""
        frame = self._read_parquet("lifecycle_patterns.parquet")
        frame = frame.loc[frame["event_transition_count"] >= request.minimum_observations]
        for column, value in (
            ("source_activity", request.source_activity),
            ("target_activity", request.target_activity),
            ("source_lifecycle", request.source_lifecycle),
            ("target_lifecycle", request.target_lifecycle),
        ):
            if value is not None:
                frame = frame.loc[frame[column] == value]
        return self._result(_records(frame), request.limit)

    def get_evidence_details(self, request: EvidenceDetailsInput) -> AggregateToolResult:
        """Resolve a stable finding or reference identifier inside the evidence contract."""
        contract = self._read_json("evidence_contract.json")
        matches = []
        for finding in contract["findings"]:
            if finding["finding_id"] == request.identifier:
                matches.append(finding)
                continue
            references = [
                reference
                for reference in finding["evidence_references"]
                if reference["reference_id"] == request.identifier
            ]
            if references:
                matches.append(
                    {
                        "finding_id": finding["finding_id"],
                        "metric": finding["metric"],
                        "evidence_references": references,
                    }
                )
        return self._result(matches, limit=MAX_TOOL_RESULTS)

    def simulate_group_transition_reduction(
        self, request: SimulationRequestV1
    ) -> AggregateToolResult:
        """Return one deterministic hypothetical result over verified aggregates."""
        result = WhatIfSimulationEngine(self._analysis_dir).simulate(request)
        return self._result([result.model_dump(mode="json")], limit=1)

    def _result(self, items: list[dict[str, Any]], limit: int) -> AggregateToolResult:
        # Suppress before limiting so `truncated` and `returned_count` cannot reveal
        # how many small groups a filter matched.
        visible, _ = suppress_small_groups(items, self._small_group_threshold)
        return _result(visible, limit)

    def _read_json(self, filename: str) -> dict[str, Any]:
        path = self._analysis_dir / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Precomputed aggregate artifact is unavailable: {filename}"
            ) from error
        if not isinstance(payload, dict):
            raise ValueError(f"Expected an object in aggregate artifact: {filename}")
        return payload

    def _read_parquet(self, filename: str) -> pd.DataFrame:
        try:
            return pd.read_parquet(self._analysis_dir / filename)
        except (OSError, ValueError) as error:
            raise ValueError(
                f"Precomputed aggregate artifact is unavailable: {filename}"
            ) from error


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _result(items: list[dict[str, Any]], limit: int) -> AggregateToolResult:
    selected = items[:limit]
    return AggregateToolResult(
        returned_count=len(selected),
        truncated=len(items) > limit,
        items=tuple(selected),
    )
