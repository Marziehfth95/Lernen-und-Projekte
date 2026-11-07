"""Reproducible PM4Py XES ingestion and artifact generation."""

from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from process_optimizer.data.constants import (
    CASE_ATTRIBUTE_PREFIX,
    CASE_ID_COLUMN,
    EVENT_ATTRIBUTE_PREFIX,
    EVENT_COUNT_COLUMN,
    EVENT_ID_COLUMN,
    EVENT_INDEX_COLUMN,
    PIPELINE_VERSION,
    SOURCE_URL,
    TRACE_INDEX_COLUMN,
)
from process_optimizer.data.errors import (
    DataPipelineError,
    GzipIntegrityError,
    InputFileMissingError,
    ProcessedDataError,
    XESParsingError,
)
from process_optimizer.data.profiling import SchemaProfiler
from process_optimizer.data.quality import evaluate_data_quality


@dataclass(frozen=True)
class PreparedDataset:
    """In-memory deterministic representation of one immutable XES input."""

    cases: pd.DataFrame
    events: pd.DataFrame
    case_table: pa.Table
    event_table: pa.Table
    schema_profile: dict[str, Any]
    source_checksum_sha256: str
    source_case_count: int
    source_event_count: int


@dataclass(frozen=True)
class IngestionResult:
    """Paths and aggregate results safe to display to an operator."""

    cases_path: Path
    events_path: Path
    schema_profile_path: Path
    manifest_path: Path
    quality_report_path: Path
    case_count: int
    event_count: int
    quality_status: str


def sha256_file(path: Path) -> str:
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_gzip(path: Path) -> None:
    """Read the complete gzip stream so footer and CRC failures are detected."""
    if not path.is_file():
        raise InputFileMissingError(f"Input XES.GZ file does not exist: {path}")
    try:
        with gzip.open(path, "rb") as stream:
            for _ in iter(lambda: stream.read(1024 * 1024), b""):
                pass
    except (OSError, EOFError) as error:
        raise GzipIntegrityError(f"Input is not a valid complete gzip stream: {path}") from error


