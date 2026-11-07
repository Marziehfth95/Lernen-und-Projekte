"""CLI for reproducible deterministic analysis through Phase 3.5."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from process_optimizer.data.errors import DataPipelineError
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR, DEFAULT_PHASE2_DIR
from process_optimizer.process_mining.models import BottleneckConfig
from process_optimizer.process_mining.pipeline import run_process_mining


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic process-mining analysis.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_PHASE2_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--minimum-observations", type=int, default=30)
    parser.add_argument("--duration-median-ratio", type=float, default=2.0)
    parser.add_argument("--duration-p90-ratio", type=float, default=1.5)
    parser.add_argument("--handoff-source-share", type=float, default=0.20)
    parser.add_argument("--repeated-visit-rate", type=float, default=0.20)
    parser.add_argument("--maximum-findings", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        config = BottleneckConfig(
            minimum_observations=arguments.minimum_observations,
            duration_median_ratio_threshold=arguments.duration_median_ratio,
            duration_p90_ratio_threshold=arguments.duration_p90_ratio,
            handoff_source_share_threshold=arguments.handoff_source_share,
            repeated_visit_rate_threshold=arguments.repeated_visit_rate,
            maximum_findings=arguments.maximum_findings,
        )
        result = run_process_mining(arguments.input_dir, arguments.output_dir, config)
    except (DataPipelineError, OSError, ValueError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(
        f"cases={result.case_count} events={result.event_count} "
        f"variants={result.variant_count} findings={result.finding_count} "
        f"semantic_patterns={result.semantic_pattern_count} "
        f"contract_findings={result.evidence_contract_finding_count} "
        f"output_dir={result.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
