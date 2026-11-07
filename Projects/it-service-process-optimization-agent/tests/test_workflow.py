"""Phase 6 durable workflow, authorization, notification and audit tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from process_optimizer.agents.schemas import (
    ObservedNumericalFact,
    RecommendationCandidate,
    RecommendationStatus,
    ToolRequest,
)
from process_optimizer.main import app
from process_optimizer.persistence import Database
from process_optimizer.persistence.models import (
    ApprovalRequestRecord,
    AuditEventRecord,
    Base,
    NotificationOutboxRecord,
    RecommendationRecord,
)
from process_optimizer.process_mining.evidence_contract import (
    AggregateEvidenceReferenceV1,
    AggregatePopulationV1,
    DatasetSourceV1,
    EvidenceContractV1,
    EvidenceCountsV1,
    EvidenceFindingV1,
    MetricDefinitionV1,
)
from process_optimizer.security.auth import (
    LocalAuthenticationAdapter,
    Principal,
    Role,
)
from process_optimizer.workflow.notifications import LocalDemoNotificationAdapter
from process_optimizer.workflow.schemas import (
    DecisionRequestV1,
    Priority,
    RecommendationSubmissionV1,
)
from process_optimizer.workflow.service import (
    LocalImprovementExecutor,
    RecommendationValidationError,
    SelfApprovalError,
    WorkflowConflictError,
    WorkflowService,
)
from process_optimizer.workflow.state import (
    ALLOWED_TRANSITIONS,
    ApprovalState,
    InvalidTransitionError,
    ensure_transition,
)

RUN_ID = "run_aaaaaaaaaaaaaaaaaaaaaaaa"
FINDING_ID = "finding_bbbbbbbbbbbbbbbbbbbbbbbb"
ANALYST = Principal(actor_id="analyst.test", roles=frozenset({Role.ANALYST}))
OWNER = Principal(actor_id="owner.test", roles=frozenset({Role.PROCESS_OWNER}))
ADMIN = Principal(actor_id="admin.test", roles=frozenset({Role.ADMIN}))


@pytest.fixture
def evidence() -> EvidenceContractV1:
    source = DatasetSourceV1(
        name="Synthetic aggregate workflow fixture",
        source_url="https://example.invalid/synthetic",
        sha256="d" * 64,
    )
    finding = EvidenceFindingV1(
        finding_id=FINDING_ID,
        source_dataset=source,
        analysis_run_id=RUN_ID,
        analysis_configuration_version="synthetic-v1",
        metric=MetricDefinitionV1(
            name="lifecycle_decomposition_of_activity_transition",
            definition="Synthetic aggregate lifecycle observation.",
            unit="event_transitions",
        ),
        aggregate_population=AggregatePopulationV1(label="synthetic aggregate"),
        counts=EvidenceCountsV1(
            count_level="event_transition", observation_count=20, denominator=100
        ),
        measured_statistics={"incident_count": 10},
        data_limitations=("Synthetic fixture only.",),
        sufficiency_status="context_only",
        evidence_references=(
            AggregateEvidenceReferenceV1(
                reference_id="evidence_ref_cccccccccccccccccccccccc",
                artifact="synthetic_aggregate.json",
                artifact_sha256="e" * 64,
                selector={"fixture": "synthetic"},
                metric="synthetic lifecycle count",
            ),
        ),
    )
    return EvidenceContractV1(
        analysis_run_id=RUN_ID,
        analysis_configuration_version="synthetic-v1",
        source_dataset=source,
        findings=(finding,),
    )


@pytest.fixture
def workflow(tmp_path: Path, evidence: EvidenceContractV1) -> WorkflowService:
    return WorkflowService(Database(f"sqlite:///{tmp_path / 'workflow.sqlite3'}"), evidence)


def recommendation(*, suffix: str = "") -> RecommendationCandidate:
    return RecommendationCandidate(
        recommendation_id="recommendation_" + "a" * 24,
        problem_statement=f"Observed aggregate lifecycle pattern{suffix}.",
        evidence_ids=(FINDING_ID,),
        observed_numerical_facts=(
            ObservedNumericalFact(
                statement="Ten synthetic incidents are represented.",
                evidence_id=FINDING_ID,
                field_path="measured_statistics.incident_count",
                value=10,
                unit="incidents",
            ),
        ),
        hypotheses=("A governed review may identify a safe process improvement.",),
        data_limitations=("This is aggregate observational evidence.",),
        suggested_process_improvement="Review the aggregate lifecycle pattern with the owner.",
        expected_outcome_with_uncertainty="Any benefit requires a controlled pilot.",
        proposed_follow_up_kpi="Re-measure the same aggregate metric after a pilot.",
        implementation_risk="A process change may have unintended routing effects.",
        status=RecommendationStatus.VALIDATED,
    )


def submission(
    *, suffix: str = "", key: str = "submit-1", correlation: str = "corr-1"
) -> RecommendationSubmissionV1:
    return RecommendationSubmissionV1(
        analysis_run_id=RUN_ID,
        recommendation=recommendation(suffix=suffix),
        proposed_owner="process-team",
        priority=Priority.MEDIUM,
        idempotency_key=key,
        correlation_id=correlation,
    )


def decision(detail: Any, *, key: str = "decision-1") -> DecisionRequestV1:
    return DecisionRequestV1(
        recommendation_version_hash=detail.recommendation_version_hash,
        idempotency_key=key,
        correlation_id="corr-decision",
        reason="Reviewed by the accountable process owner.",
    )


def test_state_machine_has_only_explicit_transitions() -> None:
    assert ALLOWED_TRANSITIONS[ApprovalState.PENDING_APPROVAL] == {
        ApprovalState.APPROVED,
        ApprovalState.REJECTED,
        ApprovalState.REVISION_REQUESTED,
    }
    with pytest.raises(InvalidTransitionError):
        ensure_transition(ApprovalState.PENDING_APPROVAL, ApprovalState.EXECUTED)


def test_full_approval_execution_and_audit(workflow: WorkflowService) -> None:
    pending = workflow.submit(submission(), ANALYST)
    assert pending.state == ApprovalState.PENDING_APPROVAL

    completed = workflow.decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )

    assert completed.state == ApprovalState.EXECUTED
    tickets = workflow.list_tickets()
    assert len(tickets) == 1
    assert tickets[0].recommendation_version_hash == pending.recommendation_version_hash
    actions = [event.action_type for event in workflow.list_audit()]
    assert {
        "RECOMMENDATION_SUBMITTED",
        "APPROVAL_REQUESTED",
        "APPROVAL_GRANTED",
        "ACTION_EXECUTION_STARTED",
        "ACTION_EXECUTION_COMPLETED",
        "NOTIFICATION_QUEUED",
    }.issubset(actions)


def test_rejection_creates_no_ticket(workflow: WorkflowService) -> None:
    pending = workflow.submit(submission(), ANALYST)
    rejected = workflow.decide(
        pending.approval_request_id, ApprovalState.REJECTED, decision(pending), OWNER
    )

    assert rejected.state == ApprovalState.REJECTED
    assert workflow.list_tickets() == ()


def test_only_deterministically_valid_recommendation_can_enter_pending(
    workflow: WorkflowService,
) -> None:
    candidate = recommendation()
    invalid_fact = candidate.observed_numerical_facts[0].model_copy(update={"value": 11})
    invalid = candidate.model_copy(update={"observed_numerical_facts": (invalid_fact,)})
    request = submission().model_copy(update={"recommendation": invalid})

    with pytest.raises(RecommendationValidationError):
        workflow.submit(request, ANALYST)
    assert workflow.list_pending() == ()


def test_immutable_version_self_approval_and_revision(workflow: WorkflowService) -> None:
    pending = workflow.submit(submission(), ANALYST)
    altered = decision(pending).model_copy(update={"recommendation_version_hash": "f" * 64})
    with pytest.raises(WorkflowConflictError, match="outdated"):
        workflow.decide(pending.approval_request_id, ApprovalState.APPROVED, altered, OWNER)
    with pytest.raises(SelfApprovalError):
        workflow.decide(
            pending.approval_request_id, ApprovalState.APPROVED, decision(pending), ANALYST
        )

    revised = workflow.decide(
        pending.approval_request_id,
        ApprovalState.REVISION_REQUESTED,
        decision(pending, key="revision-1"),
        OWNER,
    )
    assert revised.state == ApprovalState.REVISION_REQUESTED
    second = workflow.submit(
        submission(suffix=" Updated after owner feedback", key="submit-2"), ANALYST
    )
    assert second.recommendation_version == 2
    assert second.recommendation_version_hash != pending.recommendation_version_hash


def test_submission_and_decision_are_idempotent(workflow: WorkflowService) -> None:
    first = workflow.submit(submission(), ANALYST)
    assert workflow.submit(submission(), ANALYST) == first
    completed = workflow.decide(
        first.approval_request_id, ApprovalState.APPROVED, decision(first), OWNER
    )
    repeated = workflow.decide(
        first.approval_request_id, ApprovalState.APPROVED, decision(first), OWNER
    )
    assert repeated.state == completed.state == ApprovalState.EXECUTED
    assert len(workflow.list_tickets()) == 1


def test_database_restart_preserves_business_state(
    tmp_path: Path, evidence: EvidenceContractV1
) -> None:
    url = f"sqlite:///{tmp_path / 'restart.sqlite3'}"
    first_service = WorkflowService(Database(url), evidence)
    pending = first_service.submit(submission(), ANALYST)

    restarted = WorkflowService(Database(url), evidence)
    loaded = restarted.get_approval(pending.approval_request_id)
    assert loaded.recommendation_version_hash == pending.recommendation_version_hash
    assert loaded.state == ApprovalState.PENDING_APPROVAL
    assert "schema_migrations" in restarted.database.table_names()


class FailingExecutor:
    def create_ticket(self, ticket: Any, idempotency_key: str) -> dict[str, Any]:
        del ticket, idempotency_key
        raise RuntimeError("sensitive adapter detail")


def test_failed_and_recovered_execution_survives_restart(
    tmp_path: Path, evidence: EvidenceContractV1
) -> None:
    url = f"sqlite:///{tmp_path / 'recover.sqlite3'}"
    failing = WorkflowService(Database(url), evidence, executor=FailingExecutor())
    pending = failing.submit(submission(), ANALYST)
    failed = failing.decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )
    assert failed.state == ApprovalState.FAILED

    with failing.database.transaction() as session:
        row = session.get(ApprovalRequestRecord, pending.approval_request_id)
        assert row is not None
        row.state = ApprovalState.EXECUTING
    restarted = WorkflowService(Database(url), evidence, executor=LocalImprovementExecutor())
    assert restarted.recover_interrupted_executions(ADMIN) == 1
    assert restarted.get_approval(pending.approval_request_id).state == ApprovalState.EXECUTED
    assert len(restarted.list_tickets()) == 1


def test_transaction_rolls_back_submission_on_outbox_failure(
    workflow: WorkflowService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("synthetic rollback")

    monkeypatch.setattr(workflow, "_queue_notification", fail)
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        workflow.submit(submission(), ANALYST)
    with workflow.database.session() as session:
        assert session.scalar(select(func.count()).select_from(RecommendationRecord)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEventRecord)) == 0


def test_notification_outbox_success_and_failure(workflow: WorkflowService) -> None:
    workflow.submit(submission(), ANALYST)
    success = LocalDemoNotificationAdapter()
    assert workflow.deliver_notifications(success, ADMIN) == (1, 0)
    assert len(success.deliveries) == 1

    pending = workflow.submit(
        RecommendationSubmissionV1(
            **submission(suffix=" another", key="submit-other").model_dump(
                exclude={"recommendation"}
            ),
            recommendation=recommendation(suffix=" another").model_copy(
                update={"recommendation_id": "recommendation_" + "b" * 24}
            ),
        ),
        ANALYST,
    )
    assert pending.state == ApprovalState.PENDING_APPROVAL
    assert workflow.deliver_notifications(LocalDemoNotificationAdapter(fail=True), ADMIN) == (0, 1)
    with workflow.database.session() as session:
        failures = session.scalars(
            select(NotificationOutboxRecord).where(NotificationOutboxRecord.status == "FAILED")
        ).all()
        assert failures[0].last_error_code == "LOCAL_DEMO_FAILURE"


def test_concurrent_approval_attempts_create_one_ticket(workflow: WorkflowService) -> None:
    pending = workflow.submit(submission(), ANALYST)

    def approve(key: str) -> str:
        try:
            result = workflow.decide(
                pending.approval_request_id,
                ApprovalState.APPROVED,
                decision(pending, key=key),
                OWNER,
            )
            return result.state
        except (WorkflowConflictError, InvalidTransitionError):
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(approve, ("concurrent-1", "concurrent-2")))
    assert ApprovalState.EXECUTED in outcomes
    assert len(workflow.list_tickets()) == 1


def test_racing_decisions_on_one_sqlite_file_keep_one_outcome_and_audit(
    workflow: WorkflowService,
) -> None:
    pending = workflow.submit(submission(), ANALYST)
    targets = [ApprovalState.APPROVED, ApprovalState.REJECTED] * 4

    def decide(index: int) -> str:
        try:
            return workflow.decide(
                pending.approval_request_id,
                targets[index],
                decision(pending, key=f"race-{index}"),
                OWNER,
            ).state
        except (WorkflowConflictError, InvalidTransitionError):
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        outcomes = list(pool.map(decide, range(len(targets))))
    assert len([item for item in outcomes if item != "CONFLICT"]) == 1
    final = workflow.get_approval(pending.approval_request_id).state
    assert final in {ApprovalState.EXECUTED, ApprovalState.REJECTED}
    assert len(workflow.list_tickets()) == (1 if final == ApprovalState.EXECUTED else 0)
    decisions = [
        event.action_type
        for event in workflow.list_audit()
        if event.action_type in {"APPROVAL_GRANTED", "APPROVAL_REJECTED"}
    ]
    assert len(decisions) == 1


def test_no_llm_write_tool_and_no_raw_record_storage(workflow: WorkflowService) -> None:
    with pytest.raises(ValidationError):
        ToolRequest(name="approve_recommendation", arguments={})  # type: ignore[arg-type]
    columns = {
        column.name
        for table in Base.metadata.sorted_tables
        for column in table.columns
    }
    assert "case_id" not in columns
    assert "event_id" not in columns
    assert "model_credentials" not in columns


def test_api_rbac_fail_closed_and_end_to_end(workflow: WorkflowService) -> None:
    app.state.workflow_service = workflow
    app.state.authentication_adapter = LocalAuthenticationAdapter(
        {"analyst-token": ANALYST, "owner-token": OWNER, "admin-token": ADMIN}
    )

    async def run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get("/workflow/approvals/pending")
            assert missing.status_code == 401
            forbidden = await client.get(
                "/workflow/audit", headers={"Authorization": "Bearer analyst-token"}
            )
            assert forbidden.status_code == 403
            created = await client.post(
                "/workflow/recommendations/submit",
                json=submission().model_dump(mode="json"),
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert created.status_code == 201
            approval = created.json()
            approved = await client.post(
                f"/workflow/approvals/{approval['approval_request_id']}/approve",
                json={
                    "recommendation_version_hash": approval["recommendation_version_hash"],
                    "idempotency_key": "api-decision",
                    "correlation_id": "api-correlation",
                    "reason": "Authorized API approval.",
                },
                headers={"Authorization": "Bearer owner-token"},
            )
            assert approved.status_code == 200
            assert approved.json()["state"] == "EXECUTED"
            audit = await client.get(
                "/workflow/audit", headers={"Authorization": "Bearer admin-token"}
            )
            assert audit.status_code == 200
            assert audit.json()

    asyncio.run(run())


def test_production_authentication_is_fail_closed(
    workflow: WorkflowService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.workflow_service = workflow
    if hasattr(app.state, "authentication_adapter"):
        delattr(app.state, "authentication_adapter")
    monkeypatch.setenv("APP_ENV", "production")

    async def run() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/workflow/approvals/pending",
                headers={"Authorization": "Bearer arbitrary-client-value"},
            )
            assert response.status_code == 401

    asyncio.run(run())
