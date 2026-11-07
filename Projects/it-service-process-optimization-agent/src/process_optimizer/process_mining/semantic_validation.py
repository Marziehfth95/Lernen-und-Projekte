"""Aggregate semantic validation for lifecycle and group-routing observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from process_optimizer.process_mining.constants import (
    ACTIVITY,
    CASE_ID,
    GROUP,
    LIFECYCLE,
    TIMESTAMP,
)
from process_optimizer.process_mining.metrics import (
    consecutive_events,
    descriptive_statistics,
    order_events,
)

VALIDATED_ACTIVITY_TRANSITIONS = (
    ("Completed", "Completed"),
    ("Completed", "Accepted"),
    ("Completed", "Queued"),
)


@dataclass(frozen=True)
class SemanticValidationResult:
    """Aggregate-only semantic outputs; no case or resource identifiers leave this layer."""

    lifecycle_patterns: pd.DataFrame
    transition_summaries: list[dict[str, Any]]
    handoff_validation: dict[str, Any]
    cohort_comparison: dict[str, Any]


def build_semantic_validation(events: pd.DataFrame) -> SemanticValidationResult:
    """Validate lifecycle meanings and handoff classifications using aggregate evidence."""
    ordered = order_events(events)
    lifecycle_patterns = build_lifecycle_patterns(ordered)
    transition_summaries = build_transition_summaries(ordered)
    handoff_validation, cohort_comparison = build_handoff_validation(ordered)
    return SemanticValidationResult(
        lifecycle_patterns=lifecycle_patterns,
        transition_summaries=transition_summaries,
        handoff_validation=handoff_validation,
        cohort_comparison=cohort_comparison,
    )


def build_lifecycle_patterns(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate activity/lifecycle/group combinations for every consecutive event pair."""
    pairs = _semantic_pairs(events)
    rows: list[dict[str, Any]] = []
    grouping = [
        "source_activity",
        "source_lifecycle",
        "source_group",
        "target_activity",
        "target_lifecycle",
        "target_group",
    ]
    for keys, group in pairs.groupby(grouping, sort=True, dropna=False):
        stats = descriptive_statistics(group["elapsed_seconds"])
        (
            source_activity,
            source_lifecycle,
            source_group,
            target_activity,
            target_lifecycle,
            target_group,
        ) = keys
        rows.append(
            {
                "source_activity": _category(source_activity),
                "source_lifecycle": _category(source_lifecycle),
                "source_group": _category(source_group),
                "target_activity": _category(target_activity),
                "target_lifecycle": _category(target_lifecycle),
                "target_group": _category(target_group),
                "event_transition_count": int(len(group)),
                "incident_count": int(group[CASE_ID].nunique()),
                "same_group_event_count": int((group["group_change"] == False).sum()),  # noqa: E712
                "group_change_event_count": int(group["group_change"].sum()),
                "elapsed_seconds_median": stats["median"],
                "elapsed_seconds_p75": stats["p75"],
                "elapsed_seconds_p90": stats["p90"],
                "first_observed_utc": _timestamp(group[TIMESTAMP].min()),
                "last_observed_utc": _timestamp(group["target_timestamp"].max()),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["event_transition_count", *grouping],
        ascending=[False, True, True, True, True, True, True],
        kind="stable",
    ).reset_index(drop=True)


def build_transition_summaries(events: pd.DataFrame) -> list[dict[str, Any]]:
    """Explain selected long activity transitions by observed lifecycle pair."""
    pairs = _semantic_pairs(events)
    summaries: list[dict[str, Any]] = []
    for source_activity, target_activity in VALIDATED_ACTIVITY_TRANSITIONS:
        selected = pairs.loc[
            (pairs["source_activity"] == source_activity)
            & (pairs["target_activity"] == target_activity)
        ]
        lifecycle_rows = []
        for (source_lifecycle, target_lifecycle), group in selected.groupby(
            ["source_lifecycle", "target_lifecycle"], sort=True, dropna=False
        ):
            stats = descriptive_statistics(group["elapsed_seconds"])
            lifecycle_rows.append(
                {
                    "source_lifecycle": _category(source_lifecycle),
                    "target_lifecycle": _category(target_lifecycle),
                    "event_transition_count": int(len(group)),
                    "incident_count": int(group[CASE_ID].nunique()),
                    "same_group_event_count": int((~group["group_change"]).sum()),
                    "group_change_event_count": int(group["group_change"].sum()),
                    "elapsed_seconds": _distribution(stats),
                }
            )
        lifecycle_rows.sort(
            key=lambda row: (
                -row["event_transition_count"],
                row["source_lifecycle"],
                row["target_lifecycle"],
            )
        )
        total_stats = descriptive_statistics(selected["elapsed_seconds"])
        summaries.append(
            {
                "source_activity": source_activity,
                "target_activity": target_activity,
                "event_transition_count": int(len(selected)),
                "incident_count": int(selected[CASE_ID].nunique()),
                "same_group_event_count": int((~selected["group_change"]).sum()),
                "group_change_event_count": int(selected["group_change"].sum()),
                "elapsed_seconds": _distribution(total_stats),
                "lifecycle_patterns": lifecycle_rows,
                "interpretation": (
                    "Observed activity/lifecycle sequence only; it does not establish "
                    "rework, closure, reopening, inefficiency, or causality."
                ),
            }
        )
    return summaries


def build_handoff_validation(
    events: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate event-level routing events from incident-level cohort membership."""
    ordered = order_events(events)
    case_rows: list[dict[str, int | float | bool]] = []
    for _, case_events in ordered.groupby(CASE_ID, sort=False):
        groups = case_events[GROUP].tolist()
        activities = case_events[ACTIVITY].tolist()
        visited: set[str] = set()
        group_changes = 0
        repeated_group_visits = 0
        immediate_group_reversals = 0
        for position, current_group in enumerate(groups):
            current = None if pd.isna(current_group) else str(current_group)
            if position:
                previous_group = groups[position - 1]
                previous = None if pd.isna(previous_group) else str(previous_group)
                if current is not None and previous is not None and current != previous:
                    group_changes += 1
                    if current in visited:
                        repeated_group_visits += 1
                    if position >= 2:
                        prior_group = groups[position - 2]
                        prior = None if pd.isna(prior_group) else str(prior_group)
                        if prior is not None and current == prior:
                            immediate_group_reversals += 1
            if current is not None:
                visited.add(current)
        timestamp_values = case_events[TIMESTAMP]
        span = (timestamp_values.iloc[-1] - timestamp_values.iloc[0]).total_seconds()
        case_rows.append(
            {
                "has_repeated_group_visit": repeated_group_visits > 0,
                "event_count": int(len(case_events)),
                "group_change_event_count": group_changes,
                "repeated_group_visit_event_count": repeated_group_visits,
                "immediate_group_reversal_event_count": immediate_group_reversals,
                "repeated_activity_event_count": int(len(activities) - len(set(activities))),
                "observed_case_span_seconds": float(span),
            }
        )

    case_metrics = pd.DataFrame(case_rows)
    span_threshold = float(case_metrics["observed_case_span_seconds"].quantile(0.90))
    case_metrics["is_long_observed_span"] = (
        case_metrics["observed_case_span_seconds"] >= span_threshold
    )
    cohort_rows = []
    for has_return, cohort in case_metrics.groupby("has_repeated_group_visit", sort=True):
        label = "with_group_return" if has_return else "without_group_return"
        cohort_rows.append(_cohort_row(label, cohort))
    cohorts_by_label = {row["cohort"]: row for row in cohort_rows}
    with_return = cohorts_by_label["with_group_return"]
    without_return = cohorts_by_label["without_group_return"]
    comparison = {
        "classification": "observational_association_not_causal",
        "cohort_definition": (
            "Incidents are assigned by whether at least one group-change event targets "
            "a group observed earlier in the same incident."
        ),
        "long_observed_span_definition": (
            "Observed case span at or above the dataset-wide p90; this is not an SLA breach."
        ),
        "long_observed_span_threshold_seconds": span_threshold,
        "cohorts": cohort_rows,
        "distributional_differences": {
            metric: _cohort_difference(with_return[metric], without_return[metric])
            for metric in (
                "observed_case_span_seconds",
                "event_count",
                "group_change_event_count",
                "repeated_activity_event_count",
                "immediate_group_reversal_event_count",
            )
        },
        "limitations": [
            "Cohorts are observational and differ in event volume and process complexity.",
            (
                "No adjustment for product, impact, organization, censoring, or calendar "
                "time is applied."
            ),
            "Repeated group visits can reflect valid routing and do not establish inefficiency.",
            "Observed case span is not verified resolution time or active processing time.",
        ],
    }
    validation = {
        "definitions": {
            "group_change": "Consecutive classified events with different org:group values.",
            "repeated_group_visit": (
                "A group-change event whose target group appeared earlier in the incident."
            ),
            "immediate_group_reversal": "A classified A -> B -> A group sequence.",
            "repeated_activity": (
                "An activity occurrence after its first occurrence in an incident, regardless "
                "of lifecycle transition."
            ),
            "long_observed_process_span": (
                "Last minus first observed timestamp at or above the dataset-wide p90."
            ),
        },
        "event_level_counts": {
            "group_change_events": int(case_metrics["group_change_event_count"].sum()),
            "repeated_group_visit_events": int(
                case_metrics["repeated_group_visit_event_count"].sum()
            ),
            "immediate_group_reversal_events": int(
                case_metrics["immediate_group_reversal_event_count"].sum()
            ),
            "repeated_activity_events_beyond_first": int(
                case_metrics["repeated_activity_event_count"].sum()
            ),
        },
        "incident_level_counts": {
            "total_incidents": int(len(case_metrics)),
            "incidents_with_group_changes": int(
                (case_metrics["group_change_event_count"] > 0).sum()
            ),
            "incidents_with_repeated_group_visits": int(
                case_metrics["has_repeated_group_visit"].sum()
            ),
            "incidents_with_immediate_group_reversals": int(
                (case_metrics["immediate_group_reversal_event_count"] > 0).sum()
            ),
            "incidents_with_repeated_activity_names": int(
                (case_metrics["repeated_activity_event_count"] > 0).sum()
            ),
            "incidents_with_long_observed_spans": int(case_metrics["is_long_observed_span"].sum()),
        },
        "long_observed_span_threshold_seconds": span_threshold,
        "interpretation": (
            "Event-level routing counts and incident-level cohort counts are distinct; all "
            "reported relationships are observational, not causal."
        ),
    }
    return validation, comparison


def _semantic_pairs(events: pd.DataFrame) -> pd.DataFrame:
    pairs = consecutive_events(events).rename(columns={LIFECYCLE: "source_lifecycle"})
    ordered = order_events(events)
    grouped = ordered.groupby(CASE_ID, sort=False)
    lifecycle_targets = grouped[LIFECYCLE].shift(-1)
    lifecycle_lookup = ordered[[CASE_ID]].copy()
    lifecycle_lookup["target_lifecycle"] = lifecycle_targets
    lifecycle_lookup = lifecycle_lookup.loc[lifecycle_lookup["target_lifecycle"].notna()]
    pairs["source_lifecycle"] = ordered.loc[pairs.index, LIFECYCLE].to_numpy()
    pairs["target_lifecycle"] = lifecycle_lookup["target_lifecycle"].to_numpy()
    pairs["group_change"] = (
        pairs["source_group"].notna()
        & pairs["target_group"].notna()
        & (pairs["source_group"] != pairs["target_group"])
    )
    return pairs


def _cohort_row(label: str, cohort: pd.DataFrame) -> dict[str, Any]:
    row: dict[str, Any] = {
        "cohort": label,
        "incident_count": int(len(cohort)),
        "long_observed_span_incident_count": int(cohort["is_long_observed_span"].sum()),
        "long_observed_span_rate": float(cohort["is_long_observed_span"].mean()),
    }
    for metric in (
        "observed_case_span_seconds",
        "event_count",
        "group_change_event_count",
        "repeated_group_visit_event_count",
        "immediate_group_reversal_event_count",
        "repeated_activity_event_count",
    ):
        row[metric] = _distribution(descriptive_statistics(cohort[metric]))
    return row


def _cohort_difference(
    with_return: dict[str, Any], without_return: dict[str, Any]
) -> dict[str, Any]:
    with_median = float(with_return["median"] or 0.0)
    without_median = float(without_return["median"] or 0.0)
    return {
        "with_group_return_median": with_median,
        "without_group_return_median": without_median,
        "median_difference": with_median - without_median,
        "median_ratio": with_median / without_median if without_median else None,
    }


def _distribution(stats: dict[str, float | int | None]) -> dict[str, float | int | None]:
    return {
        "observation_count": stats["observation_count"],
        "median": stats["median"],
        "p75": stats["p75"],
        "p90": stats["p90"],
    }


def _category(value: Any) -> str:
    return "<missing>" if pd.isna(value) else str(value)


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()
