"""Executable Phase 4 demonstrations over the real aggregate Evidence Contract V1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from process_optimizer.agents.provider import DeterministicMockProvider, FoundryModelProvider
from process_optimizer.agents.schemas import InvestigationRequestV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR

_GOALS = {
    "group_returns": (
        "Investigate the observed association between repeated group visits and observed "
        "case spans, then propose a safe evidence-grounded next step."
    ),
    "completed_lifecycle": (
        "Investigate the long Completed-to-Completed transition without assuming that the "
        "repeated activity name represents rework."
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded Phase 4 investigation demo.")
    parser.add_argument("--scenario", choices=tuple(_GOALS), default="group_returns")
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument(
        "--mode", choices=("mocked_llm", "real_model"), default="mocked_llm"
    )
    parser.add_argument(
        "--allow-paid-model-call",
        action="store_true",
        help="Required with --mode real_model; may incur Azure model charges.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.mode == "real_model" and not (
        arguments.allow_paid_model_call and os.environ.get("ALLOW_PAID_MODEL_CALLS") == "true"
    ):
        print(
            "Real model execution requires --allow-paid-model-call and "
            "ALLOW_PAID_MODEL_CALLS=true; no call was made.",
            file=sys.stderr,
        )
        return 2
    provider = (
        FoundryModelProvider()
        if arguments.mode == "real_model"
        else DeterministicMockProvider()
    )
    runner = InvestigationRunner(arguments.analysis_dir, provider=provider)
    request = InvestigationRequestV1(
        analysis_run_id=runner.analysis_run_id,
        investigation_goal=_GOALS[arguments.scenario],
        execution_mode=arguments.mode,
        scenario=arguments.scenario,
    )
    result = runner.run(request)
    print(result.model_dump_json(indent=2))
    if isinstance(provider, FoundryModelProvider):
        summary = invocation_summary(provider.invocation_records)
        print(json.dumps(summary, indent=2), file=sys.stderr)
    return 0 if result.final_decision == "VALIDATED_RECOMMENDATIONS" else 1


def invocation_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-call metadata only; no prompt or completion content."""

    def total(key: str) -> int:
        return sum(
            (record.get("token_usage") or {}).get(key, 0)
            for record in records
            if isinstance((record.get("token_usage") or {}).get(key, 0), int)
        )

    return {
        "model_invocation_attempts": len(records),
        "failed_attempts": sum(1 for record in records if record.get("error_category")),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "service_reported_models": sorted(
            {record["response_model"] for record in records if record.get("response_model")}
        ),
        "calls": [
            {
                "role": record.get("role"),
                "model_deployment": record.get("model_deployment"),
                "response_model": record.get("response_model"),
                "system_fingerprint": record.get("system_fingerprint"),
                "latency_ms": record.get("latency_ms"),
                "error_category": record.get("error_category"),
                "token_usage": {
                    key: value
                    for key, value in (record.get("token_usage") or {}).items()
                    if key in {"input_tokens", "output_tokens", "total_tokens"}
                },
            }
            for record in records
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
