"""Command-line entry points for profiling, ingestion, and validation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from process_optimizer.data.constants import DEFAULT_INPUT_PATH, DEFAULT_OUTPUT_DIR
from process_optimizer.data.errors import DataPipelineError
from process_optimizer.data.ingestion import (
    ingest_xes,
    validate_processed,
    write_schema_profile,
)


def _parser(operation: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{operation.capitalize()} the BPI 2013 Incidents XES.GZ event log."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def _run(operation: str, argv: Sequence[str] | None = None) -> int:
    arguments = _parser(operation).parse_args(argv)
    try:
        if operation == "profile":
            path = write_schema_profile(arguments.input, arguments.output_dir)
            print(f"schema_profile={path}")
        elif operation == "ingest":
            result = ingest_xes(arguments.input, arguments.output_dir)
            print(
                f"cases={result.case_count} events={result.event_count} "
                f"quality_status={result.quality_status} output_dir={arguments.output_dir}"
            )
        else:
            report = validate_processed(arguments.input, arguments.output_dir)
            print(f"quality_status={report['overall_status']} output_dir={arguments.output_dir}")
    except (DataPipelineError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


def profile_main(argv: Sequence[str] | None = None) -> int:
    """Profile CLI."""
    return _run("profile", argv)


def ingest_main(argv: Sequence[str] | None = None) -> int:
    """Ingestion CLI."""
    return _run("ingest", argv)


def validate_main(argv: Sequence[str] | None = None) -> int:
    """Validation CLI."""
    return _run("validate", argv)


def _entrypoint(main: Callable[[], int]) -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entrypoint(ingest_main)

