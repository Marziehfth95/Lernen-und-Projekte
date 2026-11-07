"""Local end-to-end demo: the real API and workflow over a disposable database.

LOCAL ONLY. The FastAPI app (routes, local token adapter, RBAC, workflow service,
guardrails, migrations) runs in-process over ASGI. Recommendations come from the
DeterministicMockProvider and are labelled DETERMINISTIC_MOCK_NOT_REAL_AI. Data is the
verified aggregate package (by default the synthetic public demo package); identities are
synthetic local tokens. Nothing reaches Azure, a model, a ticketing system or a notification
channel: outbound connections to anything other than loopback are refused for the whole run.

The demo also shows one KNOWN GAP of guardrail policy 2.0.0 (a plain "cause" claim reaches
human review) and the owner's rejection of it. It is reported, never counted as a pass.

Run through scripts/run_local_e2e_demo.sh, or directly from the repository root:
    .venv/bin/python -I scripts/local_e2e_demo.py --backend sqlite
    .venv/bin/python -I scripts/local_e2e_demo.py --backend postgres \
        --pg-admin-url postgresql+psycopg://postgres@127.0.0.1:55433/postgres
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ANALYSIS_DIR = REPO / "demo-data" / "public-synthetic-aggregates"
PROTECTED_DIR = (REPO / "data").resolve()
# Pin of the default package: the SYNTHETIC public demo package written by
# scripts/prepare_public_demo_data.sh (reviewed values in
# tests/data/synthetic_ci_aggregate_fixture.json). Another package must name its own
# independently recorded pin; a package is never trusted by its own hash.
DEFAULT_EVIDENCE_SHA256 = "15f30d73f4a92ecc7daaf9e3bdccbc651dcafccfb9a1a96c799aecb2c46ca408"
PINNED_POLICY_SHA256 = "5a886139a16221c9f1fc6cbcc7754e2086bbfcdfdc2ce74f2a9bdc1a15dae358"
LOCAL_PG_URL = re.compile(r"^postgresql\+psycopg://[^@/:]+@127\.0\.0\.1:(\d+)/")
SCHEMA = "process_optimizer"
MIGRATION_ROLE = "demo-migration"
RUNTIME_ROLE = "demo-runtime"
SUBMIT = "/workflow/recommendations/submit"

# Synthetic, local-only bearer tokens; meaningful only to this in-process app.
IDENTITIES = {
    "analyst-token": {
        "actor_id": "demo.analyst",
        "roles": ["ANALYST"],
        "permissions": ["Workflow.Submit", "Aggregate.Read", "Simulation.Execute"],
        "identity_type": "local",
    },
    "owner-token": {
        "actor_id": "demo.process_owner",
        "roles": ["PROCESS_OWNER"],
        "permissions": ["Workflow.Approve", "Aggregate.Read"],
        "identity_type": "local",
    },
    "admin-token": {
        "actor_id": "demo.admin",
        "roles": ["ADMIN"],
        "permissions": ["Workflow.Audit"],
        "identity_type": "local",
    },
    # Holds both roles, so only the service's separation-of-duties rule can stop it.
    "dual-token": {
        "actor_id": "demo.dual_role_user",
        "roles": ["ANALYST", "PROCESS_OWNER"],
        "permissions": ["Workflow.Submit", "Workflow.Approve", "Aggregate.Read"],
        "identity_type": "local",
    },
    # Mirrors the hosted agent identity: may read aggregates, never submit or approve.
    "agent-token": {
        "actor_id": "demo.agent-identity",
        "roles": ["ANALYST"],
        "permissions": ["Aggregate.Read", "Simulation.Execute"],
        "identity_type": "service",
    },
}


class DemoError(RuntimeError):
    """A precondition of the local demo is not met; nothing was changed."""


# ------------------------------------------------------------------- isolation


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


def configure_environment(database_url: str, analysis_dir: Path) -> None:
    """Remove cloud settings and point the app at the disposable store and local tokens."""
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
            "LOCAL_AUTH_IDENTITIES_JSON": json.dumps(IDENTITIES),
        }
    )


def require_disposable(url: str, workdir: Path) -> None:
    """Fail closed unless the store is a fresh temporary SQLite file or a local cluster."""
    if url.startswith("sqlite:///"):
        path = Path(url.removeprefix("sqlite:///")).resolve()
        if PROTECTED_DIR in path.parents or workdir.resolve() not in path.parents:
            raise DemoError(f"Refusing a non-disposable SQLite path: {path}")
        if path.exists():
            raise DemoError(f"Refusing an existing SQLite file: {path}")
        return
    if not LOCAL_PG_URL.match(url):
        raise DemoError("PostgreSQL must be a disposable cluster on 127.0.0.1")


# ---------------------------------------------------------------------- helpers


class Demo:
    def __init__(self, app: Any) -> None:
        import httpx

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local-demo"
        )
        self.steps: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.known_gaps: list[dict[str, Any]] = []

    async def call(
        self, method: str, path: str, token: str | None = None, body: Any = None
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await self.client.request(method, path, headers=headers, json=body)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    def check(self, step: str, condition: bool, **observed: Any) -> None:
        self.steps.append({"step": step, "passed": bool(condition), **observed})
        if not condition:
            self.failures.append(step)
        summary = ", ".join(
            f"{key}={value}"
            for key, value in observed.items()
            if not isinstance(value, dict | list)
        )
        print(f"[{'PASS' if condition else 'FAIL'}] {step}: {summary}")


def derived_id(original: str, suffix: str) -> str:
    """A distinct, schema-valid recommendation ID for a demo copy."""
    digest = hashlib.sha256(f"{original}:{suffix}".encode()).hexdigest()
    return f"recommendation_{digest[:24]}"


def code(body: Any) -> Any:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return detail.get("code")
    if isinstance(detail, list):
        return "SCHEMA_VALIDATION_ERROR"
    return None


def submission(result: Any, recommendation: Any, key: str) -> dict[str, Any]:
    return {
        "analysis_run_id": result.analysis_run_id,
        "recommendation": recommendation.model_dump(mode="json"),
        "tool_call_records": [r.model_dump(mode="json") for r in result.tool_call_records],
        "proposed_owner": "incident-process-team",
        "priority": "MEDIUM",
        "idempotency_key": f"submit-{key}",
        "correlation_id": f"corr-{key}",
    }


def decision(detail: dict[str, Any], key: str, reason: str) -> dict[str, Any]:
    return {
        "recommendation_version_hash": detail["recommendation_version_hash"],
        "idempotency_key": f"decide-{key}",
        "correlation_id": detail["correlation_id"],
        "reason": reason,
    }


def result_digest(report: dict[str, Any]) -> str:
    """SHA-256 over every observed value; IDs and timestamps are never part of them."""
    stable = {"steps": report["steps"], "known_gaps": report["known_gaps"]}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------- the walkthrough


async def walkthrough(
    database_url: str,
    premigrated: bool,
    workdir: Path,
    analysis_dir: Path,
    evidence_pin: str,
) -> dict[str, Any]:
    from process_optimizer.agents.provider import DeterministicMockProvider
    from process_optimizer.agents.schemas import InvestigationRequestV1
    from process_optimizer.agents.workflow import InvestigationRunner
    from process_optimizer.demo.launcher import _SCENARIO_GOALS
    from process_optimizer.guardrails import GuardrailEngine, load_policy
    from process_optimizer.guardrails.engine import check_evidence_contract_pin
    from process_optimizer.main import app
    from process_optimizer.persistence import Database
    from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
    from process_optimizer.security.auth import Principal, Role
    from process_optimizer.workflow.notifications import LocalDemoNotificationAdapter
    from process_optimizer.workflow.service import WorkflowService

    demo = Demo(app)
    evidence = EvidenceContractV1.model_validate_json(
        (analysis_dir / "evidence_contract.json").read_text(encoding="utf-8")
    )
    database = Database(database_url)
    # SQLite: the service migrates its fresh file. PostgreSQL: a separate migration role
    # already migrated, and the runtime role only verifies the schema (durable behaviour).
    service = WorkflowService(database, evidence, apply_migrations=not premigrated)
    app.state.workflow_service = service
    run_key = uuid.uuid4().hex[:8]
    try:
        # ------------------------------------------------------- 1. platform boundary
        status, _ = await demo.call("GET", "/health")
        demo.check("health", status == 200, http=status)
        status, _ = await demo.call("GET", "/aggregate/v1/process-overview")
        demo.check("aggregate_requires_authentication", status == 401, http=status)
        status, body = await demo.call("GET", "/aggregate/v1/process-overview", "admin-token")
        demo.check("admin_lacks_aggregate_read", status == 403, http=status, code=code(body))

        # ------------------------------------------ 2. evidence and aggregate retrieval
        status, body = await demo.call("GET", "/aggregate/v1/evidence-contract", "agent-token")
        contract = EvidenceContractV1.model_validate(body["evidence"]) if status == 200 else None
        pinned = contract is not None and check_evidence_contract_pin(
            contract, evidence_pin
        )
        demo.check(
            "EV-02_evidence_contract_pin",
            status == 200 and pinned,
            http=status,
            findings=len(contract.findings) if contract else 0,
            pin_matches=pinned,
        )
        status, body = await demo.call("GET", "/aggregate/v1/guardrail-policy", "agent-token")
        remote = body.get("guardrail_policy") if status == 200 else None
        compatibility = GuardrailEngine().check_policy_compatibility(remote_metadata=remote)
        remote = remote or {}
        demo.check(
            "EV-04_policy_compatibility",
            status == 200
            and not compatibility.blocked
            and remote.get("policy_version") == PINNED_POLICY_SHA256,
            http=status,
            policy_schema=remote.get("policy_schema_version"),
            policy_hash_prefix=str(remote.get("policy_version"))[:12],
        )
        status, body = await demo.call(
            "POST", "/aggregate/v1/cohort-comparison", "agent-token", {}
        )
        demo.check(
            "aggregate_tool_cohort_comparison",
            status == 200 and body.get("analysis_run_id") == evidence.analysis_run_id,
            http=status,
            analysis_run_id=body.get("analysis_run_id") if isinstance(body, dict) else None,
        )

        # --------------------- 3. recommendation (deterministic mock) and validation
        runner = InvestigationRunner(analysis_dir, provider=DeterministicMockProvider())
        results = {}
        for scenario in ("group_returns", "completed_lifecycle"):
            result = runner.run(
                InvestigationRequestV1(
                    analysis_run_id=runner.analysis_run_id,
                    investigation_goal=_SCENARIO_GOALS[scenario],
                    scenario=scenario,
                )
            )
            results[scenario] = result
            valid = sum(v.valid for v in result.validation_results)
            demo.check(
                f"investigation_{scenario}",
                result.execution_label == "DETERMINISTIC_MOCK_NOT_REAL_AI"
                and len(result.recommendations) == 1
                and valid == len(result.validation_results) > 0,
                execution_label=result.execution_label,
                final_decision=str(result.final_decision),
                validations=f"{valid}/{len(result.validation_results)}",
                tool_calls=len(result.tool_call_records),
            )
        approve_result, reject_result = results["group_returns"], results["completed_lifecycle"]
        approve_rec = approve_result.recommendations[0]
        reject_rec = reject_result.recommendations[0]

        def copy_of(base: Any, suffix: str, **update: Any) -> Any:
            # Every demo copy gets its own ID, so it never occupies the real recommendation.
            return base.model_copy(
                update={"recommendation_id": derived_id(base.recommendation_id, suffix), **update}
            )

        # ---------------------------------- 4. guardrail and identity negative paths
        causal = copy_of(
            approve_rec,
            "neg_causal",
            problem_statement=(
                "Repeated group returns lead to longer case spans for these incidents."
            ),
        )
        status, body = await demo.call(
            "POST", SUBMIT, "analyst-token", submission(approve_result, causal, f"causal-{run_key}")
        )
        demo.check("guardrail_blocks_causal_claim", status == 422, http=status, code=code(body))
        effect = copy_of(
            approve_rec,
            "neg_savings",
            expected_outcome_with_uncertainty=(
                "This change will reduce the median case span by 30 percent."
            ),
        )
        status, body = await demo.call(
            "POST", SUBMIT, "analyst-token", submission(approve_result, effect, f"effect-{run_key}")
        )
        demo.check(
            "guardrail_blocks_unsupported_effect_claim", status == 422, http=status, code=code(body)
        )

        # KNOWN GAP (policy 2.0.0): the plain verb "cause(s)" and agentive effect verbs such
        # as "delay closure" are not among the assertive causal patterns, so this claim
        # reaches human review. It is reported, never counted as a pass, and the process
        # owner rejects it, so it can never produce a ticket.
        gap = copy_of(
            approve_rec,
            "known_gap",
            problem_statement="Repeated group returns cause longer case spans and delay closure.",
        )
        status, body = await demo.call(
            "POST", SUBMIT, "analyst-token", submission(approve_result, gap, f"gap-{run_key}")
        )
        rejected_state = None
        if status == 201:
            _, rejected = await demo.call(
                "POST",
                f"/workflow/approvals/{body['approval_request_id']}/reject",
                "owner-token",
                decision(body, f"gap-reject-{run_key}", "Rejected: unsupported causal claim."),
            )
            rejected_state = rejected.get("state")
        demo.known_gaps.append(
            {
                "case": "plain causal verb 'cause' and agentive 'delay closure' in "
                "problem_statement",
                "policy_schema_version": "2.0.0",
                "submit_http": status,
                "reached_human_review": status == 201,
                "human_decision": rejected_state,
                "status": "OPEN_NOT_SAFE",
            }
        )
        print(
            f"[KNOWN GAP] unsupported 'cause' claim reached human review: http={status}; "
            f"process owner decision={rejected_state} (not counted as a pass)"
        )

        unknown = copy_of(approve_rec, "neg_evidence", evidence_ids=("finding_does_not_exist",))
        status, body = await demo.call(
            "POST", SUBMIT, "analyst-token", submission(approve_result, unknown, f"unk-{run_key}")
        )
        demo.check("validator_blocks_unknown_evidence", status == 422, http=status, code=code(body))
        status, body = await demo.call(
            "POST", SUBMIT, "agent-token", submission(approve_result, approve_rec, f"svc-{run_key}")
        )
        demo.check("service_identity_cannot_submit", status == 403, http=status, code=code(body))

        # ------------------------------------------------------ 5. analyst submission
        first = submission(approve_result, approve_rec, f"approve-{run_key}")
        status, pending = await demo.call("POST", SUBMIT, "analyst-token", first)
        demo.check(
            "analyst_submits",
            status == 201 and pending.get("state") == "PENDING_APPROVAL",
            http=status,
            state=pending.get("state"),
        )
        status, replay = await demo.call("POST", SUBMIT, "analyst-token", first)
        same = replay.get("approval_request_id") == pending.get("approval_request_id")
        demo.check("idempotent_resubmission", status in (200, 201) and same, http=status, same=same)
        approval = f"/workflow/approvals/{pending['approval_request_id']}"

        # ---------------------------------------- 6. separation of duties at approval
        status, body = await demo.call(
            "POST", f"{approval}/approve", "analyst-token", decision(pending, f"a-{run_key}", "x")
        )
        demo.check(
            "analyst_without_approve_permission_blocked",
            status == 403,
            http=status,
            code=code(body),
        )
        own_rec = copy_of(reject_rec, "dual")
        status, own = await demo.call(
            "POST", SUBMIT, "dual-token", submission(reject_result, own_rec, f"dual-{run_key}")
        )
        status_self, body = await demo.call(
            "POST",
            f"/workflow/approvals/{own.get('approval_request_id')}/approve",
            "dual-token",
            decision(own, f"dual-self-{run_key}", "Self-approval attempt."),
        )
        demo.check(
            "self_approval_blocked_for_dual_role_user",
            status == 201 and status_self == 403 and code(body) == "SELF_APPROVAL_PROHIBITED",
            submit_http=status,
            http=status_self,
            code=code(body),
        )
        if status == 201:
            await demo.call(
                "POST",
                f"/workflow/approvals/{own['approval_request_id']}/reject",
                "owner-token",
                decision(own, f"dual-reject-{run_key}", "Closed after the self-approval check."),
            )
        status, body = await demo.call(
            "POST", f"{approval}/approve", "admin-token", decision(pending, f"ad-{run_key}", "x")
        )
        demo.check("admin_cannot_approve", status == 403, http=status, code=code(body))
        status, body = await demo.call(
            "POST", f"{approval}/approve", "agent-token", decision(pending, f"ag-{run_key}", "x")
        )
        demo.check("service_identity_cannot_approve", status == 403, http=status, code=code(body))

        # ------ 7. owner approval: revalidated under the current policy, then executed
        owner = decision(pending, f"owner-{run_key}", "Approved by the accountable process owner.")
        status, approved = await demo.call("POST", f"{approval}/approve", "owner-token", owner)
        demo.check(
            "owner_approves_and_executes",
            status == 200 and approved.get("state") == "EXECUTED",
            http=status,
            state=approved.get("state"),
        )

        # ------------------------------------------------- 8. exactly-once ticket
        status, again = await demo.call("POST", f"{approval}/approve", "owner-token", owner)
        status_new, body_new = await demo.call(
            "POST",
            f"{approval}/approve",
            "owner-token",
            decision(pending, f"owner-second-{run_key}", "Second approval attempt."),
        )
        _, tickets = await demo.call("GET", "/workflow/tickets", "owner-token")
        demo.check(
            "exactly_once_ticket",
            status == 200
            and again.get("state") == "EXECUTED"
            and status_new == 409
            and len(tickets) == 1,
            replay_http=status,
            new_key_http=status_new,
            new_key_code=code(body_new),
            tickets=len(tickets),
        )

        # ------------------------------------------------------------ 9. rejection
        status, pending_r = await demo.call(
            "POST", SUBMIT, "analyst-token", submission(reject_result, reject_rec, f"rej-{run_key}")
        )
        status, rejected = await demo.call(
            "POST",
            f"/workflow/approvals/{pending_r['approval_request_id']}/reject",
            "owner-token",
            decision(pending_r, f"reject-{run_key}", "Rejected: needs domain review first."),
        )
        _, tickets = await demo.call("GET", "/workflow/tickets", "owner-token")
        demo.check(
            "rejection_creates_no_ticket",
            status == 200 and rejected.get("state") == "REJECTED" and len(tickets) == 1,
            http=status,
            state=rejected.get("state"),
            tickets=len(tickets),
        )

        # ---------- 10. REVISION_REQUIRED: a pending approval meets a stricter policy
        revision_rec = copy_of(reject_rec, "revision")
        status, pending_v = await demo.call(
            "POST",
            SUBMIT,
            "analyst-token",
            submission(reject_result, revision_rec, f"rv-{run_key}"),
        )
        demo.check(
            "revision_case_submitted_under_current_policy",
            status == 201 and pending_v.get("state") == "PENDING_APPROVAL",
            http=status,
        )
        if status == 201:
            # SIMULATED POLICY UPGRADE: the packaged policy plus one extra pattern that matches
            # this recommendation, written to the temporary run directory and never packaged.
            packaged = Path(load_policy.__code__.co_filename).with_name("policy.v1.json")
            raw = json.loads(packaged.read_text(encoding="utf-8"))
            phrase = re.escape(" ".join(reject_rec.suggested_process_improvement.split()[:3]))
            raw["output"]["assertive_causal_patterns"].append(rf"\b{phrase}\b")
            derived = workdir / "simulated_stricter_policy.json"
            derived.write_text(json.dumps(raw), encoding="utf-8")
            stricter = GuardrailEngine(load_policy(derived))
            app.state.workflow_service = WorkflowService(
                database, evidence, guardrails=stricter, apply_migrations=not premigrated
            )
            target = f"/workflow/approvals/{pending_v['approval_request_id']}"
            status, body = await demo.call(
                "POST",
                f"{target}/approve",
                "owner-token",
                decision(pending_v, f"rva-{run_key}", "Approval attempt."),
            )
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            _, tickets = await demo.call("GET", "/workflow/tickets", "owner-token")
            _, still = await demo.call("GET", target, "owner-token")
            echoed = reject_rec.suggested_process_improvement in json.dumps(body)
            demo.check(
                "REVISION_REQUIRED_on_policy_upgrade",
                status == 409
                and detail.get("code") == "REVISION_REQUIRED"
                and still.get("state") == "PENDING_APPROVAL"
                and len(tickets) == 1
                and not echoed,
                http=status,
                code=detail.get("code"),
                reason_codes=detail.get("reason_codes"),
                state_after=still.get("state"),
                tickets=len(tickets),
                text_echoed=echoed,
                simulated_policy_hash_prefix=stricter.policy.version[:12],
            )
            status, revised = await demo.call(
                "POST",
                f"{target}/request-revision",
                "owner-token",
                decision(pending_v, f"rvr-{run_key}", "Please revise the wording."),
            )
            demo.check(
                "owner_requests_revision",
                status == 200 and revised.get("state") == "REVISION_REQUESTED",
                http=status,
                state=revised.get("state"),
            )
            app.state.workflow_service = service

        # ---------------------------------------------- 11. notifications and audit
        admin = Principal(actor_id="demo.admin", roles=frozenset({Role.ADMIN}))
        delivered, failed = service.deliver_notifications(LocalDemoNotificationAdapter(), admin)
        status_admin, page = await demo.call("GET", "/workflow/audit?limit=100", "admin-token")
        status_analyst, _ = await demo.call("GET", "/workflow/audit?limit=100", "analyst-token")
        events = service.list_audit(1000)
        actions: dict[str, int] = {}
        for event in events:
            actions[event.action_type] = actions.get(event.action_type, 0) + 1
        blocked = json.dumps(
            [e.details for e in events if e.action_type.endswith("REVALIDATION_BLOCKED")]
        )
        texts = (reject_rec.suggested_process_improvement, approve_rec.problem_statement)
        text_in_blocked = any(text in blocked for text in texts)
        demo.check(
            "audit_trail",
            status_admin == 200
            and status_analyst == 403
            and actions.get("APPROVAL_REVALIDATION_BLOCKED", 0) >= 1
            and not text_in_blocked
            and failed == 0,
            http=status_admin,
            analyst_http=status_analyst,
            events_total=len(events),
            text_in_blocked_audit=text_in_blocked,
            notifications_delivered=delivered,
            notifications_failed=failed,
            actions=dict(sorted(actions.items())),
        )
    finally:
        await demo.client.aclose()
        del app.state.workflow_service
        # Release every pooled connection so the disposable database can be dropped.
        database.engine.dispose()

    return {
        "mode": "LOCAL_ONLY_DETERMINISTIC_MOCK_NO_AZURE_NO_PAID_MODEL_NO_EXTERNAL_SEND",
        "execution_label": "DETERMINISTIC_MOCK_NOT_REAL_AI",
        "analysis_run_id": evidence.analysis_run_id,
        "passed": len(demo.steps) - len(demo.failures),
        "total": len(demo.steps),
        "failures": demo.failures,
        "known_gaps": demo.known_gaps,
        "steps": demo.steps,
    }


# ------------------------------------------------------------------ PostgreSQL


def _admin_engine(admin_url: str) -> Any:
    from sqlalchemy import create_engine

    return create_engine(admin_url, isolation_level="AUTOCOMMIT")


def create_postgres_database(admin_url: str, name: str) -> str:
    """Create a disposable database; a separate migration role migrates it."""
    from sqlalchemy import create_engine, text

    from process_optimizer.persistence.bootstrap import (
        grant_runtime,
        prepare_schema,
        secure_database,
    )
    from process_optimizer.persistence.migrations import MigrationRunner

    admin = _admin_engine(admin_url)
    with admin.connect() as connection:
        for role in (MIGRATION_ROLE, RUNTIME_ROLE):
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
            connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    server, user = admin_url.rsplit("/", 1)[0], admin_url.split("://", 1)[1].split("@", 1)[0]
    owner = create_engine(f"{server}/{name}")
    with owner.begin() as connection:
        secure_database(connection, runtime_role=RUNTIME_ROLE, migration_role=MIGRATION_ROLE)
    owner.dispose()
    role_url = server.replace(f"//{user}@", "//{role}@", 1)
    role_url += f"/{name}?options=-csearch_path%3D{SCHEMA}"
    migration = create_engine(role_url.format(role=MIGRATION_ROLE))
    with migration.begin() as connection:
        prepare_schema(connection, schema=SCHEMA, runtime_role=RUNTIME_ROLE)
    applied = MigrationRunner(migration).apply()
    with migration.begin() as connection:
        grant_runtime(connection, schema=SCHEMA, runtime_role=RUNTIME_ROLE)
    migration.dispose()
    print(
        f"[INFO] disposable PostgreSQL database {name}: {MIGRATION_ROLE} applied "
        f"{', '.join(applied)}; the app runs as {RUNTIME_ROLE} (no DDL)"
    )
    return role_url.format(role=RUNTIME_ROLE)


def probe_runtime_privileges(runtime_url: str) -> dict[str, bool]:
    """True means the runtime role was refused, as required."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    probes = {
        "update_audit_events": f"UPDATE {SCHEMA}.audit_events SET result = 'x'",
        "delete_audit_events": f"DELETE FROM {SCHEMA}.audit_events",
        "update_policy_provenance": (
            f"UPDATE {SCHEMA}.approval_policy_provenance SET policy_version = 'x'"
        ),
        "delete_policy_provenance": f"DELETE FROM {SCHEMA}.approval_policy_provenance",
        "delete_improvement_actions": f"DELETE FROM {SCHEMA}.improvement_actions",
        "drop_table": f"DROP TABLE {SCHEMA}.improvement_actions",
        "create_table": f"CREATE TABLE {SCHEMA}.demo_probe (id integer)",
    }
    engine = create_engine(runtime_url)
    refused = {}
    try:
        for name, statement in probes.items():
            try:
                with engine.begin() as connection:
                    connection.execute(text(statement))
                refused[name] = False
            except DBAPIError:
                refused[name] = True
    finally:
        engine.dispose()
    return refused


