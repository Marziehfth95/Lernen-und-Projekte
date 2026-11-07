"""Generate the synthetic CI aggregate package deterministically.

The package is built from invented Phase-2 inputs (no BPI Challenge record, no original
ticket, no protected database is read) by the real analysis pipeline, aggregate manifest
and deployable-package builder, so it has exactly the production package's schema. Its
dataset provenance says SYNTHETIC CI FIXTURE with an example.invalid URL, so it can never
be mistaken for evidence. Some synthetic groups are deliberately small, so the served
contract exercises small-group suppression.

    .venv/bin/python -I scripts/generate_synthetic_aggregate_package.py \
        --output-dir <new directory> --check tests/data/synthetic_ci_aggregate_fixture.json

--check fails unless every artifact hash, the served-contract pin and the finding counts
equal the reviewed values. Parquet bytes embed the writer version, so the reviewed values
belong to ci/requirements.lock; regenerate them with --write-expected only after review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REFUSED = ((REPO / "data").resolve(), (REPO / "deploy" / "aggregate-package").resolve())

FIXTURE_VERSION = "synthetic-ci-fixture-v1"
SEED = 20261101
CASE_COUNT = 600
SYNTHETIC_DATASET_NAME = "SYNTHETIC CI FIXTURE - NOT BPI CHALLENGE 2013 - NOT EVIDENCE"
SYNTHETIC_DATASET_URL = "https://example.invalid/synthetic-ci-fixture"
COMMON_GROUPS = tuple(f"SYNTH-G{index}" for index in range(1, 7))
# Used by only a handful of cases, so findings about it fall below the suppression threshold.
RARE_GROUP = "SYNTH-RARE"
PRODUCTS = tuple(f"SYNTH-P{index}" for index in range(1, 5))
ORGANIZATIONS = ("SYNTH-ORG-A", "SYNTH-ORG-B", "SYNTH-ORG-C")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_records() -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Invented cases and events; the activity vocabulary is the one the analysis expects."""
    rng = random.Random(SEED)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    cases: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []
    for number in range(CASE_COUNT):
        case_id = f"synthetic-case-{number:05d}"
        product, organization = rng.choice(PRODUCTS), rng.choice(ORGANIZATIONS)
        groups = [COMMON_GROUPS[0]]
        for _ in range(rng.randint(1, 4)):
            groups.append(rng.choice(COMMON_GROUPS))
        if number % 150 == 0:
            groups.append(RARE_GROUP)
        steps = [("Accepted", "In Progress", groups[0])]
        for group in groups[1:]:
            steps += [("Queued", "Awaiting Assignment", group), ("Accepted", "In Progress", group)]
        if rng.random() < 0.4:
            steps.append(("Completed", "Resolved", groups[-1]))
        steps.append(("Completed", "Closed", groups[-1]))
        returns = len(set(groups)) < len(groups)
        moment = start + timedelta(hours=rng.randint(0, 24 * 300))
        for index, (activity, lifecycle, group) in enumerate(steps):
            moment += timedelta(seconds=rng.randint(60, 86400 * (3 if returns else 1)))
            events.append(
                (
                    f"synthetic-event-{number:05d}-{index:02d}",
                    case_id,
                    index,
                    activity,
                    lifecycle,
                    group,
                    moment,
                    product,
                    organization,
                )
            )
        cases.append((case_id, len(steps)))
    return cases, events


