"""Create and verify a deployable package of aggregate-only analytical artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from process_optimizer.process_mining.evidence_contract import EvidenceContractV1

AGGREGATE_MANIFEST_VERSION = "1.0.0"
APPROVED_AGGREGATE_ARTIFACTS = (
    "evidence_contract.json",
    "process_overview.json",
    "handoff_analysis.parquet",
    "handoff_cohort_comparison.json",
    "lifecycle_patterns.parquet",
    "semantic_validation_report.json",
)


class AggregateArtifactV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str = Field(pattern=r"^[a-z0-9_]+\.(json|parquet)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AggregateManifestV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = AGGREGATE_MANIFEST_VERSION
    analysis_run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    evidence_contract_version: Literal["1.0.0"] = "1.0.0"
    source_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: tuple[AggregateArtifactV1, ...]


def build_aggregate_manifest(analysis_dir: Path) -> AggregateManifestV1:
    """Hash only the fixed aggregate allowlist; raw and record-level files are excluded."""
    root = Path(analysis_dir)
    evidence = EvidenceContractV1.model_validate_json(
        (root / "evidence_contract.json").read_text(encoding="utf-8")
    )
    artifacts = tuple(
        AggregateArtifactV1(filename=name, sha256=_sha256(root / name))
        for name in APPROVED_AGGREGATE_ARTIFACTS
    )
    return AggregateManifestV1(
        analysis_run_id=evidence.analysis_run_id,
        source_dataset_sha256=evidence.source_dataset.sha256,
        artifacts=artifacts,
    )


class AggregateArtifactVerifier:
    """Fail closed unless every approved aggregate exactly matches its manifest hash."""

    def __init__(self, analysis_dir: Path, manifest: AggregateManifestV1) -> None:
        self.analysis_dir = Path(analysis_dir)
        self.manifest = manifest

    @classmethod
    def from_file(cls, analysis_dir: Path, manifest_path: Path) -> AggregateArtifactVerifier:
        manifest = AggregateManifestV1.model_validate_json(
            Path(manifest_path).read_text(encoding="utf-8")
        )
        return cls(analysis_dir, manifest)

    def verify(self) -> EvidenceContractV1:
        expected_names = set(APPROVED_AGGREGATE_ARTIFACTS)
        actual_names = {item.filename for item in self.manifest.artifacts}
        if actual_names != expected_names:
            raise ValueError("Aggregate manifest does not contain the exact artifact allowlist")
        for artifact in self.manifest.artifacts:
            if _sha256(self.analysis_dir / artifact.filename) != artifact.sha256:
                raise ValueError("Aggregate artifact integrity verification failed")
        evidence = EvidenceContractV1.model_validate_json(
            (self.analysis_dir / "evidence_contract.json").read_text(encoding="utf-8")
        )
        if evidence.analysis_run_id != self.manifest.analysis_run_id:
            raise ValueError("Aggregate manifest analysis version mismatch")
        if evidence.source_dataset.sha256 != self.manifest.source_dataset_sha256:
            raise ValueError("Aggregate manifest provenance mismatch")
        return evidence


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_aggregate_manifest(args.analysis_dir)
    args.output.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "artifact_count": len(manifest.artifacts)}))


if __name__ == "__main__":
    main()
