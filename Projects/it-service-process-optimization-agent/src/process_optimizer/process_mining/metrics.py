"""Explicit, reproducible process-mining metrics over validated Parquet records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from process_optimizer.process_mining.constants import (
    ACTIVITY,
    CASE_ID,
    COMPARATIVE_DIMENSIONS,
    EVENT_INDEX,
    GROUP,
    LIFECYCLE,
    TIMESTAMP,
)


@dataclass(frozen=True)
class HandoffAnalysis:
    """Aggregate handoff table plus classification coverage."""

    transitions: pd.DataFrame
    eligible_consecutive_event_count: int
    unclassified_missing_group_count: int
    same_group_consecutive_event_count: int


def order_events(events: pd.DataFrame) -> pd.DataFrame:
    """Return stable source order; timestamps never break ties or replace event_index."""
    duplicate_positions = events.duplicated([CASE_ID, EVENT_INDEX], keep=False)
    if duplicate_positions.any():
        raise ValueError("Duplicate case_id/event_index positions prevent deterministic ordering")
    ordered = events.sort_values([CASE_ID, EVENT_INDEX], kind="stable").reset_index(drop=True)
    ordered[TIMESTAMP] = pd.to_datetime(ordered[TIMESTAMP], utc=True, errors="coerce")
    return ordered


def descriptive_statistics(values: pd.Series) -> dict[str, float | int | None]:
    """Return count and robust distribution statistics without imputation."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {
            "observation_count": 0,
            "minimum": None,
            "mean": None,
            "median": None,
            "p75": None,
            "p90": None,
            "maximum": None,
        }
    return {
        "observation_count": int(len(numeric)),
        "minimum": _number(numeric.min()),
        "mean": _number(numeric.mean()),
        "median": _number(numeric.median()),
        "p75": _number(numeric.quantile(0.75)),
        "p90": _number(numeric.quantile(0.90)),
        "maximum": _number(numeric.max()),
    }


def calculate_case_spans(events: pd.DataFrame) -> pd.DataFrame:
    """Calculate last-minus-first observed timestamp per case in source data."""
    ordered = order_events(events)
    spans = ordered.groupby(CASE_ID, sort=True)[TIMESTAMP].agg(["min", "max", "count"])
    spans = spans.rename(
        columns={"min": "first_observed_utc", "max": "last_observed_utc", "count": "event_count"}
    )
    spans["observed_case_span_seconds"] = (
        spans["last_observed_utc"] - spans["first_observed_utc"]
    ).dt.total_seconds()
    return spans.reset_index()


def build_process_overview(cases: pd.DataFrame, events: pd.DataFrame) -> dict[str, Any]:
    """Build dataset-wide counts, frequencies, and observed-span distributions."""
    ordered = order_events(events)
    spans = calculate_case_spans(ordered)
    activities = ordered[ACTIVITY].value_counts(dropna=False)
    lifecycle = ordered[LIFECYCLE].value_counts(dropna=False)
    event_counts = ordered.groupby(CASE_ID, sort=True).size()
    return {
        "definitions": {
            "activity": f"Exact original XES field {ACTIVITY}; values are not remapped.",
            "lifecycle": (
                f"Exact original XES field {LIFECYCLE}; retained separately and not "
                "combined with activity."
            ),
            "event_order": (
                f"Ascending {EVENT_INDEX} within case; timestamp ties preserve source order."
            ),
            "observed_case_span": (
                "Last observed timestamp minus first observed timestamp; not verified "
                "resolution time or active work duration."
            ),
        },
        "case_count": int(cases[CASE_ID].nunique()),
        "event_count": int(len(ordered)),
        "observed_time_range": {
            "start_utc": _timestamp(ordered[TIMESTAMP].min()),
            "end_utc": _timestamp(ordered[TIMESTAMP].max()),
        },
        "activity_frequencies": [
            {"activity": _category(name), "event_count": int(count)}
            for name, count in activities.items()
        ],
        "lifecycle_frequencies": [
            {"lifecycle": _category(name), "event_count": int(count)}
            for name, count in lifecycle.items()
        ],
        "events_per_case": descriptive_statistics(event_counts),
        "single_event_case_count": int((event_counts == 1).sum()),
        "observed_case_span_seconds": descriptive_statistics(
            spans["observed_case_span_seconds"]
        ),
    }