def _canonical_value(value: Any) -> Any:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, datetime):
        normalized = _normalize_datetime(value)[0]
        return {"type": "datetime", "value": normalized.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"type": "float", "value": str(value)}
    if isinstance(value, Mapping):
        return {
            "type": "mapping",
            "value": {
                str(key): _canonical_value(item) for key, item in sorted(value.items(), key=str)
            },
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return {"type": "sequence", "value": [_canonical_value(item) for item in value]}
    return {"type": type(value).__name__, "value": value}


def _canonical_json(value: Any) -> str:
    return json.dumps(_canonical_value(value), sort_keys=True, separators=(",", ":"))


def _normalize_datetime(value: datetime) -> tuple[datetime, str]:
    if value.tzinfo is None or value.utcoffset() is None:
        return value, "timezone-naive datetime preserved; source timezone is unspecified"
    return value.astimezone(UTC), "timezone-aware datetime converted to UTC"


def _normalize_attribute(value: Any) -> tuple[Any, str]:
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if isinstance(value, (str, bool, int, float, bytes, date)) or value is None:
        return value, "preserved"
    return _canonical_json(value), "complex value encoded as deterministic typed JSON"


def _stable_identifier(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(_canonical_json(part).encode("utf-8"))
        digest.update(b"\x1f")
    return f"{prefix}_{digest.hexdigest()[:32]}"


def _read_xes(path: Path) -> Any:
    try:
        import pm4py
    except ImportError as error:
        raise XESParsingError(
            "PM4Py is not installed; install the 'ingestion' extra for offline XES ingestion"
        ) from error
    try:
        return pm4py.read_xes(
            str(path),
            return_legacy_log_object=True,
            show_progress_bar=False,
        )
    except Exception as error:
        raise XESParsingError(f"PM4Py could not parse the XES event log: {path}") from error


def _to_arrow_table(frame: pd.DataFrame, scope: str) -> pa.Table:
    try:
        return pa.Table.from_pandas(frame, preserve_index=False)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise DataPipelineError(
            f"Discovered {scope} attribute types cannot be represented safely in Parquet"
        ) from error


def prepare_dataset(input_path: Path) -> PreparedDataset:
    """Validate, parse, normalize, and profile an immutable XES.GZ input."""
    input_path = Path(input_path)
    validate_gzip(input_path)
    checksum = sha256_file(input_path)
    event_log = _read_xes(input_path)
    profiler = SchemaProfiler()

    case_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    source_event_count = 0

    for trace_index, trace in enumerate(event_log):
        trace_attributes = dict(getattr(trace, "attributes", {}))
        case_id = _stable_identifier("case", checksum, trace_index, trace_attributes)
        case_row: dict[str, Any] = {
            CASE_ID_COLUMN: case_id,
            TRACE_INDEX_COLUMN: trace_index,
            EVENT_COUNT_COLUMN: len(trace),
        }
        profiler.begin_record("case")
        for source_name, source_value in trace_attributes.items():
            storage_value, transformation = _normalize_attribute(source_value)
            column = f"{CASE_ATTRIBUTE_PREFIX}{source_name}"
            case_row[column] = storage_value
            profiler.observe(
                "case", str(source_name), source_value, storage_value, transformation
            )
        case_rows.append(case_row)

        for event_index, event in enumerate(trace):
            event_attributes = dict(event)
            event_id = _stable_identifier(
                "event",
                checksum,
                case_id,
                event_index,
                event_attributes,
            )
            event_row: dict[str, Any] = {
                EVENT_ID_COLUMN: event_id,
                CASE_ID_COLUMN: case_id,
                EVENT_INDEX_COLUMN: event_index,
            }
            profiler.begin_record("event")
            for source_name, source_value in event_attributes.items():
                storage_value, transformation = _normalize_attribute(source_value)
                column = f"{EVENT_ATTRIBUTE_PREFIX}{source_name}"
                event_row[column] = storage_value
                profiler.observe(
                    "event", str(source_name), source_value, storage_value, transformation
                )
            event_rows.append(event_row)
            source_event_count += 1

    case_attribute_columns = sorted(
        {column for row in case_rows for column in row if column.startswith(CASE_ATTRIBUTE_PREFIX)}
    )
    event_attribute_columns = sorted(
        {
            column
            for row in event_rows
            for column in row
            if column.startswith(EVENT_ATTRIBUTE_PREFIX)
        }
    )
    cases = pd.DataFrame(
        case_rows,
        columns=[
            CASE_ID_COLUMN,
            TRACE_INDEX_COLUMN,
            EVENT_COUNT_COLUMN,
            *case_attribute_columns,
        ],
    )
    events = pd.DataFrame(
        event_rows,
        columns=[
            EVENT_ID_COLUMN,
            CASE_ID_COLUMN,
            EVENT_INDEX_COLUMN,
            *event_attribute_columns,
        ],
    )
    case_table = _to_arrow_table(cases, "case")
    event_table = _to_arrow_table(events, "event")

    generated_at = datetime.now(UTC).isoformat()
    schema_profile = {
        "generated_at_utc": generated_at,
        "pipeline_version": PIPELINE_VERSION,
        "source": {
            "url": SOURCE_URL,
            "input_filename": input_path.name,
            "sha256": checksum,
        },
        **profiler.to_dict(case_table.schema, event_table.schema),
    }
    return PreparedDataset(
        cases=cases,
        events=events,
        case_table=case_table,
        event_table=event_table,
        schema_profile=schema_profile,
        source_checksum_sha256=checksum,
        source_case_count=len(case_rows),
        source_event_count=source_event_count,
    )


def _atomic_write_json(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True, ensure_ascii=False)
            temporary.write("\n")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_parquet(table: pa.Table, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        pq.write_table(table, temporary_path, compression="zstd")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _library_versions() -> dict[str, str]:
    packages = ("pandas", "pm4py", "pyarrow")
    return {
        "python": platform.python_version(),
        **{package: importlib.metadata.version(package) for package in packages},
    }


def _quality_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "overall_status": report["overall_status"],
        "check_statuses": {
            name: result["status"] for name, result in report["checks"].items()
        },
    }


def _build_manifest(
    prepared: PreparedDataset,
    input_path: Path,
    output_paths: dict[str, Path],
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "ingestion_timestamp_utc": datetime.now(UTC).isoformat(),
        "source": {
            "url": SOURCE_URL,
            "input_filename": input_path.name,
            "sha256": prepared.source_checksum_sha256,
            "modified": False,
        },
        "library_versions": _library_versions(),
        "outputs": {
            name: {
                "filename": path.name,
                "sha256": sha256_file(path),
            }
            for name, path in output_paths.items()
        },
        "record_counts": {
            "cases": len(prepared.cases),
            "events": len(prepared.events),
        },
        "transformation_rules": [
            "Original trace attributes use the original_case__ prefix.",
            "Original event attributes use the original_event__ prefix.",
            "Timezone-aware datetimes are converted to UTC; timezone-naive values are preserved.",
            "Source trace order is preserved by source_trace_index and event_index.",
            (
                "Stable IDs are SHA-256-derived from source checksum, source order, "
                "and source attributes."
            ),
            "Complex non-scalar values are retained as deterministic typed JSON when encountered.",
            "No input rows are added, deleted, reordered, or changed to match reference counts.",
        ],
        "discovered_schema": prepared.schema_profile,
        "validation_results": _quality_summary(quality_report),
    }


def write_schema_profile(input_path: Path, output_dir: Path) -> Path:
    """Parse the source and write only aggregate schema metadata."""
    prepared = prepare_dataset(input_path)
    destination = Path(output_dir) / "schema_profile.json"
    _atomic_write_json(prepared.schema_profile, destination)
    return destination


def ingest_xes(input_path: Path, output_dir: Path) -> IngestionResult:
    """Run the full deterministic ingestion and validation pipeline."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    prepared = prepare_dataset(input_path)

    cases_path = output_dir / "cases.parquet"
    events_path = output_dir / "events.parquet"
    schema_path = output_dir / "schema_profile.json"
    quality_path = output_dir / "data_quality_report.json"
    manifest_path = output_dir / "ingestion_manifest.json"

    _atomic_write_parquet(prepared.case_table, cases_path)
    _atomic_write_parquet(prepared.event_table, events_path)
    _atomic_write_json(prepared.schema_profile, schema_path)

    quality_report = evaluate_data_quality(
        prepared.cases,
        prepared.events,
        prepared.schema_profile,
        source_case_count=prepared.source_case_count,
        source_event_count=prepared.source_event_count,
    )
    _atomic_write_json(quality_report, quality_path)

    manifest = _build_manifest(
        prepared,
        input_path,
        {
            "cases": cases_path,
            "events": events_path,
            "schema_profile": schema_path,
            "data_quality_report": quality_path,
        },
        quality_report,
    )
    _atomic_write_json(manifest, manifest_path)

    return IngestionResult(
        cases_path=cases_path,
        events_path=events_path,
        schema_profile_path=schema_path,
        manifest_path=manifest_path,
        quality_report_path=quality_path,
        case_count=len(prepared.cases),
        event_count=len(prepared.events),
        quality_status=quality_report["overall_status"],
    )


def validate_processed(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """Re-parse the immutable source and validate existing Parquet outputs."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    prepared = prepare_dataset(input_path)
    cases_path = output_dir / "cases.parquet"
    events_path = output_dir / "events.parquet"
    if not cases_path.is_file() or not events_path.is_file():
        raise ProcessedDataError(
            f"Processed Parquet files are missing from configured directory: {output_dir}"
        )
    try:
        cases = pd.read_parquet(cases_path)
        events = pd.read_parquet(events_path)
    except (OSError, ValueError, pa.ArrowException) as error:
        raise ProcessedDataError("Processed Parquet files could not be read") from error

    report = evaluate_data_quality(
        cases,
        events,
        prepared.schema_profile,
        source_case_count=prepared.source_case_count,
        source_event_count=prepared.source_event_count,
    )
    report["checks"]["processed_file_checksums"] = _check_processed_files(
        cases_path,
        events_path,
    )
    report["checks"]["manifest_artifact_integrity"] = _check_manifest_artifacts(output_dir)
    statuses = [item["status"] for item in report["checks"].values()]
    report["overall_status"] = (
        "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    )
    return report


def _check_processed_files(cases_path: Path, events_path: Path) -> dict[str, Any]:
    return {
        "status": "pass",
        "metrics": {
            "cases_sha256": sha256_file(cases_path),
            "events_sha256": sha256_file(events_path),
        },
    }


def _check_manifest_artifacts(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "ingestion_manifest.json"
    if not manifest_path.is_file():
        return {
            "status": "fail",
            "metrics": {"manifest_present": False, "mismatched_artifacts": []},
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outputs = manifest["outputs"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ProcessedDataError("Ingestion manifest could not be read") from error

    mismatched: list[str] = []
    for name, metadata in outputs.items():
        artifact_path = output_dir / metadata["filename"]
        if not artifact_path.is_file() or sha256_file(artifact_path) != metadata["sha256"]:
            mismatched.append(name)
    return {
        "status": "fail" if mismatched else "pass",
        "metrics": {
            "manifest_present": True,
            "checked_artifact_count": len(outputs),
            "mismatched_artifacts": sorted(mismatched),
        },
    }
