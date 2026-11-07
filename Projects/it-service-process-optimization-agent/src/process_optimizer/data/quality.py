"""Deterministic data-quality validation for cases and events."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from process_optimizer.data.constants import (
    CASE_ATTRIBUTE_PREFIX,
    CASE_ID_COLUMN,
    EVENT_ATTRIBUTE_PREFIX,
    EVENT_COUNT_COLUMN,
    EVENT_ID_COLUMN,
    EVENT_INDEX_COLUMN,
    REFERENCE_CASE_COUNT,
    REFERENCE_EVENT_COUNT,
    XES_ACTIVITY_ATTRIBUTE,
    XES_CASE_IDENTIFIER,
    XES_TIMESTAMP_ATTRIBUTE,
)

Check = dict[str, Any]


def _check(status: str, **metrics: Any) -> Check:
    return {"status": status, "metrics": metrics}


def _status_for_count(count: int, *, failure: bool = False) -> str:
    if count == 0:
        return "pass"
    return "fail" if failure else "warn"


def _missing_profile(frame: pd.DataFrame, columns: Iterable[str]) -> dict[str, Any]:
    total = len(frame)
    result: dict[str, Any] = {}
    for column in sorted(columns):
        missing = int(frame[column].isna().sum())
        result[column] = {
            "missing_count": missing,
            "missing_percentage": round((missing / total * 100) if total else 0.0, 6),
        }
    return result


def _out_of_order_count(events: pd.DataFrame, timestamp_column: str) -> int:
    if timestamp_column not in events:
        return 0

    violations = 0
    ordered = events.sort_values([CASE_ID_COLUMN, EVENT_INDEX_COLUMN], kind="stable")
    for _, group in ordered.groupby(CASE_ID_COLUMN, sort=False):
        previous: Any = None
        for timestamp in group[timestamp_column]:
            if pd.isna(timestamp):
                continue
            if previous is not None:
                try:
                    violations += int(timestamp < previous)
                except TypeError:
                    violations += 1
            previous = timestamp
    return violations


def _unexpected_types(schema_profile: dict[str, Any]) -> list[dict[str, Any]]:
    unexpected: list[dict[str, Any]] = []
    for scope in ("case", "event"):
        attributes = schema_profile.get(scope, {}).get("attributes", {})
        for source_name, metadata in attributes.items():
            types = metadata.get("source_types", [])
            flagged = metadata.get("unexpected_types", [])
            if flagged or len(types) > 1:
                unexpected.append(
                    {
                        "scope": scope,
                        "attribute": source_name,
                        "source_types": types,
                        "unsupported_types": flagged,
                    }
                )
    return unexpected


def evaluate_data_quality(
    cases: pd.DataFrame,
    events: pd.DataFrame,
    schema_profile: dict[str, Any],
    *,
    source_case_count: int,
    source_event_count: int,
    input_file_exists: bool = True,
    gzip_valid: bool = True,
    xes_parsed: bool = True,
) -> dict[str, Any]:
    """Evaluate quality without logging or returning source attribute values."""
    checks: dict[str, Check] = {
        "input_file_exists": _check("pass" if input_file_exists else "fail"),
        "gzip_integrity": _check("pass" if gzip_valid else "fail"),
        "xes_parsing": _check("pass" if xes_parsed else "fail"),
    }

    duplicate_case_ids = int(cases[CASE_ID_COLUMN].duplicated(keep=False).sum())
    missing_case_ids = int(cases[CASE_ID_COLUMN].isna().sum())
    checks["unique_generated_case_ids"] = _check(
        _status_for_count(duplicate_case_ids + missing_case_ids, failure=True),
        duplicate_count=duplicate_case_ids,
        missing_count=missing_case_ids,
    )

    duplicate_event_ids = int(events[EVENT_ID_COLUMN].duplicated(keep=False).sum())
    missing_event_ids = int(events[EVENT_ID_COLUMN].isna().sum())
    checks["unique_generated_event_ids"] = _check(
        _status_for_count(duplicate_event_ids + missing_event_ids, failure=True),
        duplicate_count=duplicate_event_ids,
        missing_count=missing_event_ids,
    )

    known_case_ids = set(cases[CASE_ID_COLUMN])
    orphan_events = int((~events[CASE_ID_COLUMN].isin(known_case_ids)).sum())
    checks["event_to_case_relationships"] = _check(
        _status_for_count(orphan_events, failure=True),
        orphan_event_count=orphan_events,
    )

    source_case_column = f"{CASE_ATTRIBUTE_PREFIX}{XES_CASE_IDENTIFIER}"
    if source_case_column in cases:
        source_case_missing = int(cases[source_case_column].isna().sum())
        source_case_duplicates = int(
            cases[source_case_column].dropna().duplicated(keep=False).sum()
        )
        source_case_status = _status_for_count(source_case_missing + source_case_duplicates)
    else:
        source_case_missing = len(cases)
        source_case_duplicates = 0
        source_case_status = "warn"
    checks["source_case_identifiers"] = _check(
        source_case_status,
        attribute_present=source_case_column in cases,
        missing_count=source_case_missing,
        duplicate_count=source_case_duplicates,
    )

    timestamp_column = f"{EVENT_ATTRIBUTE_PREFIX}{XES_TIMESTAMP_ATTRIBUTE}"
    timestamp_missing = (
        int(events[timestamp_column].isna().sum()) if timestamp_column in events else len(events)
    )
    checks["event_timestamps"] = _check(
        _status_for_count(timestamp_missing),
        attribute_present=timestamp_column in events,
        missing_count=timestamp_missing,
        missing_percentage=round(
            (timestamp_missing / len(events) * 100) if len(events) else 0.0,
            6,
        ),
    )

    activity_column = f"{EVENT_ATTRIBUTE_PREFIX}{XES_ACTIVITY_ATTRIBUTE}"
    activity_missing = (
        int(events[activity_column].isna().sum()) if activity_column in events else len(events)
    )
    checks["event_activities"] = _check(
        _status_for_count(activity_missing),
        attribute_present=activity_column in events,
        missing_count=activity_missing,
        missing_percentage=round(
            (activity_missing / len(events) * 100) if len(events) else 0.0,
            6,
        ),
    )

    original_event_columns = [
        column for column in events if column.startswith(EVENT_ATTRIBUTE_PREFIX)
    ]
    duplicate_subset = [CASE_ID_COLUMN, *original_event_columns]
    duplicate_events = int(events.duplicated(subset=duplicate_subset, keep=False).sum())
    checks["duplicate_events"] = _check(
        _status_for_count(duplicate_events),
        duplicate_event_rows=duplicate_events,
    )

    if timestamp_column in events:
        timestamp_rows = events.loc[events[timestamp_column].notna()]
        duplicate_timestamp_mask = timestamp_rows.duplicated(
            subset=[CASE_ID_COLUMN, timestamp_column], keep=False
        )
        duplicate_timestamp_events = int(duplicate_timestamp_mask.sum())
        duplicate_timestamp_groups = int(
            timestamp_rows.loc[duplicate_timestamp_mask]
            .groupby([CASE_ID_COLUMN, timestamp_column], dropna=False)
            .ngroups
        )
    else:
        duplicate_timestamp_events = 0
        duplicate_timestamp_groups = 0
    checks["duplicate_timestamps_within_case"] = _check(
        _status_for_count(duplicate_timestamp_events),
        event_count=duplicate_timestamp_events,
        group_count=duplicate_timestamp_groups,
        interpretation="ties are preserved by event_index and are not removed",
    )

    order_violations = _out_of_order_count(events, timestamp_column)
    checks["chronological_source_order"] = _check(
        _status_for_count(order_violations),
        adjacent_order_violation_count=order_violations,
        ordering_rule="source trace order; event_index resolves timestamp ties",
    )

    unexpected_types = _unexpected_types(schema_profile)
    checks["unexpected_data_types"] = _check(
        _status_for_count(len(unexpected_types)),
        attribute_count=len(unexpected_types),
        attributes=unexpected_types,
    )

    case_columns = [column for column in cases if column.startswith(CASE_ATTRIBUTE_PREFIX)]
    event_columns = [column for column in events if column.startswith(EVENT_ATTRIBUTE_PREFIX)]
    case_missing_profile = _missing_profile(cases, case_columns)
    event_missing_profile = _missing_profile(events, event_columns)
    attributes_with_missing_values = sum(
        item["missing_count"] > 0
        for item in [*case_missing_profile.values(), *event_missing_profile.values()]
    )
    checks["missing_value_percentages"] = _check(
        _status_for_count(attributes_with_missing_values),
        attributes_with_missing_values=attributes_with_missing_values,
        case_attributes=case_missing_profile,
        event_attributes=event_missing_profile,
    )

    event_count_sum = int(cases[EVENT_COUNT_COLUMN].sum()) if len(cases) else 0
    actual_events_by_case = events.groupby(CASE_ID_COLUMN).size()
    declared_events_by_case = cases.set_index(CASE_ID_COLUMN)[EVENT_COUNT_COLUMN]
    actual_counts = actual_events_by_case.reindex(declared_events_by_case.index, fill_value=0)
    per_case_count_mismatches = int((actual_counts != declared_events_by_case).sum())
    reconciliation_failures = sum(
        (
            len(cases) != source_case_count,
            len(events) != source_event_count,
            event_count_sum != len(events),
            per_case_count_mismatches > 0,
        )
    )
    checks["row_count_reconciliation"] = _check(
        "fail" if reconciliation_failures else "pass",
        source_case_count=source_case_count,
        output_case_count=len(cases),
        source_event_count=source_event_count,
        output_event_count=len(events),
        case_event_count_sum=event_count_sum,
        per_case_count_mismatch_count=per_case_count_mismatches,
    )

    incomplete_cases = int((cases[EVENT_COUNT_COLUMN] == 0).sum())
    checks["incomplete_cases"] = _check(
        _status_for_count(incomplete_cases),
        zero_event_case_count=incomplete_cases,
    )

    case_discrepancy = len(cases) - REFERENCE_CASE_COUNT
    event_discrepancy = len(events) - REFERENCE_EVENT_COUNT
    checks["published_reference_counts"] = _check(
        "pass" if case_discrepancy == 0 and event_discrepancy == 0 else "warn",
        reference_is_approximate=True,
        reference_case_count=REFERENCE_CASE_COUNT,
        actual_case_count=len(cases),
        case_discrepancy=case_discrepancy,
        reference_event_count=REFERENCE_EVENT_COUNT,
        actual_event_count=len(events),
        event_discrepancy=event_discrepancy,
        rule="actual records are never altered to match reference counts",
    )

    statuses = [check["status"] for check in checks.values()]
    overall_status = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "overall_status": overall_status,
        "checks": checks,
    }
