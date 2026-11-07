"""Synthetic-only tests for the XES ingestion pipeline."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from process_optimizer.data.constants import (
    CASE_ID_COLUMN,
    EVENT_ATTRIBUTE_PREFIX,
    EVENT_ID_COLUMN,
    XES_TIMESTAMP_ATTRIBUTE,
)
from process_optimizer.data.errors import GzipIntegrityError, XESParsingError
from process_optimizer.data.ingestion import ingest_xes, prepare_dataset, validate_processed
from process_optimizer.data.quality import evaluate_data_quality


def test_valid_ingestion_writes_governed_artifacts(
    tmp_path: Path,
    xes_factory,  # type: ignore[no-untyped-def]
    valid_synthetic_cases,  # type: ignore[no-untyped-def]
) -> None:
    source = xes_factory(valid_synthetic_cases)
    output = tmp_path / "processed"

    result = ingest_xes(source, output)

    assert result.case_count == 2
    assert result.event_count == 3
    assert result.quality_status == "warn"
    assert result.cases_path.is_file()
    assert result.events_path.is_file()
    assert result.schema_profile_path.is_file()
    assert result.manifest_path.is_file()
    assert result.quality_report_path.is_file()

    events = pd.read_parquet(result.events_path)
    timestamp_column = f"{EVENT_ATTRIBUTE_PREFIX}{XES_TIMESTAMP_ATTRIBUTE}"
    assert timestamp_column in events
    assert str(events[timestamp_column].dtype).endswith("UTC]")

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["modified"] is False
    assert manifest["record_counts"] == {"cases": 2, "events": 3}
    assert set(manifest["outputs"]) == {
        "cases",
        "events",
        "schema_profile",
        "data_quality_report",
    }
    validation = validate_processed(source, output)
    assert validation["checks"]["manifest_artifact_integrity"]["status"] == "pass"


def test_missing_activity_and_timestamp_fields_are_reported(
    xes_factory,  # type: ignore[no-untyped-def]
) -> None:
    source = xes_factory(
        [
            (
                {"concept:name": ("string", "SYNTHETIC-MISSING")},
                [
                    {"concept:name": ("string", "Only activity")},
                    {"time:timestamp": ("date", "2020-01-01T00:00:00Z")},
                ],
            )
        ]
    )
    prepared = prepare_dataset(source)
    report = _quality(prepared)

    assert report["checks"]["event_timestamps"]["metrics"]["missing_count"] == 1
    assert report["checks"]["event_activities"]["metrics"]["missing_count"] == 1
    assert report["checks"]["event_timestamps"]["status"] == "warn"
    assert report["checks"]["missing_value_percentages"]["status"] == "warn"


def test_duplicate_timestamps_are_counted_and_order_is_preserved(
    xes_factory,  # type: ignore[no-untyped-def]
    valid_synthetic_cases,  # type: ignore[no-untyped-def]
) -> None:
    prepared = prepare_dataset(xes_factory(valid_synthetic_cases))
    report = _quality(prepared)

    duplicate_check = report["checks"]["duplicate_timestamps_within_case"]
    assert duplicate_check["status"] == "warn"
    assert duplicate_check["metrics"]["event_count"] == 2
    assert prepared.events["event_index"].tolist() == [0, 1, 0]


def test_event_ids_are_stable_across_repeated_ingestion(
    xes_factory,  # type: ignore[no-untyped-def]
    valid_synthetic_cases,  # type: ignore[no-untyped-def]
) -> None:
    source = xes_factory(valid_synthetic_cases)

    first = prepare_dataset(source)
    second = prepare_dataset(source)

    assert first.cases[CASE_ID_COLUMN].tolist() == second.cases[CASE_ID_COLUMN].tolist()
    assert first.events[EVENT_ID_COLUMN].tolist() == second.events[EVENT_ID_COLUMN].tolist()


def test_transformations_are_deterministic(
    xes_factory,  # type: ignore[no-untyped-def]
    valid_synthetic_cases,  # type: ignore[no-untyped-def]
) -> None:
    source = xes_factory(valid_synthetic_cases)

    first = prepare_dataset(source)
    second = prepare_dataset(source)

    pd.testing.assert_frame_equal(first.cases, second.cases)
    pd.testing.assert_frame_equal(first.events, second.events)
    assert first.source_checksum_sha256 == second.source_checksum_sha256


def test_incomplete_zero_event_case_is_reported(
    xes_factory,  # type: ignore[no-untyped-def]
) -> None:
    source = xes_factory(
        [({"concept:name": ("string", "SYNTHETIC-INCOMPLETE")}, [])]
    )
    prepared = prepare_dataset(source)
    report = _quality(prepared)

    assert report["checks"]["incomplete_cases"]["status"] == "warn"
    assert report["checks"]["incomplete_cases"]["metrics"]["zero_event_case_count"] == 1


def test_broken_event_to_case_relationship_is_a_failure(
    xes_factory,  # type: ignore[no-untyped-def]
    valid_synthetic_cases,  # type: ignore[no-untyped-def]
) -> None:
    prepared = prepare_dataset(xes_factory(valid_synthetic_cases))
    broken_events = prepared.events.copy()
    broken_events.loc[0, CASE_ID_COLUMN] = "case_nonexistent"

    report = evaluate_data_quality(
        prepared.cases,
        broken_events,
        prepared.schema_profile,
        source_case_count=prepared.source_case_count,
        source_event_count=prepared.source_event_count,
    )

    relationship_check = report["checks"]["event_to_case_relationships"]
    assert relationship_check["status"] == "fail"
    assert relationship_check["metrics"]["orphan_event_count"] == 1
    assert report["overall_status"] == "fail"


def test_invalid_gzip_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "invalid.xes.gz"
    source.write_bytes(b"this is synthetic invalid gzip content")

    with pytest.raises(GzipIntegrityError):
        prepare_dataset(source)


def test_malformed_xes_is_rejected_after_valid_gzip(tmp_path: Path) -> None:
    source = tmp_path / "malformed.xes.gz"
    with gzip.GzipFile(filename=source, mode="wb", mtime=0) as stream:
        stream.write(b"<log><trace>")

    with pytest.raises(XESParsingError):
        prepare_dataset(source)


def test_missing_pm4py_names_the_ingestion_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "synthetic.xes.gz"
    with gzip.GzipFile(filename=source, mode="wb", mtime=0) as stream:
        stream.write(b"<log></log>")
    # Runtime images ship without PM4Py; ingestion must say so instead of blaming the file.
    monkeypatch.setitem(sys.modules, "pm4py", None)

    with pytest.raises(XESParsingError, match="'ingestion' extra"):
        prepare_dataset(source)


def _quality(prepared):  # type: ignore[no-untyped-def]
    return evaluate_data_quality(
        prepared.cases,
        prepared.events,
        prepared.schema_profile,
        source_case_count=prepared.source_case_count,
        source_event_count=prepared.source_event_count,
    )