def build_variants(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate exact source-ordered activity sequences."""
    ordered = order_events(events)
    if ordered[ACTIVITY].isna().any():
        raise ValueError("Missing activities prevent complete variant construction")
    sequences = ordered.groupby(CASE_ID, sort=True)[ACTIVITY].agg(tuple)
    frequencies = sequences.value_counts(sort=False).rename("case_count").reset_index()
    frequencies = frequencies.rename(columns={ACTIVITY: "activity_sequence"})
    frequencies["variant_id"] = frequencies["activity_sequence"].map(_variant_id)
    total_cases = int(len(sequences))
    frequencies["coverage"] = frequencies["case_count"] / total_cases if total_cases else 0.0
    frequencies["variant_length"] = frequencies["activity_sequence"].map(len)
    frequencies = frequencies.sort_values(
        ["case_count", "variant_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    frequencies["cumulative_coverage"] = frequencies["coverage"].cumsum()
    frequencies["activity_sequence"] = frequencies["activity_sequence"].map(list)
    return frequencies[
        [
            "variant_id",
            "activity_sequence",
            "variant_length",
            "case_count",
            "coverage",
            "cumulative_coverage",
        ]
    ]


def consecutive_events(events: pd.DataFrame) -> pd.DataFrame:
    """Materialize consecutive source-ordered pairs for aggregate calculations."""
    ordered = order_events(events)
    grouped = ordered.groupby(CASE_ID, sort=False)
    pairs = ordered[[CASE_ID, ACTIVITY, GROUP, TIMESTAMP, EVENT_INDEX]].copy()
    pairs["target_activity"] = grouped[ACTIVITY].shift(-1)
    pairs["target_group"] = grouped[GROUP].shift(-1)
    pairs["target_timestamp"] = grouped[TIMESTAMP].shift(-1)
    pairs["target_event_index"] = grouped[EVENT_INDEX].shift(-1)
    pairs = pairs.loc[pairs["target_event_index"].notna()].copy()
    pairs = pairs.rename(columns={ACTIVITY: "source_activity", GROUP: "source_group"})
    pairs["elapsed_seconds"] = (
        pairs["target_timestamp"] - pairs[TIMESTAMP]
    ).dt.total_seconds()
    return pairs


def build_directly_follows(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate directly-follows frequency and inter-event elapsed-time statistics."""
    pairs = consecutive_events(events)
    rows: list[dict[str, Any]] = []
    total = len(pairs)
    for (source, target), group in pairs.groupby(
        ["source_activity", "target_activity"], sort=True, dropna=False
    ):
        stats = descriptive_statistics(group["elapsed_seconds"])
        rows.append(
            {
                "source_activity": _category(source),
                "target_activity": _category(target),
                "observation_count": int(len(group)),
                "case_count": int(group[CASE_ID].nunique()),
                "transition_share": float(len(group) / total) if total else 0.0,
                "negative_elapsed_count": int((group["elapsed_seconds"] < 0).sum()),
                **_duration_columns(stats),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return _empty_dfg()
    source_totals = frame.groupby("source_activity")["observation_count"].transform("sum")
    frame["source_transition_share"] = frame["observation_count"] / source_totals
    return frame.sort_values(
        ["observation_count", "source_activity", "target_activity"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def build_handoff_analysis(events: pd.DataFrame) -> HandoffAnalysis:
    """Aggregate consecutive group changes and identify returns to visited groups."""
    ordered = order_events(events)
    records: list[dict[str, Any]] = []
    unclassified = 0
    same_group = 0
    eligible = 0

    for _, case_events in ordered.groupby(CASE_ID, sort=False):
        visited: set[str] = set()
        previous_group: Any = None
        previous_timestamp: Any = None
        previous_exists = False
        selected = case_events[[CASE_ID, GROUP, TIMESTAMP]]
        for current_case, current_group, current_timestamp in selected.itertuples(
            index=False, name=None
        ):
            if previous_exists:
                eligible += 1
                if pd.isna(previous_group) or pd.isna(current_group):
                    unclassified += 1
                elif current_group == previous_group:
                    same_group += 1
                else:
                    records.append(
                        {
                            CASE_ID: current_case,
                            "source_group": str(previous_group),
                            "target_group": str(current_group),
                            "elapsed_seconds": (
                                current_timestamp - previous_timestamp
                            ).total_seconds(),
                            "repeated_visit": str(current_group) in visited,
                        }
                    )
            if not pd.isna(current_group):
                visited.add(str(current_group))
            previous_group = current_group
            previous_timestamp = current_timestamp
            previous_exists = True

    details = pd.DataFrame(records)
    if details.empty:
        transitions = _empty_handoffs()
    else:
        rows = []
        for (source, target), group in details.groupby(
            ["source_group", "target_group"], sort=True
        ):
            stats = descriptive_statistics(group["elapsed_seconds"])
            rows.append(
                {
                    "source_group": source,
                    "target_group": target,
                    "observation_count": int(len(group)),
                    "case_count": int(group[CASE_ID].nunique()),
                    "repeated_visit_count": int(group["repeated_visit"].sum()),
                    "repeated_visit_rate": float(group["repeated_visit"].mean()),
                    **_duration_columns(stats),
                }
            )
        transitions = pd.DataFrame(rows)
        source_totals = transitions.groupby("source_group")["observation_count"].transform("sum")
        transitions["source_handoff_share"] = transitions["observation_count"] / source_totals
        transitions = transitions.sort_values(
            ["observation_count", "source_group", "target_group"],
            ascending=[False, True, True],
            kind="stable",
        ).reset_index(drop=True)
    return HandoffAnalysis(
        transitions=transitions,
        eligible_consecutive_event_count=eligible,
        unclassified_missing_group_count=unclassified,
        same_group_consecutive_event_count=same_group,
    )


def build_rework_analysis(events: pd.DataFrame, handoffs: HandoffAnalysis) -> dict[str, Any]:
    """Measure repeats without interpreting them as waste or failed work."""
    ordered = order_events(events)
    activity_case_counts = ordered.groupby([CASE_ID, ACTIVITY], dropna=False).size()
    repeated = activity_case_counts.loc[activity_case_counts > 1]
    activity_rows = []
    if not repeated.empty:
        repeat_frame = repeated.rename("occurrences").reset_index()
        repeat_frame["repeat_event_count"] = repeat_frame["occurrences"] - 1
        for activity, group in repeat_frame.groupby(ACTIVITY, dropna=False, sort=True):
            activity_rows.append(
                {
                    "activity": _category(activity),
                    "cases_with_repetition": int(group[CASE_ID].nunique()),
                    "repeat_event_count_beyond_first": int(group["repeat_event_count"].sum()),
                }
            )
    pairs = consecutive_events(ordered)
    immediate_loops = pairs.loc[pairs["source_activity"] == pairs["target_activity"]]
    return {
        "definitions": {
            "repeated_activity": "Occurrences of an activity after its first occurrence in a case.",
            "immediate_activity_loop": "Consecutive events with the same activity value.",
            "repeated_group_visit": (
                "A handoff whose target group appeared earlier in the same case, after "
                "at least one intervening event."
            ),
        },
        "cases_with_any_repeated_activity": int(repeated.index.get_level_values(0).nunique()),
        "repeat_event_count_beyond_first": int((repeated - 1).sum()),
        "activity_repetitions": sorted(
            activity_rows,
            key=lambda row: (-row["repeat_event_count_beyond_first"], row["activity"]),
        ),
        "immediate_activity_loop_count": int(len(immediate_loops)),
        "repeated_group_visit_count": int(
            handoffs.transitions["repeated_visit_count"].sum()
            if not handoffs.transitions.empty
            else 0
        ),
    }


def build_comparative_analysis(events: pd.DataFrame) -> dict[str, Any]:
    """Compare observed spans across verified dimensions with explicit cohort policy."""
    ordered = order_events(events)
    spans = calculate_case_spans(ordered).set_index(CASE_ID)
    result: dict[str, Any] = {}
    for dimension in COMPARATIVE_DIMENSIONS:
        if dimension not in ordered:
            continue
        per_case_cardinality = ordered.groupby(CASE_ID)[dimension].nunique(dropna=True)
        multi_value_cases = int((per_case_cardinality > 1).sum())
        cohorts = []
        for value, dimension_events in ordered.groupby(dimension, dropna=False, sort=True):
            case_ids = dimension_events[CASE_ID].drop_duplicates()
            cohort_spans = spans.reindex(case_ids)["observed_case_span_seconds"]
            cohorts.append(
                {
                    "value": _category(value),
                    "case_count": int(len(case_ids)),
                    "event_count": int(len(dimension_events)),
                    "observed_case_span_seconds": descriptive_statistics(cohort_spans),
                }
            )
        cohorts.sort(key=lambda row: (-row["case_count"], row["value"]))
        result[dimension] = {
            "cohort_policy": (
                "overlapping_observed_value_cohorts"
                if multi_value_cases
                else "single_observed_value_per_case"
            ),
            "cases_with_multiple_values": multi_value_cases,
            "cohorts": cohorts,
        }
    return result


def build_temporal_analysis(events: pd.DataFrame) -> dict[str, Any]:
    """Create non-overlapping UTC calendar-quarter cohorts by first observed event."""
    ordered = order_events(events)
    spans = calculate_case_spans(ordered)
    first = spans["first_observed_utc"]
    spans["cohort"] = first.dt.year.astype(str) + "-Q" + first.dt.quarter.astype(str)
    event_counts = ordered.groupby(CASE_ID).size()
    rows = []
    for cohort, group in spans.groupby("cohort", sort=True):
        case_ids = group[CASE_ID]
        rows.append(
            {
                "cohort": cohort,
                "case_count": int(len(group)),
                "event_count": int(event_counts.reindex(case_ids).sum()),
                "observed_case_span_seconds": descriptive_statistics(
                    group["observed_case_span_seconds"]
                ),
            }
        )
    return {
        "definition": (
            "Non-overlapping cohort based on UTC calendar quarter of each case's first "
            "observed event; spans may extend beyond the cohort quarter."
        ),
        "cohorts": rows,
    }


def transition_duration_baseline(events: pd.DataFrame) -> dict[str, float | int | None]:
    """Describe all non-negative consecutive-event elapsed times as the baseline."""
    durations = consecutive_events(events)["elapsed_seconds"]
    return descriptive_statistics(durations.loc[durations >= 0])


def _duration_columns(stats: dict[str, float | int | None]) -> dict[str, Any]:
    return {
        "duration_observation_count": stats["observation_count"],
        "elapsed_seconds_minimum": stats["minimum"],
        "elapsed_seconds_mean": stats["mean"],
        "elapsed_seconds_median": stats["median"],
        "elapsed_seconds_p75": stats["p75"],
        "elapsed_seconds_p90": stats["p90"],
        "elapsed_seconds_maximum": stats["maximum"],
    }


def _number(value: Any) -> float:
    return round(float(value), 6)


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _category(value: Any) -> str:
    return "<missing>" if pd.isna(value) else str(value)


def _variant_id(sequence: tuple[str, ...]) -> str:
    payload = json.dumps(sequence, ensure_ascii=False, separators=(",", ":"))
    return f"variant_{hashlib.sha256(payload.encode()).hexdigest()[:20]}"


def _empty_dfg() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_activity",
            "target_activity",
            "observation_count",
            "case_count",
            "transition_share",
            "negative_elapsed_count",
            "duration_observation_count",
            "elapsed_seconds_minimum",
            "elapsed_seconds_mean",
            "elapsed_seconds_median",
            "elapsed_seconds_p75",
            "elapsed_seconds_p90",
            "elapsed_seconds_maximum",
            "source_transition_share",
        ]
    )


def _empty_handoffs() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_group",
            "target_group",
            "observation_count",
            "case_count",
            "repeated_visit_count",
            "repeated_visit_rate",
            "duration_observation_count",
            "elapsed_seconds_minimum",
            "elapsed_seconds_mean",
            "elapsed_seconds_median",
            "elapsed_seconds_p75",
            "elapsed_seconds_p90",
            "elapsed_seconds_maximum",
            "source_handoff_share",
        ]
    )
