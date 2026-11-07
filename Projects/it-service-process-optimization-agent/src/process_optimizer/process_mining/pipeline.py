"""Governed deterministic analysis and semantic-validation output writing."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from process_optimizer.data.errors import DataPipelineError
from process_optimizer.process_mining.bottlenecks import detect_bottlenecks
from process_optimizer.process_mining.constants import (
    ANALYSIS_VERSION,
    COMPARATIVE_DIMENSIONS,
    DEFAULT_ANALYSIS_DIR,
    DEFAULT_PHASE2_DIR,
    REQUIRED_CASE_COLUMNS,
    REQUIRED_EVENT_COLUMNS,
    RESOURCE,
)
from process_optimizer.process_mining.evidence_contract import (
    ANALYSIS_CONFIGURATION_VERSION,
    analysis_run_id,
    build_evidence_contract,
)
from process_optimizer.process_mining.metrics import (
    build_comparative_analysis,
    build_directly_follows,
    build_handoff_analysis,
    build_process_overview,
    build_rework_analysis,
    build_temporal_analysis,
    build_variants,
    transition_duration_baseline,
)
from process_optimizer.process_mining.models import BottleneckConfig, TimePeriod
from process_optimizer.process_mining.semantic_validation import build_semantic_validation


class AnalysisInputError(DataPipelineError):
    """Raised when governed Phase 2 inputs are missing, altered, or incompatible."""


@dataclass(frozen=True)
class AnalysisResult:
    """Aggregate result safe for CLI output."""

    output_dir: Path
    case_count: int
    event_count: int
    variant_count: int
    finding_count: int
    semantic_pattern_count: int
    evidence_contract_finding_count: int


def run_process_mining(
    input_dir: Path = DEFAULT_PHASE2_DIR,
    output_dir: Path = DEFAULT_ANALYSIS_DIR,
    config: BottleneckConfig | None = None,
) -> AnalysisResult:
    """Verify governed inputs and produce deterministic Phase 3/3.5 artifacts."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    config = config or BottleneckConfig()
    verification = verify_phase2_artifacts(input_dir)
    manifest = verification["manifest"]

    case_columns = list(REQUIRED_CASE_COLUMNS)
    event_columns = list(REQUIRED_EVENT_COLUMNS)
    schema_event_columns = set(
        manifest.get("discovered_schema", {})
        .get("event", {})
        .get("parquet_schema", {})
    )
    event_columns.extend(
        dimension
        for dimension in COMPARATIVE_DIMENSIONS
        if dimension in schema_event_columns and dimension not in event_columns
    )
    event_columns = [column for column in event_columns if column != RESOURCE]

    try:
        cases = pd.read_parquet(input_dir / "cases.parquet", columns=case_columns)
        events = pd.read_parquet(input_dir / "events.parquet", columns=event_columns)
    except (OSError, ValueError, pa.ArrowException) as error:
        raise AnalysisInputError("Validated Phase 2 Parquet inputs could not be read") from error
    _validate_columns(cases, events)
    if len(cases) != manifest["record_counts"]["cases"]:
        raise AnalysisInputError("Case count differs from the ingestion manifest")
    if len(events) != manifest["record_counts"]["events"]:
        raise AnalysisInputError("Event count differs from the ingestion manifest")

    overview = build_process_overview(cases, events)
    variants = build_variants(events)
    directly_follows = build_directly_follows(events)
    handoffs = build_handoff_analysis(events)
    rework = build_rework_analysis(events, handoffs)
    comparative = build_comparative_analysis(events)
    temporal = build_temporal_analysis(events)
    duration_baseline = transition_duration_baseline(events)
    time_period = TimePeriod(**overview["observed_time_range"])
    evidence = detect_bottlenecks(
        directly_follows,
        handoffs.transitions,
        duration_baseline,
        dataset_sha256=manifest["source"]["sha256"],
        time_period=time_period,
        config=config,
    )
    semantic = build_semantic_validation(events)
    run_id = analysis_run_id(
        manifest["source"]["sha256"],
        {
            name: metadata["sha256"]
            for name, metadata in sorted(manifest["outputs"].items())
        },
        config.model_dump(mode="json"),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "process_overview": output_dir / "process_overview.json",
        "process_variants": output_dir / "process_variants.parquet",
        "directly_follows": output_dir / "directly_follows.parquet",
        "handoff_analysis": output_dir / "handoff_analysis.parquet",
        "bottleneck_findings": output_dir / "bottleneck_findings.json",
        "process_mining_report": output_dir / "process_mining_report.json",
        "lifecycle_patterns": output_dir / "lifecycle_patterns.parquet",
        "handoff_cohort_comparison": output_dir / "handoff_cohort_comparison.json",
        "semantic_validation_report": output_dir / "semantic_validation_report.json",
        "evidence_contract": output_dir / "evidence_contract.json",
    }
    _atomic_json(overview, output_paths["process_overview"])
    _atomic_parquet(variants, output_paths["process_variants"])
    _atomic_parquet(directly_follows, output_paths["directly_follows"])
    _atomic_parquet(handoffs.transitions, output_paths["handoff_analysis"])
    evidence_payload = evidence.model_dump(mode="json")
    _atomic_json(evidence_payload, output_paths["bottleneck_findings"])
    _atomic_parquet(semantic.lifecycle_patterns, output_paths["lifecycle_patterns"])
    _atomic_json(semantic.cohort_comparison, output_paths["handoff_cohort_comparison"])
    semantic_report = {
        "analysis_version": ANALYSIS_VERSION,
        "analysis_run_id": run_id,
        "analysis_configuration_version": ANALYSIS_CONFIGURATION_VERSION,
        "source_dataset_sha256": manifest["source"]["sha256"],
        "classification": "aggregate_observations_not_confirmed_causes",
        "validated_transitions": semantic.transition_summaries,
        "handoff_validation": semantic.handoff_validation,
        "cohort_comparison_artifact": "handoff_cohort_comparison.json",
        "privacy": {
            "raw_case_identifiers_exposed": False,
            "resource_identifiers_loaded": False,
            "outputs_are_aggregate_only": True,
        },
    }
    _atomic_json(semantic_report, output_paths["semantic_validation_report"])
    referenced_artifact_hashes = {
        path.name: _sha256(path)
        for path in (
            output_paths["directly_follows"],
            output_paths["handoff_analysis"],
            output_paths["lifecycle_patterns"],
            output_paths["handoff_cohort_comparison"],
        )
    }
    contract = build_evidence_contract(
        evidence,
        semantic.transition_summaries,
        semantic.cohort_comparison,
        run_id=run_id,
        artifact_hashes=referenced_artifact_hashes,
    )
    _atomic_json(contract.model_dump(mode="json"), output_paths["evidence_contract"])

    report = {
        "analysis_version": ANALYSIS_VERSION,
        "analysis_run_id": run_id,
        "analysis_configuration_version": ANALYSIS_CONFIGURATION_VERSION,
        "input_verification": {
            "dataset_sha256": manifest["source"]["sha256"],
            "manifest_artifact_hashes_verified": True,
            "phase2_pipeline_version": manifest["pipeline_version"],
            "phase2_quality_status": manifest["validation_results"]["overall_status"],
            "resource_attribute_loaded": False,
        },
        "assumptions": [
            "Activity is the exact original_event__concept:name value.",
            "Lifecycle is retained separately and does not redefine activity.",
            "Event order is event_index within case, including timestamp ties.",
            "Durations are elapsed observed time, not active work or SLA performance.",
            "Group changes and bottleneck candidates are observations, not root causes.",
            "Missing group values are not imputed.",
        ],
        "process_overview": overview,
        "rework_analysis": rework,
        "handoff_coverage": {
            "eligible_consecutive_event_count": handoffs.eligible_consecutive_event_count,
            "same_group_consecutive_event_count": handoffs.same_group_consecutive_event_count,
            "unclassified_missing_group_count": handoffs.unclassified_missing_group_count,
        },
        "comparative_analysis": comparative,
        "temporal_analysis": temporal,
        "bottleneck_summary": {
            "finding_count": len(evidence.findings),
            "insufficient_sample_summary": evidence.insufficient_sample_summary,
            "baselines": evidence.baselines,
        },
        "semantic_validation_summary": {
            "lifecycle_pattern_count": len(semantic.lifecycle_patterns),
            "validated_transition_count": len(semantic.transition_summaries),
            "handoff_validation": semantic.handoff_validation,
            "evidence_contract_schema_version": contract.schema_version,
            "evidence_contract_finding_count": len(contract.findings),
        },
        "artifacts": {name: path.name for name, path in output_paths.items()},
    }
    _atomic_json(report, output_paths["process_mining_report"])
    return AnalysisResult(
        output_dir=output_dir,
        case_count=int(overview["case_count"]),
        event_count=int(overview["event_count"]),
        variant_count=len(variants),
        finding_count=len(evidence.findings),
        semantic_pattern_count=len(semantic.lifecycle_patterns),
        evidence_contract_finding_count=len(contract.findings),
    )


