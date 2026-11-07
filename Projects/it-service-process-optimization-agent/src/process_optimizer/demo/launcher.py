"""Single-command Phase 8A local demonstration: MOCK agent, LOCAL database only.

Every step below reuses an already-implemented component (aggregate manifest
verification, the deterministic mock investigation graph, the deterministic
what-if engine, and the durable approval workflow). No Azure resource is
contacted, no paid model is invoked, and no external notification is sent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from process_optimizer.agents.provider import DeterministicMockProvider
from process_optimizer.agents.schemas import InvestigationRequestV1, InvestigationResultV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.persistence import Database
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.security.auth import Principal, Role
from process_optimizer.simulation.engine import WhatIfSimulationEngine
from process_optimizer.simulation.schemas import ScenarioAssumptionV1, SimulationRequestV1
from process_optimizer.tools.manifest import AggregateArtifactVerifier
from process_optimizer.workflow.notifications import LocalDemoNotificationAdapter
from process_optimizer.workflow.schemas import (
    DecisionRequestV1,
    Priority,
    RecommendationSubmissionV1,
)
from process_optimizer.workflow.service import SelfApprovalError, WorkflowService
from process_optimizer.workflow.state import ApprovalState

DEFAULT_DEMO_DATABASE = Path("data/local/demo.sqlite3")

_SCENARIO_GOALS = {
    "group_returns": (
        "Investigate the observed association between repeated group visits and observed "
        "case spans, then propose a safe evidence-grounded next step."
    ),
    "completed_lifecycle": (
        "Investigate the long Completed-to-Completed transition without assuming that the "
        "repeated activity name represents rework."
    ),
}

DEMO_ANALYST = Principal(actor_id="demo.analyst", roles=frozenset({Role.ANALYST}))
DEMO_PROCESS_OWNER = Principal(
    actor_id="demo.process_owner", roles=frozenset({Role.PROCESS_OWNER})
)
DEMO_ADMIN = Principal(actor_id="demo.admin", roles=frozenset({Role.ADMIN}))


class DemoError(RuntimeError):
    """Raised when a precondition for the local demonstration is not met."""


def verify_governed_aggregates(analysis_dir: Path) -> EvidenceContractV1:
    """Fail closed unless the exact approved aggregate package verifies locally."""
    manifest_path = Path(analysis_dir) / "aggregate_manifest.json"
    if not manifest_path.is_file():
        raise DemoError(
            f"No aggregate manifest at {manifest_path}. Run the governed ingestion and "
            "process-mining pipeline first (process-optimizer-ingest, "
            "process-optimizer-analyze, process-optimizer-aggregate-manifest); this "
            "launcher never performs ingestion automatically."
        )
    try:
        return AggregateArtifactVerifier.from_file(analysis_dir, manifest_path).verify()
    except (OSError, ValueError) as error:
        raise DemoError(f"Governed aggregate verification failed: {error}") from error


def run_investigation(analysis_dir: Path, scenario: str) -> InvestigationResultV1:
    """Run the deterministic mock LangGraph investigation; never calls a real model."""
    runner = InvestigationRunner(analysis_dir, provider=DeterministicMockProvider())
    request = InvestigationRequestV1(
        analysis_run_id=runner.analysis_run_id,
        investigation_goal=_SCENARIO_GOALS[scenario],
        scenario=scenario,
    )
    result = runner.run(request)
    if not result.recommendations:
        raise DemoError(f"The mock investigation produced no validated recommendation: {scenario}")
    return result


def run_conservative_simulation(analysis_dir: Path) -> dict[str, Any]:
    """Run one conservative, explicitly hypothetical what-if simulation."""
    engine = WhatIfSimulationEngine(analysis_dir)
    finding_ids = engine.eligible_handoff_finding_ids()[:1]
    if not finding_ids:
        raise DemoError("No eligible handoff finding is available for the what-if simulation")
    request = SimulationRequestV1(
        analysis_run_id=engine.analysis_run_id,
        selected_finding_ids=finding_ids,
        assumptions=tuple(
            ScenarioAssumptionV1(
                name=name,
                eligible_event_share=eligibility,
                assumed_reduction_share=reduction,
            )
            for name, eligibility, reduction in (
                ("conservative", 0.10, 0.10),
                ("moderate", 0.25, 0.30),
                ("optimistic", 0.50, 0.50),
            )
        ),
    )
    result = engine.simulate(request)
    payload = result.model_dump(mode="json")
    payload["demo_headline_scenario"] = "conservative"
    return payload


def reset_demo_database(database_path: Path) -> bool:
    """Delete only the isolated demo database file; never any other path."""
    resolved = Path(database_path).resolve()
    if resolved.suffix != ".sqlite3" or "demo" not in resolved.name:
        raise DemoError(
            "Refusing to reset a database path that is not an isolated *demo*.sqlite3 file"
        )
    if resolved.exists():
        resolved.unlink()
        return True
    return False


def run_full_demo(
    analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
    database_path: Path = DEFAULT_DEMO_DATABASE,
    *,
    reset: bool = False,
) -> dict[str, Any]:
    """Orchestrate the full Phase 8A local walkthrough; returns a JSON-safe summary."""
    steps: list[dict[str, Any]] = []
    database_path = Path(database_path)

    if reset:
        deleted = reset_demo_database(database_path)
        steps.append({"step": "reset_demo_database", "deleted_existing_file": deleted})

    database_path.parent.mkdir(parents=True, exist_ok=True)

    evidence = verify_governed_aggregates(analysis_dir)
    steps.append(
        {
            "step": "verify_governed_aggregates",
            "mode": "LOCAL_VERIFIED",
            "analysis_run_id": evidence.analysis_run_id,
            "evidence_contract_version": evidence.schema_version,
            "finding_count": len(evidence.findings),
        }
    )

    database = Database(f"sqlite:///{database_path}")
    workflow = WorkflowService.from_analysis_dir(database, analysis_dir)

    approve_investigation = run_investigation(analysis_dir, "group_returns")
    approve_recommendation = approve_investigation.recommendations[0]
    steps.append(
        {
            "step": "agent_investigation",
            "mode": "MOCK_LOCAL_DEMO_NOT_REAL_AI",
            "scenario": "group_returns",
            "recommendation": approve_recommendation.model_dump(mode="json"),
        }
    )

    simulation = run_conservative_simulation(analysis_dir)
    steps.append(
        {
            "step": "what_if_simulation",
            "mode": "LOCAL_DETERMINISTIC_HYPOTHETICAL",
            "assumption_label": "CONSERVATIVE_ASSUMPTIONS_NOT_OBSERVED_DATA",
            "result": simulation,
        }
    )

    approve_token = uuid.uuid4().hex
    pending_to_approve = workflow.submit(
        RecommendationSubmissionV1(
            analysis_run_id=approve_investigation.analysis_run_id,
            recommendation=approve_recommendation,
            tool_call_records=approve_investigation.tool_call_records,
            proposed_owner="incident-process-team",
            priority=Priority.MEDIUM,
            idempotency_key=f"demo-submit-approve-{approve_token}",
            correlation_id=f"demo-approve-{approve_token}",
        ),
        DEMO_ANALYST,
    )
    steps.append(
        {
            "step": "submit_recommendation",
            "actor": DEMO_ANALYST.actor_id,
            "approval_request_id": pending_to_approve.approval_request_id,
            "state": pending_to_approve.state,
        }
    )

    self_approval_blocked = False
    try:
        workflow.decide(
            pending_to_approve.approval_request_id,
            ApprovalState.APPROVED,
            DecisionRequestV1(
                recommendation_version_hash=pending_to_approve.recommendation_version_hash,
                idempotency_key=f"demo-self-approval-attempt-{approve_token}",
                correlation_id=pending_to_approve.correlation_id,
                reason="Self-approval attempt; must be rejected by the backend.",
            ),
            DEMO_ANALYST,
        )
    except SelfApprovalError:
        self_approval_blocked = True
    steps.append(
        {
            "step": "self_approval_attempt_by_submitter",
            "actor": DEMO_ANALYST.actor_id,
            "blocked_by_backend": self_approval_blocked,
        }
    )
    if not self_approval_blocked:
        raise DemoError("Role-based authorization regression: self-approval was not blocked")

    decision_key = f"demo-decision-approve-{approve_token}"
    approved = workflow.decide(
        pending_to_approve.approval_request_id,
        ApprovalState.APPROVED,
        DecisionRequestV1(
            recommendation_version_hash=pending_to_approve.recommendation_version_hash,
            idempotency_key=decision_key,
            correlation_id=pending_to_approve.correlation_id,
            reason="Approved by the accountable process owner (local demo).",
        ),
        DEMO_PROCESS_OWNER,
    )
    steps.append(
        {
            "step": "approve_recommendation",
            "actor": DEMO_PROCESS_OWNER.actor_id,
            "approval_request_id": approved.approval_request_id,
            "state": approved.state,
        }
    )

    repeated_decision = workflow.decide(
        pending_to_approve.approval_request_id,
        ApprovalState.APPROVED,
        DecisionRequestV1(
            recommendation_version_hash=pending_to_approve.recommendation_version_hash,
            idempotency_key=decision_key,
            correlation_id=pending_to_approve.correlation_id,
            reason="Approved by the accountable process owner (local demo).",
        ),
        DEMO_PROCESS_OWNER,
    )
    tickets_after_approval = workflow.list_tickets()
    steps.append(
        {
            "step": "idempotent_decision_replay",
            "note": "Re-sending the identical decision with the same idempotency key is a no-op.",
            "state_matches": repeated_decision.state == approved.state,
            "ticket_count_after_approval": len(tickets_after_approval),
        }
    )
    if len(tickets_after_approval) != 1:
        raise DemoError(
            "Expected exactly one improvement ticket after one approval and one idempotent "
            f"replay, found {len(tickets_after_approval)}"
        )

    reject_investigation = run_investigation(analysis_dir, "completed_lifecycle")
    reject_recommendation = reject_investigation.recommendations[0]
    reject_token = uuid.uuid4().hex
    pending_to_reject = workflow.submit(
        RecommendationSubmissionV1(
            analysis_run_id=reject_investigation.analysis_run_id,
            recommendation=reject_recommendation,
            tool_call_records=reject_investigation.tool_call_records,
            proposed_owner="incident-process-team",
            priority=Priority.LOW,
            idempotency_key=f"demo-submit-reject-{reject_token}",
            correlation_id=f"demo-reject-{reject_token}",
        ),
        DEMO_ANALYST,
    )
    rejected = workflow.decide(
        pending_to_reject.approval_request_id,
        ApprovalState.REJECTED,
        DecisionRequestV1(
            recommendation_version_hash=pending_to_reject.recommendation_version_hash,
            idempotency_key=f"demo-decision-reject-{reject_token}",
            correlation_id=pending_to_reject.correlation_id,
            reason="Rejected by the accountable process owner (local demo).",
        ),
        DEMO_PROCESS_OWNER,
    )
    tickets_after_rejection = workflow.list_tickets()
    steps.append(
        {
            "step": "reject_recommendation_no_ticket",
            "mode": "MOCK_LOCAL_DEMO_NOT_REAL_AI",
            "scenario": "completed_lifecycle",
            "approval_request_id": rejected.approval_request_id,
            "state": rejected.state,
            "ticket_count_unchanged": len(tickets_after_rejection) == 1,
        }
    )
    if len(tickets_after_rejection) != 1:
        raise DemoError(
            "A rejected recommendation must never create an improvement ticket; found "
            f"{len(tickets_after_rejection)} tickets, expected 1"
        )

    delivered, failed = workflow.deliver_notifications(
        LocalDemoNotificationAdapter(), DEMO_ADMIN
    )
    audit_events = workflow.list_audit(limit=100)
    steps.append(
        {
            "step": "notifications_and_audit",
            "notification_adapter": "LOCAL_DEMO_ONLY_NO_EXTERNAL_SEND",
            "delivered": delivered,
            "failed": failed,
            "audit_event_count": len(audit_events),
            "audit_events": [event.model_dump(mode="json") for event in audit_events],
        }
    )

    return {
        "mode": "LOCAL_DEMO_NO_AZURE_NO_PAID_MODEL_NO_EXTERNAL_ACTION",
        "database": str(database_path),
        "approved_ticket_count": len(tickets_after_approval),
        "final_ticket_count": len(tickets_after_rejection),
        "steps": steps,
    }


def _serve_dashboard(
    analysis_dir: Path, database_path: Path, *, host: str, port: int
) -> None:
    """Launch the local-only read dashboard with isolated demo identities and database."""
    import uvicorn

    os.environ["APP_ENV"] = "local"
    os.environ["PROCESS_OPTIMIZER_ANALYSIS_DIR"] = str(analysis_dir)
    os.environ["AGGREGATE_MANIFEST_PATH"] = str(Path(analysis_dir) / "aggregate_manifest.json")
    os.environ["WORKFLOW_DATABASE_URL"] = f"sqlite:///{database_path}"
    os.environ.setdefault("LOCAL_AUTH_IDENTITIES_JSON", json.dumps(_default_local_identities()))

    from process_optimizer.demo.dashboard import create_dashboard_app

    app = create_dashboard_app()
    print(
        f"Serving the LOCAL demo dashboard at http://{host}:{port}/demo/ui/ "
        "(MOCK agent, LOCAL database only)",
        file=sys.stderr,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


def _default_local_identities() -> dict[str, dict[str, Any]]:
    return {
        "demo-analyst-token": {
            "actor_id": DEMO_ANALYST.actor_id,
            "roles": ["ANALYST"],
            "permissions": ["Workflow.Submit", "Aggregate.Read", "Simulation.Execute"],
            "identity_type": "local",
        },
        "demo-owner-token": {
            "actor_id": DEMO_PROCESS_OWNER.actor_id,
            "roles": ["PROCESS_OWNER"],
            "permissions": ["Workflow.Approve", "Aggregate.Read"],
            "identity_type": "local",
        },
        "demo-admin-token": {
            "actor_id": DEMO_ADMIN.actor_id,
            "roles": ["ADMIN"],
            "permissions": ["Workflow.Audit"],
            "identity_type": "local",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--database", type=Path, default=DEFAULT_DEMO_DATABASE)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete only the isolated demo database before running (never production state).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="After the walkthrough, start the local-only dashboard with uvicorn.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.serve:
            # --serve starts an interactive dashboard; it does not pre-run the scripted
            # walkthrough, because the deterministic mock agent always produces the same
            # two recommendations and a second submission would correctly hit the same
            # immutable-version conflict the walkthrough itself demonstrates. Run
            # `process-optimizer-demo --reset` (without --serve) first if you want the
            # terminal walkthrough, then `--serve` separately to browse its result, or
            # use `--serve --reset` for an empty database to click through live.
            if args.reset:
                deleted = reset_demo_database(args.database)
                print(
                    json.dumps({"status": "OK", "reset_demo_database": deleted}, indent=2),
                )
            verify_governed_aggregates(args.analysis_dir)
            _serve_dashboard(args.analysis_dir, args.database, host=args.host, port=args.port)
            return 0
        summary = run_full_demo(args.analysis_dir, args.database, reset=args.reset)
    except DemoError as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"status": "OK", **summary}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
