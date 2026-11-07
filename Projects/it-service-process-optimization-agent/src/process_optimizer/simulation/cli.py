"""CLI for deterministic what-if simulation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.simulation.engine import WhatIfSimulationEngine
from process_optimizer.simulation.schemas import (
    ScenarioAssumptionV1,
    SimulationRequestV1,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a deterministic, non-causal group-transition what-if simulation."
    )
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--finding-id", action="append", default=[])
    for name, eligibility, reduction in (
        ("conservative", 0.10, 0.10),
        ("moderate", 0.25, 0.30),
        ("optimistic", 0.50, 0.50),
    ):
        parser.add_argument(f"--{name}-eligibility", type=float, default=eligibility)
        parser.add_argument(f"--{name}-reduction", type=float, default=reduction)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    engine = WhatIfSimulationEngine(arguments.analysis_dir)
    finding_ids = tuple(arguments.finding_id) or engine.eligible_handoff_finding_ids()[:1]
    assumptions = tuple(
        ScenarioAssumptionV1(
            name=name,
            eligible_event_share=getattr(arguments, f"{name}_eligibility"),
            assumed_reduction_share=getattr(arguments, f"{name}_reduction"),
        )
        for name in ("conservative", "moderate", "optimistic")
    )
    request = SimulationRequestV1(
        analysis_run_id=engine.analysis_run_id,
        selected_finding_ids=finding_ids,
        assumptions=assumptions,
    )
    result = engine.simulate(request)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "ESTIMATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