def drop_postgres_database(admin_url: str, name: str) -> None:
    from sqlalchemy import text

    admin = _admin_engine(admin_url)
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        for role in (MIGRATION_ROLE, RUNTIME_ROLE):
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
    admin.dispose()


# ------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default="sqlite")
    parser.add_argument(
        "--pg-admin-url",
        help="Superuser URL of a disposable local cluster (postgres backend only).",
    )
    parser.add_argument("--report", type=Path, help="Also write the JSON report here.")
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=DEFAULT_ANALYSIS_DIR,
        help="Verified aggregate package (default: the approved local package).",
    )
    parser.add_argument(
        "--expected-evidence-sha256",
        default=DEFAULT_EVIDENCE_SHA256,
        help="Independently recorded evidence-contract pin for --analysis-dir (EV-02).",
    )
    parser.add_argument(
        "--expected-digest",
        help="Fail unless the result digest equals this reviewed value (regression gate).",
    )
    args = parser.parse_args(argv)

    analysis_dir = args.analysis_dir.resolve()
    if not (analysis_dir / "aggregate_manifest.json").is_file():
        print(f"[BLOCKED] aggregate package missing: {analysis_dir}", file=sys.stderr)
        return 2
    if args.report is not None and PROTECTED_DIR in args.report.resolve().parents:
        print("[BLOCKED] the report may not be written under data/", file=sys.stderr)
        return 2
    if args.backend == "postgres" and not (
        args.pg_admin_url and LOCAL_PG_URL.match(args.pg_admin_url)
    ):
        print("[BLOCKED] --pg-admin-url must name a cluster on 127.0.0.1", file=sys.stderr)
        return 2

    from sqlalchemy.exc import OperationalError

    block_non_loopback_connections()
    workdir = Path(tempfile.mkdtemp(prefix="po-local-e2e-"))
    pg_database = f"demo_{uuid.uuid4().hex[:8]}"
    created_pg = False
    try:
        if args.backend == "sqlite":
            url = f"sqlite:///{workdir / 'workflow-demo.sqlite3'}"
            require_disposable(url, workdir)
        else:
            created_pg = True  # also drop a partially created database after a failure
            url = create_postgres_database(args.pg_admin_url, pg_database)
            require_disposable(url, workdir)
        configure_environment(url, analysis_dir)
        report = asyncio.run(
            walkthrough(
                url,
                args.backend == "postgres",
                workdir,
                analysis_dir,
                args.expected_evidence_sha256,
            )
        )
        report["backend"] = args.backend
        if args.backend == "postgres":
            refused = probe_runtime_privileges(url)
            report["postgres_runtime_privileges_refused"] = refused
            print(f"[INFO] runtime role refused {sum(refused.values())}/{len(refused)} "
                  "write/DDL probes on audit, provenance and tickets")
            if not all(refused.values()):
                report["failures"].append("postgres_runtime_privileges")
        report["result_digest"] = result_digest(report)
        if args.expected_digest and report["result_digest"] != args.expected_digest:
            # Any change in an observed status, code, state or count fails the gate until
            # the new behaviour is reviewed and the expected digest is updated deliberately.
            report["failures"].append("result_digest_mismatch")
            print(
                f"[FAIL] result digest {report['result_digest']} != reviewed "
                f"{args.expected_digest}",
                file=sys.stderr,
            )
    except DemoError as error:
        print(f"[BLOCKED] {error}", file=sys.stderr)
        return 2
    except OperationalError as error:
        print(f"[BLOCKED] database unreachable: {type(error.orig).__name__}", file=sys.stderr)
        return 2
    finally:
        if created_pg:
            try:
                drop_postgres_database(args.pg_admin_url, pg_database)
            except OperationalError:
                print("[CLEANUP] PostgreSQL unreachable; nothing left to drop", file=sys.stderr)
        shutil.rmtree(workdir, ignore_errors=True)

    if args.report is not None:
        args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(
        f"\n{report['passed']}/{report['total']} checks passed on {args.backend}; "
        f"known gaps: {len(report['known_gaps'])} (open, not counted); "
        f"result digest {report['result_digest'][:16]}"
    )
    print(f"[CLEANUP] removed temporary directory{' and database' if created_pg else ''}")
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