def verify_phase2_artifacts(input_dir: Path) -> dict[str, Any]:
    """Verify every governed Phase 2 output against its ingestion manifest."""
    manifest_path = Path(input_dir) / "ingestion_manifest.json"
    if not manifest_path.is_file():
        raise AnalysisInputError(f"Ingestion manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisInputError("Ingestion manifest is unreadable") from error

    mismatches = []
    for name, metadata in manifest.get("outputs", {}).items():
        artifact = Path(input_dir) / metadata["filename"]
        if not artifact.is_file() or _sha256(artifact) != metadata["sha256"]:
            mismatches.append(name)
    required = {"cases", "events", "schema_profile", "data_quality_report"}
    missing_manifest_entries = sorted(required - set(manifest.get("outputs", {})))
    if mismatches or missing_manifest_entries:
        raise AnalysisInputError(
            "Phase 2 artifact verification failed for: "
            + ", ".join(sorted([*mismatches, *missing_manifest_entries]))
        )
    return {"manifest": manifest, "verified_artifact_count": len(required)}


def _validate_columns(cases: pd.DataFrame, events: pd.DataFrame) -> None:
    missing_cases = sorted(set(REQUIRED_CASE_COLUMNS) - set(cases.columns))
    missing_events = sorted(set(REQUIRED_EVENT_COLUMNS) - set(events.columns))
    if missing_cases or missing_events:
        raise AnalysisInputError(
            "Required analytical columns are missing; "
            f"cases={missing_cases}, events={missing_events}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(payload: dict[str, Any], destination: Path) -> None:
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


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        frame.to_parquet(temporary_path, index=False, compression="zstd")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
