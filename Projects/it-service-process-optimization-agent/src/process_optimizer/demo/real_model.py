"""One explicitly selected, bounded real-model end-to-end run (local LangGraph, no Hosted Agent).

The default demonstrations stay on ``DeterministicMockProvider``. This module runs the same
LangGraph investigation with ``FoundryModelProvider`` against the existing model deployment,
then hands a validated recommendation to the unchanged workflow service, which re-validates it
on submission. The run stops at PENDING_APPROVAL: no ticket exists until an authorized process
owner (not this command) decides. Invalid model output is never submitted, and the run proves
that the workflow service rejects it.

Usage: ``python -m process_optimizer.demo.real_model --preflight`` (no model call), then the
approved paid run with ``--allow-paid-model-call``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from process_optimizer.agents.demo import invocation_summary
from process_optimizer.agents.provider import (
    FoundryModelProvider,
    foundry_model_deployment_name,
)
from process_optimizer.agents.schemas import InvestigationRequestV1, InvestigationResultV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.demo.launcher import (
    _SCENARIO_GOALS,
    DEMO_ANALYST,
    DemoError,
    reset_demo_database,
    verify_governed_aggregates,
)
from process_optimizer.persistence import Database
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.workflow.schemas import (
    DecisionRequestV1,
    Priority,
    RecommendationSubmissionV1,
)
from process_optimizer.workflow.service import (
    RecommendationValidationError,
    SelfApprovalError,
    WorkflowService,
)
from process_optimizer.workflow.state import ApprovalState

DEFAULT_REAL_MODEL_DATABASE = Path("data/local/real-model-demo.sqlite3")
SCENARIO = "group_returns"
ITERATION_BUDGET = 2
TOOL_CALL_BUDGET = 4
# analyst 1 + investigator <= ITERATION_BUDGET + optimizer <= 1 + TOOL_CALL_BUDGET.
MAX_MODEL_CALLS = 1 + ITERATION_BUDGET + 1 + TOOL_CALL_BUDGET
MAX_OUTPUT_TOKENS = 2048
REQUIRED_NODES = ("analyst", "investigator", "tool", "optimizer", "validator", "finalizer")


def check_limits(environ: dict[str, str]) -> list[str]:
    """Return every unmet run precondition; an empty list means the limits are enforced."""
    problems = []
    if environ.get("ALLOW_PAID_MODEL_CALLS") != "true":
        problems.append("ALLOW_PAID_MODEL_CALLS must be 'true' for this command only")
    if not environ.get("FOUNDRY_PROJECT_ENDPOINT", "").startswith("https://"):
        problems.append("FOUNDRY_PROJECT_ENDPOINT must be the https project endpoint")
    if not (environ.get("MODEL_DEPLOYMENT_NAME") or environ.get("FOUNDRY_MODEL_NAME")):
        problems.append("MODEL_DEPLOYMENT_NAME must name the existing deployment")
    if environ.get("MODEL_MAX_RETRIES") != "0":
        problems.append("MODEL_MAX_RETRIES must be exactly 0")
    calls = environ.get("MODEL_MAX_CALLS", "")
    if not calls.isdigit() or not 1 <= int(calls) <= MAX_MODEL_CALLS:
        problems.append(f"MODEL_MAX_CALLS must be set from 1 to {MAX_MODEL_CALLS}")
    tokens = environ.get("MODEL_MAX_OUTPUT_TOKENS", "")
    if not tokens.isdigit() or not 1 <= int(tokens) <= MAX_OUTPUT_TOKENS:
        problems.append(f"MODEL_MAX_OUTPUT_TOKENS must be set from 1 to {MAX_OUTPUT_TOKENS}")
    budget = environ.get("MODEL_TPM_BUDGET", "")
    if not budget.isdigit() or not 1000 <= int(budget) <= 18000:
        problems.append("MODEL_TPM_BUDGET must be set from 1000 to 18000 (deployment: 20K TPM)")
    return problems


def nodes_visited(result: InvestigationResultV1) -> tuple[str, ...]:
    return tuple(dict.fromkeys(event.stage for event in result.audit_events))


def run_real_model_e2e(
    provider: Any,
    analysis_dir: Path,
    database_path: Path,
    expected_analysis_run_id: str,
) -> dict[str, Any]:
    """Run one bounded investigation and route its output through the governed workflow."""
    evidence = verify_governed_aggregates(analysis_dir)
    if evidence.analysis_run_id != expected_analysis_run_id:
        raise DemoError("analysis_run_id does not match the expected governed evidence")
    runner = InvestigationRunner(analysis_dir, provider=provider)
    result = runner.run(
        InvestigationRequestV1(
            analysis_run_id=runner.analysis_run_id,
            investigation_goal=_SCENARIO_GOALS[SCENARIO],
            execution_mode=provider.mode,
            scenario=SCENARIO,
            iteration_budget=ITERATION_BUDGET,
            tool_call_budget=TOOL_CALL_BUDGET,
        )
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(f"sqlite:///{database_path}")
    workflow = WorkflowService.from_analysis_dir(database, analysis_dir)
    workflow_outcome = _route_to_workflow(workflow, result)
    visited = nodes_visited(result)
    tickets = workflow.list_tickets()
    invocations = invocation_summary(getattr(provider, "invocation_records", []))
    deployment = foundry_model_deployment_name() or ""
    return {
        "schema_version": "1.0.0",
        "captured_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "execution_label": result.execution_label,
        "provider": type(provider).__name__,
        "model_deployment": foundry_model_deployment_name(),
        "analysis_run_id": result.analysis_run_id,
        "scenario": SCENARIO,
        "limits": {
            "iteration_budget": ITERATION_BUDGET,
            "tool_call_budget": TOOL_CALL_BUDGET,
            "model_max_calls": os.environ.get("MODEL_MAX_CALLS"),
            "model_max_output_tokens": os.environ.get("MODEL_MAX_OUTPUT_TOKENS"),
            "model_max_retries": os.environ.get("MODEL_MAX_RETRIES"),
            "model_tpm_budget": os.environ.get("MODEL_TPM_BUDGET"),
        },
        # The request-mode label alone proves nothing; the service-reported model name does.
        "genuine_model_response": bool(deployment)
        and any(
            name.startswith(deployment) for name in invocations["service_reported_models"]
        ),
        "model_invocations": invocations,
        "graph": {
            "nodes_visited": visited,
            "all_required_nodes_visited": all(node in visited for node in REQUIRED_NODES),
            "iterations_used": result.iterations_used,
            "tool_calls": [record.tool_name for record in result.tool_call_records],
            "errors": [error.code for error in result.errors],
            "final_decision": result.final_decision,
        },
        "validation": [item.model_dump(mode="json") for item in result.validation_results],
        "recommendations": [item.model_dump(mode="json") for item in result.recommendations],
        "workflow": {**workflow_outcome, "ticket_count": len(tickets)},
    }


def _route_to_workflow(workflow: WorkflowService, result: InvestigationResultV1) -> dict[str, Any]:
    """Submit only validated output; prove rejection of anything else; never decide."""
    token = uuid.uuid4().hex
    if not result.recommendations:
        return {"submitted": False, "reason": "NO_RECOMMENDATION"}
    recommendation = result.recommendations[0]
    try:
        submission = RecommendationSubmissionV1(
            analysis_run_id=result.analysis_run_id,
            recommendation=recommendation,
            tool_call_records=result.tool_call_records,
            proposed_owner="incident-process-team",
            priority=Priority.MEDIUM,
            idempotency_key=f"real-model-submit-{token}",
            correlation_id=f"real-model-{token}",
        )
    except ValidationError:
        return {"submitted": False, "reason": "SUBMISSION_SCHEMA_REJECTED"}
    if result.final_decision != "VALIDATED_RECOMMENDATIONS":
        try:
            workflow.submit(submission, DEMO_ANALYST)
        except RecommendationValidationError:
            return {"submitted": False, "reason": "SERVER_REVALIDATION_REJECTED"}
        raise DemoError("Workflow accepted a recommendation the graph did not validate")
    pending = workflow.submit(submission, DEMO_ANALYST)
    try:
        workflow.decide(
            pending.approval_request_id,
            ApprovalState.APPROVED,
            DecisionRequestV1(
                recommendation_version_hash=pending.recommendation_version_hash,
                idempotency_key=f"real-model-self-approval-{token}",
                correlation_id=pending.correlation_id,
                reason="Self-approval attempt; must be rejected by the backend.",
            ),
            DEMO_ANALYST,
        )
    except SelfApprovalError:
        self_approval_blocked = True
    else:
        raise DemoError("Role-based authorization regression: self-approval was not blocked")
    return {
        "submitted": True,
        "approval_request_id": pending.approval_request_id,
        "state": pending.state,
        "self_approval_blocked": self_approval_blocked,
        "note": "Awaiting a human PROCESS_OWNER decision; this command never approves.",
    }


def preflight(analysis_dir: Path, expected_analysis_run_id: str) -> dict[str, Any]:
    """Check evidence, limits and Entra data-plane access without any model invocation."""
    report: dict[str, Any] = {"model_calls_made": 0}
    evidence = verify_governed_aggregates(analysis_dir)
    report["analysis_run_id_matches"] = evidence.analysis_run_id == expected_analysis_run_id
    environ = dict(os.environ, ALLOW_PAID_MODEL_CALLS="true")
    report["limit_problems"] = check_limits(environ)
    report["max_model_calls"] = MAX_MODEL_CALLS
    deployment = foundry_model_deployment_name()
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential(exclude_managed_identity_credential=True)
        credential.get_token("https://ai.azure.com/.default")  # never printed
        project = AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/"), credential=credential
        )
        names = {item.name for item in project.deployments.list()}
        report["entra_token_acquired"] = True
        report["deployment_visible"] = deployment in names
    except Exception as error:  # SDK errors vary; report the type only.
        report["entra_token_acquired"] = False
        report["deployment_visible"] = False
        report["access_error"] = type(error).__name__
    report["go"] = bool(
        report["analysis_run_id_matches"]
        and not report["limit_problems"]
        and report["deployment_visible"]
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One bounded real-model end-to-end run.")
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--database", type=Path, default=DEFAULT_REAL_MODEL_DATABASE)
    parser.add_argument("--expected-analysis-run-id", required=True)
    parser.add_argument("--output", type=Path, help="Write the evidence JSON here as well.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="No model call.")
    mode.add_argument(
        "--allow-paid-model-call",
        action="store_true",
        help="Run the bounded paid call; also requires ALLOW_PAID_MODEL_CALLS=true.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.preflight:
        report = preflight(arguments.analysis_dir, arguments.expected_analysis_run_id)
        print(json.dumps(report, indent=2))
        return 0 if report["go"] else 2
    problems = check_limits(dict(os.environ))
    if problems:
        print(json.dumps({"model_calls_made": 0, "refused": problems}, indent=2), file=sys.stderr)
        return 2
    reset_demo_database(arguments.database)
    evidence = run_real_model_e2e(
        FoundryModelProvider(),
        arguments.analysis_dir,
        arguments.database,
        arguments.expected_analysis_run_id,
    )
    text = json.dumps(evidence, indent=2)
    print(text)
    if arguments.output:
        arguments.output.write_text(text + "\n", encoding="utf-8")
    passed = (
        evidence["execution_label"] == "REAL_MODEL_OUTPUT"
        and evidence["genuine_model_response"]
        and evidence["graph"]["all_required_nodes_visited"]
        and evidence["workflow"].get("submitted") is True
        and evidence["workflow"]["ticket_count"] == 0
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
