"""Start the interactive LOCAL demo: fresh disposable database, loopback only, no egress.

    .venv/bin/python -I -m process_optimizer.demo.interactive_launcher [--port 8765]

Every launch:

* removes Foundry, Azure, Application Insights and OTLP-exporter variables, sets
  APP_ENV=local and ALLOW_PAID_MODEL_CALLS=false;
* refuses every Python-level outbound connection except loopback;
* creates a new temporary directory with a new SQLite file (never under ``data/``),
  and deletes both on exit;
* verifies the aggregate package, the EV-02 evidence pin and the EV-04 policy pin before
  serving anything (fail closed). The default package is the reproducible SYNTHETIC public
  demo package written by ``scripts/prepare_public_demo_data.sh``; a package built from a
  licensed copy of the original event log needs ``--analysis-dir`` and its own independently
  recorded ``--expected-evidence-sha256``;
* binds to 127.0.0.1 only.

The deterministic mock provider is the default. "Real Local AI (Ollama)" is selectable in
the UI; it talks only to the loopback Ollama endpoint (``OLLAMA_BASE_URL``, default
http://127.0.0.1:11434) with ``--ollama-model`` / ``OLLAMA_MODEL`` (default
``qwen3.5:9b``), never pulls a model and never falls back to the mock.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
DEFAULT_ANALYSIS_DIR = REPO / "demo-data" / "public-synthetic-aggregates"
PROTECTED_DIR = (REPO / "data").resolve()


def block_non_loopback_connections() -> None:
    """Refuse every Python-level outbound connection except loopback and Unix sockets."""
    original = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0]
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            if not loopback:
                raise PermissionError(f"Local demo refused an outbound connection to {host}")
        return original(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign]


def configure_environment(analysis_dir: Path, database_url: str) -> None:
    for name in list(os.environ):
        if name.startswith(("FOUNDRY_", "AZURE_", "APPLICATIONINSIGHTS_", "OTEL_EXPORTER_")):
            del os.environ[name]
    os.environ.update(
        {
            "APP_ENV": "local",
            "ALLOW_PAID_MODEL_CALLS": "false",
            "ENABLE_AZURE_MONITOR": "false",
            "PROCESS_OPTIMIZER_ANALYSIS_DIR": str(analysis_dir),
            "AGGREGATE_MANIFEST_PATH": str(analysis_dir / "aggregate_manifest.json"),
            "WORKFLOW_DATABASE_URL": database_url,
        }
    )
    # No LOCAL_AUTH_IDENTITIES_JSON: the app installs its own per-launch synthetic tokens.
    os.environ.pop("LOCAL_AUTH_IDENTITIES_JSON", None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive LOCAL demo (deterministic mock by default; optional local Ollama)."
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--ollama-model",
        help="Locally installed Ollama model for 'Real Local AI' (default: qwen3.5:9b).",
    )
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument(
        "--expected-evidence-sha256",
        help="Independently recorded served-evidence pin for --analysis-dir (EV-02). "
        "Default: the reviewed pin of the synthetic public demo package.",
    )
    args = parser.parse_args(argv)

    analysis_dir = args.analysis_dir.resolve()
    block_non_loopback_connections()
    workdir = Path(tempfile.mkdtemp(prefix="po-interactive-demo-")).resolve()
    database_path = workdir / "interactive-demo.sqlite3"
    if PROTECTED_DIR in database_path.parents or database_path.exists():
        print("[BLOCKED] refusing a non-disposable database path", file=sys.stderr)
        return 2
    database_url = f"sqlite:///{database_path}"
    configure_environment(analysis_dir, database_url)
    if args.ollama_model:
        os.environ["OLLAMA_MODEL"] = args.ollama_model

    from process_optimizer.demo.interactive import (
        PINNED_EVIDENCE_SHA256,
        DemoStartupError,
        build_runtime,
        create_interactive_app,
    )

    if not (analysis_dir / "aggregate_manifest.json").is_file():
        shutil.rmtree(workdir, ignore_errors=True)
        print(
            f"[BLOCKED] aggregate package missing: {analysis_dir}\n"
            "  Generate the synthetic public demo package first:\n"
            "    scripts/prepare_public_demo_data.sh",
            file=sys.stderr,
        )
        return 2
    try:
        runtime = build_runtime(
            analysis_dir,
            database_url,
            workdir,
            expected_evidence_sha256=args.expected_evidence_sha256 or PINNED_EVIDENCE_SHA256,
        )
        app = create_interactive_app(runtime)
    except DemoStartupError as error:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[BLOCKED] {error}", file=sys.stderr)
        return 2

    import uvicorn

    print(
        "\n  Interactive LOCAL demo (no Azure, no paid model, no external send)\n"
        "  Default model mode: Deterministic Demo (DETERMINISTIC_MOCK_NOT_REAL_AI)\n"
        f"  Optional: Real Local AI (REAL_MODEL_OUTPUT) via Ollama {runtime.ollama_model} "
        f"at {runtime.ollama_base_url}\n"
        f"  Open http://127.0.0.1:{args.port}/app/\n"
        f"  Disposable database: {database_path}\n"
        "  Press Ctrl+C to stop; the database is deleted on exit.\n",
        file=sys.stderr,
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        runtime.database.engine.dispose()
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[CLEANUP] removed {workdir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
