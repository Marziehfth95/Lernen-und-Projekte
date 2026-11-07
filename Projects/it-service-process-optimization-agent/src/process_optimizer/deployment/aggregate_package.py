"""Build and verify an aggregate-only, reproducible cloud deployment package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet

from process_optimizer.tools.manifest import (
    APPROVED_AGGREGATE_ARTIFACTS,
    AggregateArtifactVerifier,
    AggregateManifestV1,
)

MANIFEST_FILENAME = "aggregate_manifest.json"
PACKAGE_FILENAMES = frozenset((*APPROVED_AGGREGATE_ARTIFACTS, MANIFEST_FILENAME))
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "case_id",
        "case:concept:name",
        "event_id",
        "org:resource",
        "resource_id",
        "resource_identifier",
    }
)


class AggregatePackageError(ValueError):
    """Raised when a deployment package is incomplete or contains record-level data."""


def verify_deployable_package(package_dir: Path) -> AggregateManifestV1:
    """Fail closed unless a directory is the exact governed aggregate package."""
    root = Path(package_dir)
    if not root.is_dir() or root.is_symlink():
        raise AggregatePackageError("Aggregate package must be a real directory")
    entries = {item.name for item in root.iterdir()}
    if entries != PACKAGE_FILENAMES:
        raise AggregatePackageError("Aggregate package does not contain the exact allowlist")
    for name in sorted(PACKAGE_FILENAMES):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise AggregatePackageError("Aggregate package contains a non-regular file")

    manifest_path = root / MANIFEST_FILENAME
    manifest = AggregateManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    AggregateArtifactVerifier(root, manifest).verify()
    _reject_record_level_fields(root)
    return manifest


def build_deployable_package(
    analysis_dir: Path,
    output_dir: Path,
    *,
    replace: bool = False,
) -> AggregateManifestV1:
    """Copy only verified aggregate artifacts into an atomically staged directory."""
    source = Path(analysis_dir).resolve()
    destination = Path(output_dir).resolve()
    if source == destination or source in destination.parents:
        raise AggregatePackageError("Package destination must be separate from analytical inputs")
    if not source.is_dir() or source.is_symlink():
        raise AggregatePackageError("Analysis directory must be a real directory")

    manifest_path = source / MANIFEST_FILENAME
    manifest = AggregateManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    AggregateArtifactVerifier(source, manifest).verify()
    for name in PACKAGE_FILENAMES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise AggregatePackageError("Analytical input contains a non-regular artifact")
    _reject_record_level_fields(source)

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="aggregate-package-", dir=destination.parent))
    try:
        for name in sorted(PACKAGE_FILENAMES):
            target = stage / name
            shutil.copyfile(source / name, target)
            target.chmod(0o444)
        verify_deployable_package(stage)
        if destination.exists():
            if not replace:
                raise AggregatePackageError("Package destination already exists")
            if destination.is_symlink() or not destination.is_dir():
                raise AggregatePackageError("Refusing to replace a non-directory destination")
            shutil.rmtree(destination)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return verify_deployable_package(destination)


def _reject_record_level_fields(root: Path) -> None:
    for name in APPROVED_AGGREGATE_ARTIFACTS:
        path = root / name
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            discovered = _json_keys(payload)
        else:
            discovered = set(parquet.read_schema(path).names)
        forbidden = {field.lower() for field in discovered}.intersection(FORBIDDEN_FIELD_NAMES)
        if forbidden:
            raise AggregatePackageError(
                "Aggregate package contains prohibited record identifier fields"
            )


def _json_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_json_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_json_keys(child))
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        manifest = verify_deployable_package(args.output_dir)
    else:
        if args.analysis_dir is None:
            parser.error("--analysis-dir is required unless --verify-only is used")
        manifest = build_deployable_package(
            args.analysis_dir,
            args.output_dir,
            replace=args.replace,
        )
    print(
        json.dumps(
            {
                "status": "verified",
                "analysis_run_id": manifest.analysis_run_id,
                "artifact_count": len(manifest.artifacts),
                "schema_version": manifest.schema_version,
                "evidence_contract_version": manifest.evidence_contract_version,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