def write_phase2_inputs(directory: Path) -> None:
    """Write the governed Phase-2 inputs the pipeline verifies, like the unit tests do."""
    import pandas as pd

    from process_optimizer.process_mining.constants import (
        ACTIVITY,
        CASE_ID,
        EVENT_ID,
        EVENT_INDEX,
        GROUP,
        LIFECYCLE,
        ORGANIZATION_INVOLVED,
        PRODUCT,
        TIMESTAMP,
    )

    cases, events = synthetic_records()
    case_frame = pd.DataFrame(cases, columns=[CASE_ID, "event_count"])
    event_frame = pd.DataFrame(
        events,
        columns=[
            EVENT_ID,
            CASE_ID,
            EVENT_INDEX,
            ACTIVITY,
            LIFECYCLE,
            GROUP,
            TIMESTAMP,
            PRODUCT,
            ORGANIZATION_INVOLVED,
        ],
    )
    paths = {
        "cases": directory / "cases.parquet",
        "events": directory / "events.parquet",
        "schema_profile": directory / "schema_profile.json",
        "data_quality_report": directory / "data_quality_report.json",
    }
    case_frame.to_parquet(paths["cases"], index=False)
    event_frame.to_parquet(paths["events"], index=False)
    schema = {
        "event": {"parquet_schema": {name: str(kind) for name, kind in event_frame.dtypes.items()}}
    }
    paths["schema_profile"].write_text(json.dumps(schema), encoding="utf-8")
    paths["data_quality_report"].write_text(
        json.dumps({"overall_status": "pass"}), encoding="utf-8"
    )
    source = hashlib.sha256(f"{FIXTURE_VERSION}:{SEED}:{CASE_COUNT}".encode()).hexdigest()
    manifest = {
        "pipeline_version": FIXTURE_VERSION,
        "source": {"sha256": source},
        "outputs": {
            name: {"filename": path.name, "sha256": _sha256(path)} for name, path in paths.items()
        },
        "record_counts": {"cases": len(case_frame), "events": len(event_frame)},
        "validation_results": {"overall_status": "pass"},
        "discovered_schema": schema,
    }
    (directory / "ingestion_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@contextmanager
def synthetic_provenance() -> Iterator[None]:
    """Label the contract's dataset as the synthetic fixture for this build only."""
    from process_optimizer.process_mining import evidence_contract

    original = (evidence_contract.DATASET_NAME, evidence_contract.DATASET_URL)
    evidence_contract.DATASET_NAME = SYNTHETIC_DATASET_NAME
    evidence_contract.DATASET_URL = SYNTHETIC_DATASET_URL
    try:
        yield
    finally:
        evidence_contract.DATASET_NAME, evidence_contract.DATASET_URL = original


def build_package(output_dir: Path) -> dict[str, Any]:
    from process_optimizer.api.aggregate import VerifiedAggregateService
    from process_optimizer.deployment.aggregate_package import build_deployable_package
    from process_optimizer.guardrails import evidence_contract_sha256
    from process_optimizer.process_mining.pipeline import run_process_mining
    from process_optimizer.tools.manifest import AggregateArtifactVerifier, build_aggregate_manifest

    with tempfile.TemporaryDirectory(prefix="synthetic-aggregates-") as temporary:
        phase2, analysis = Path(temporary) / "phase2", Path(temporary) / "analysis"
        phase2.mkdir()
        write_phase2_inputs(phase2)
        with synthetic_provenance():
            run_process_mining(phase2, analysis)
        manifest = build_aggregate_manifest(analysis)
        (analysis / "aggregate_manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        build_deployable_package(analysis, output_dir)

    service = VerifiedAggregateService(
        AggregateArtifactVerifier.from_file(output_dir, output_dir / "aggregate_manifest.json")
    )
    names = {finding.source_dataset.name for finding in service.evidence.findings}
    urls = {finding.source_dataset.source_url for finding in service.evidence.findings}
    if names != {SYNTHETIC_DATASET_NAME} or urls != {SYNTHETIC_DATASET_URL}:
        raise RuntimeError("The synthetic package does not carry synthetic provenance")
    return {
        "fixture_version": FIXTURE_VERSION,
        "seed": SEED,
        "case_count": CASE_COUNT,
        "dataset_name": SYNTHETIC_DATASET_NAME,
        "analysis_run_id": service.analysis_run_id,
        "artifacts": {
            path.name: _sha256(path) for path in sorted(output_dir.iterdir()) if path.is_file()
        },
        "finding_count": len(service.evidence.findings),
        "served_finding_count": len(service.served_evidence.findings),
        "suppressed_finding_count": len(service.evidence.findings)
        - len(service.served_evidence.findings),
        "served_evidence_sha256": evidence_contract_sha256(service.served_evidence),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True, help="Must not exist yet.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", type=Path, help="Reviewed expected values to compare.")
    group.add_argument("--write-expected", type=Path, help="Record values for review.")
    args = parser.parse_args(argv)

    output_dir = args.output_dir.resolve()
    if any(output_dir == root or root in output_dir.parents for root in REFUSED):
        print("[BLOCKED] refusing to write under data/ or deploy/aggregate-package/",
              file=sys.stderr)
        return 2
    if output_dir.exists():
        print(f"[BLOCKED] output directory already exists: {output_dir}", file=sys.stderr)
        return 2
    try:
        summary = build_package(output_dir)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

    print(json.dumps({key: summary[key] for key in summary if key != "artifacts"}, indent=2))
    if args.write_expected is not None:
        args.write_expected.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"[INFO] wrote expected values to {args.write_expected}; review before committing")
    if args.check is not None:
        expected = json.loads(args.check.read_text(encoding="utf-8"))
        fixture = {key: value for key, value in expected.items() if key in summary}
        mismatches = sorted(key for key in summary if fixture.get(key) != summary[key])
        if mismatches:
            print(f"[FAIL] synthetic package differs from {args.check}: {mismatches}",
                  file=sys.stderr)
            return 1
        print(f"[PASS] synthetic package matches the reviewed values in {args.check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
