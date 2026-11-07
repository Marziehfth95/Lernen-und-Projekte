"""Local-only approval and rejection demonstrations over verified aggregate evidence."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from process_optimizer.agents.provider import DeterministicMockProvider
from process_optimizer.agents.schemas import InvestigationRequestV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.persistence import Database
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.security.auth import Principal, Role
from process_optimizer.workflow.notifications import LocalDemoNotificationAdapter
from process_optimizer.workflow.schemas import (
    DecisionRequestV1,
    Priority,
    RecommendationSubmissionV1,
)
from process_optimizer.workflow.service import WorkflowService
from process_optimizer.workflow.state import ApprovalState


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument(
        "--database", type=Path, default=Path("data/local/workflow-demo.sqlite3")
    )
    parser.add_argument("--decision", choices=("approve", "reject"), default="approve")
    args = parser.parse_args()

    args.database.parent.mkdir(parents=True, exist_ok=True)
    runner = InvestigationRunner(args.analysis_dir, DeterministicMockProvider())
    investigation = runner.run(
        InvestigationRequestV1(
            analysis_run_id=runner.analysis_run_id,
            investigation_goal=(
                "Prepare an aggregate, non-causal group-return improvement hypothesis."
            ),
            scenario="group_returns",
        )
    )
    if not investigation.recommendations:
        raise SystemExit("No validated recommendation was produced")
    recommendation = investigation.recommendations[0]
    run_token = uuid.uuid4().hex
    analyst = Principal(actor_id="local.analyst", roles=frozenset({Role.ANALYST}))
    owner = Principal(actor_id="local.process_owner", roles=frozenset({Role.PROCESS_OWNER}))
    administrator = Principal(actor_id="local.admin", roles=frozenset({Role.ADMIN}))
    workflow = WorkflowService.from_analysis_dir(
        Database(f"sqlite:///{args.database}"), args.analysis_dir
    )
    pending = workflow.submit(
        RecommendationSubmissionV1(
            analysis_run_id=runner.analysis_run_id,
            recommendation=recommendation,
            tool_call_records=investigation.tool_call_records,
            proposed_owner="incident-process-team",
            priority=Priority.MEDIUM,
            idempotency_key=f"demo-submit-{run_token}",
            correlation_id=f"demo-{run_token}",
        ),
        analyst,
    )
    target = ApprovalState.APPROVED if args.decision == "approve" else ApprovalState.REJECTED
    final = workflow.decide(
        pending.approval_request_id,
        target,
        DecisionRequestV1(
            recommendation_version_hash=pending.recommendation_version_hash,
            idempotency_key=f"demo-decision-{run_token}",
            correlation_id=pending.correlation_id,
            reason="Local deterministic Phase 6 demonstration.",
        ),
        owner,
    )
    adapter = LocalDemoNotificationAdapter()
    delivered, failed = workflow.deliver_notifications(adapter, administrator)
    print(
        json.dumps(
            {
                "mode": "LOCAL_DEMO_NO_EXTERNAL_ACTION",
                "decision": args.decision,
                "approval_state": final.state,
                "ticket_count": len(workflow.list_tickets()),
                "notification_delivery": {
                    "adapter": "LOCAL_DEMO_ONLY",
                    "delivered": delivered,
                    "failed": failed,
                },
                "audit_event_count": len(workflow.list_audit()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
