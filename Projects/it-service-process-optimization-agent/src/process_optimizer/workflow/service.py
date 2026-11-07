"""Transactional human approval, deterministic execution, notifications and audit."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from process_optimizer.agents.schemas import (
    RecommendationCandidate,
    RecommendationStatus,
    ToolCallRecord,
)
from process_optimizer.agents.validation import validate_recommendations
from process_optimizer.guardrails import (
    Decision,
    GuardrailDecisionV1,
    GuardrailEngine,
    evidence_contract_sha256,
)
from process_optimizer.guardrails.compatibility import GuardrailPolicyMetadataV1, policy_metadata
from process_optimizer.observability import record_operational_event
from process_optimizer.persistence.database import Database
from process_optimizer.persistence.models import (
    ApprovalDecisionRecord,
    ApprovalPolicyProvenanceRecord,
    ApprovalRequestRecord,
    AuditEventRecord,
    ImprovementActionRecord,
    NotificationOutboxRecord,
    RecommendationRecord,
    utc_now,
)
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.security.auth import Principal
from process_optimizer.workflow.notifications import (
    NotificationAdapter,
    NotificationDeliveryError,
)
from process_optimizer.workflow.schemas import (
    ApprovalDetailV1,
    AuditEventV1,
    DecisionRequestV1,
    ImprovementTicketV1,
    NotificationPayloadV1,
    RecommendationSubmissionV1,
)
from process_optimizer.workflow.state import ApprovalState, ensure_transition


class WorkflowError(RuntimeError):
    code = "WORKFLOW_ERROR"


class WorkflowConflictError(WorkflowError):
    code = "WORKFLOW_CONFLICT"


class WorkflowNotFoundError(WorkflowError):
    code = "NOT_FOUND"


class RecommendationValidationError(WorkflowError):
    code = "RECOMMENDATION_NOT_VALIDATED"


class SelfApprovalError(WorkflowError):
    code = "SELF_APPROVAL_PROHIBITED"


class ResourceAuthorizationError(WorkflowError):
    code = "RESOURCE_ACCESS_DENIED"


class GuardrailBlockedError(WorkflowError):
    code = "GUARDRAIL_BLOCKED"


class RevisionRequiredError(WorkflowError):
    """The current policy does not accept the stored recommendation; nothing was changed."""

    code = "REVISION_REQUIRED"

    def __init__(self, reason_codes: tuple[str, ...], provenance: str) -> None:
        super().__init__("The recommendation must be revised under the current policy")
        self.reason_codes = reason_codes
        self.provenance = provenance


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ImprovementExecutor(Protocol):
    def create_ticket(
        self, ticket: ImprovementTicketV1, idempotency_key: str
    ) -> dict[str, Any]: ...


class LocalImprovementExecutor:
    """Deterministic local adapter; it persists a ticket but changes no ITSM system."""

    def create_ticket(self, ticket: ImprovementTicketV1, idempotency_key: str) -> dict[str, Any]:
        return {
            "adapter": "LOCAL_DEMO_ONLY",
            "outcome": "PERSISTED_LOCAL_IMPROVEMENT_TICKET",
            "ticket_id": ticket.ticket_id,
            "idempotency_key": idempotency_key,
        }


class WorkflowService:
    """Database-authoritative application service; no model or graph can write directly."""

    def __init__(
        self,
        database: Database,
        evidence: EvidenceContractV1,
        *,
        executor: ImprovementExecutor | None = None,
        guardrails: GuardrailEngine | None = None,
        apply_migrations: bool = True,
    ) -> None:
        self.database = database
        self.evidence = evidence
        self.executor = executor or LocalImprovementExecutor()
        self.guardrails = guardrails or GuardrailEngine()
        self._policy_identity = policy_metadata(self.guardrails.policy)
        self._evidence_sha256 = evidence_contract_sha256(evidence)
        if apply_migrations:
            self.database.migrate()
        else:
            # Durable databases are migrated by the separate migration identity; the
            # runtime identity has no DDL rights and must not attempt any.
            self.database.require_migrated()

    @classmethod
    def from_analysis_dir(
        cls,
        database: Database,
        analysis_dir: Path,
        *,
        executor: ImprovementExecutor | None = None,
        apply_migrations: bool = True,
    ) -> WorkflowService:
        evidence = EvidenceContractV1.model_validate_json(
            (Path(analysis_dir) / "evidence_contract.json").read_text(encoding="utf-8")
        )
        return cls(database, evidence, executor=executor, apply_migrations=apply_migrations)

    def submit(
        self, request: RecommendationSubmissionV1, principal: Principal
    ) -> ApprovalDetailV1:
        if request.analysis_run_id != self.evidence.analysis_run_id:
            raise RecommendationValidationError("Analysis run does not match trusted evidence")
        validated, results = validate_recommendations(
            (request.recommendation,),
            self.evidence,
            request.tool_call_records,
            policy=self.guardrails.policy,
        )
        guardrail = self.guardrails.check_submission(
            recommendation=validated[0],
            validation_result=results[0],
            identity_type=principal.identity_type,
        )
        if not results[0].valid or validated[0].status != RecommendationStatus.VALIDATED:
            self._record_guardrail_block(
                principal, request.correlation_id, validated[0].recommendation_id, guardrail
            )
            raise RecommendationValidationError("Recommendation failed deterministic validation")
        if guardrail.decision != Decision.REQUIRE_APPROVAL:
            self._record_guardrail_block(
                principal, request.correlation_id, validated[0].recommendation_id, guardrail
            )
            raise GuardrailBlockedError("Recommendation was blocked by deterministic guardrails")
        recommendation = validated[0]
        payload = recommendation.model_dump(mode="json")
        version_hash = self._version_hash(payload)
        try:
            with self.database.transaction() as session:
                existing_request = session.scalar(
                    select(ApprovalRequestRecord).where(
                        ApprovalRequestRecord.submission_idempotency_key == request.idempotency_key
                    )
                )
                if existing_request is not None:
                    if existing_request.recommendation.version_hash != version_hash:
                        raise WorkflowConflictError(
                            "Idempotency key was used for different content"
                        )
                    return self._approval_detail(existing_request)

                previous = session.scalars(
                    select(RecommendationRecord)
                    .where(
                        RecommendationRecord.recommendation_id
                        == recommendation.recommendation_id
                    )
                    .order_by(RecommendationRecord.version.desc())
                ).first()
                version = 1
                if previous is not None:
                    previous_approval = previous.approvals[0]
                    if previous.version_hash == version_hash:
                        raise WorkflowConflictError("This immutable recommendation version exists")
                    if previous_approval.state != ApprovalState.REVISION_REQUESTED:
                        raise WorkflowConflictError(
                            "A new version requires a prior REVISION_REQUESTED decision"
                        )
                    version = previous.version + 1

                recommendation_row = RecommendationRecord(
                    id=self._id("recver"),
                    recommendation_id=recommendation.recommendation_id,
                    version=version,
                    version_hash=version_hash,
                    analysis_run_id=request.analysis_run_id,
                    payload_json=self._json(payload),
                    evidence_references_json=self._json(list(recommendation.evidence_ids)),
                    simulation_assumptions_json=self._json(
                        recommendation.simulation.model_dump(mode="json")
                        if recommendation.simulation is not None
                        else None
                    ),
                    submitted_by=principal.actor_id,
                )
                approval = ApprovalRequestRecord(
                    id=self._id("approval"),
                    recommendation=recommendation_row,
                    state=ApprovalState.PENDING_APPROVAL,
                    proposed_owner=request.proposed_owner,
                    priority=request.priority,
                    submission_idempotency_key=request.idempotency_key,
                    correlation_id=request.correlation_id,
                )
                # Provenance: what this submission was accepted under (append-only).
                provenance = ApprovalPolicyProvenanceRecord(
                    approval=approval,
                    policy_id=self._policy_identity.policy_id,
                    policy_schema_version=self._policy_identity.policy_schema_version,
                    policy_version=self._policy_identity.policy_version,
                    evidence_contract_sha256=self._evidence_sha256,
                    analysis_run_id=self.evidence.analysis_run_id,
                    tool_call_records_json=self._json(
                        [record.model_dump(mode="json") for record in request.tool_call_records]
                    ),
                )
                session.add_all([recommendation_row, approval, provenance])
                session.flush()
                self._audit(
                    session,
                    principal.actor_id,
                    "RECOMMENDATION_SUBMITTED",
                    "success",
                    "recommendation",
                    recommendation.recommendation_id,
                    request.correlation_id,
                    {"version": version, "version_hash": version_hash, **guardrail.audit_details()},
                )
                self._audit(
                    session,
                    principal.actor_id,
                    "APPROVAL_REQUESTED",
                    "success",
                    "approval_request",
                    approval.id,
                    request.correlation_id,
                    {"state": ApprovalState.PENDING_APPROVAL},
                )
                self._queue_notification(session, approval, "APPROVAL_REQUIRED", principal.actor_id)
                return self._approval_detail(approval)
        except IntegrityError as error:
            raise WorkflowConflictError(
                "A duplicate immutable workflow record was rejected"
            ) from error

    def decide(
        self,
        approval_id: str,
        target: ApprovalState,
        request: DecisionRequestV1,
        principal: Principal,
    ) -> ApprovalDetailV1:
        if target not in {
            ApprovalState.APPROVED,
            ApprovalState.REJECTED,
            ApprovalState.REVISION_REQUESTED,
        }:
            raise WorkflowConflictError("Unsupported human decision")
        try:
            with self.database.transaction() as session:
                # Lock the request first so the idempotency check, the policy check and
                # the state change below cannot interleave with a concurrent decision.
                approval = self._get_approval(session, approval_id, lock=True)
                existing = session.scalar(
                    select(ApprovalDecisionRecord).where(
                        ApprovalDecisionRecord.idempotency_key == request.idempotency_key
                    )
                )
                if existing is not None:
                    if existing.approval_request_id != approval_id or existing.decision != target:
                        raise WorkflowConflictError("Decision idempotency key was reused")
                    return self._approval_detail(existing.approval)
                recommendation = approval.recommendation
                if recommendation.version_hash != request.recommendation_version_hash:
                    raise WorkflowConflictError("Recommendation version is altered or outdated")
                if principal.actor_id == recommendation.submitted_by:
                    raise SelfApprovalError("A submitter cannot approve their own recommendation")
                if target == ApprovalState.APPROVED:
                    # An impossible transition is refused as such, before any policy check.
                    ensure_transition(ApprovalState(approval.state), target)
                    self._require_current_policy(approval)
                self._transition(session, approval, target)
                decision = ApprovalDecisionRecord(
                    id=self._id("decision"),
                    approval=approval,
                    decision=target,
                    actor_id=principal.actor_id,
                    reason=request.reason,
                    idempotency_key=request.idempotency_key,
                    recommendation_version_hash=recommendation.version_hash,
                )
                session.add(decision)
                action_type = {
                    ApprovalState.APPROVED: "APPROVAL_GRANTED",
                    ApprovalState.REJECTED: "APPROVAL_REJECTED",
                    ApprovalState.REVISION_REQUESTED: "REVISION_REQUESTED",
                }[target]
                self._audit(
                    session,
                    principal.actor_id,
                    action_type,
                    "success",
                    "approval_request",
                    approval.id,
                    request.correlation_id,
                    {"state": target, "version_hash": recommendation.version_hash},
                )
                self._queue_notification(
                    session, approval, f"APPROVAL_{target}", principal.actor_id
                )
                if target == ApprovalState.APPROVED:
                    self._execute(session, approval, decision, principal.actor_id)
                return self._approval_detail(approval)
        except RevisionRequiredError as error:
            self._record_revalidation_block(
                principal.actor_id, approval_id, request.correlation_id, "APPROVAL", error
            )
            raise
        except IntegrityError as error:
            raise WorkflowConflictError(
                "A concurrent or duplicate decision was rejected"
            ) from error

    def retry_failed_execution(
        self, approval_id: str, correlation_id: str, principal: Principal
    ) -> ApprovalDetailV1:
        try:
            with self.database.transaction() as session:
                approval = self._get_approval(session, approval_id, lock=True)
                ensure_transition(ApprovalState(approval.state), ApprovalState.EXECUTING)
                decision = approval.decisions[-1]
                self._execute(session, approval, decision, principal.actor_id, correlation_id)
                return self._approval_detail(approval)
        except RevisionRequiredError as error:
            self._record_revalidation_block(
                principal.actor_id, approval_id, correlation_id, "EXECUTION", error
            )
            raise

    def recover_interrupted_executions(self, principal: Principal) -> int:
        """Idempotently complete persisted EXECUTING records after process restart."""
        recovered = 0
        with self.database.transaction() as session:
            approvals = session.scalars(
                select(ApprovalRequestRecord)
                .where(ApprovalRequestRecord.state == ApprovalState.EXECUTING)
                .with_for_update()
            ).all()
            for approval in approvals:
                try:
                    self._finish_execution(session, approval, principal.actor_id)
                except RevisionRequiredError as error:
                    # Checked before any write for this request: it stays EXECUTING and
                    # no ticket is created; the others are still recovered.
                    self._audit_revalidation_block(
                        session, principal.actor_id, approval.id, approval.correlation_id,
                        "EXECUTION", error,
                    )
                    continue
                recovered += 1
        return recovered

    def list_pending(self, *, submitted_by: str | None = None) -> tuple[ApprovalDetailV1, ...]:
        with self.database.session() as session:
            statement = select(ApprovalRequestRecord).where(
                ApprovalRequestRecord.state == ApprovalState.PENDING_APPROVAL
            )
            if submitted_by is not None:
                statement = statement.join(RecommendationRecord).where(
                    RecommendationRecord.submitted_by == submitted_by
                )
            records = session.scalars(statement.order_by(ApprovalRequestRecord.created_at)).all()
            return tuple(self._approval_detail(item) for item in records)

    def get_approval(
        self, approval_id: str, *, submitted_by: str | None = None
    ) -> ApprovalDetailV1:
        with self.database.session() as session:
            approval = self._get_approval(session, approval_id)
            if (
                submitted_by is not None
                and approval.recommendation.submitted_by != submitted_by
            ):
                raise ResourceAuthorizationError("Approval request is outside actor scope")
            return self._approval_detail(approval)

    def list_tickets(self, *, submitted_by: str | None = None) -> tuple[ImprovementTicketV1, ...]:
        with self.database.session() as session:
            statement = select(ImprovementActionRecord)
            if submitted_by is not None:
                statement = (
                    statement.join(ApprovalRequestRecord)
                    .join(RecommendationRecord)
                    .where(RecommendationRecord.submitted_by == submitted_by)
                )
            records = session.scalars(
                statement.order_by(ImprovementActionRecord.created_at)
            ).all()
            return tuple(self._ticket(item) for item in records)

    def get_ticket(
        self, ticket_id: str, *, submitted_by: str | None = None
    ) -> ImprovementTicketV1:
        with self.database.session() as session:
            row = session.scalar(
                select(ImprovementActionRecord).where(
                    ImprovementActionRecord.ticket_id == ticket_id
                )
            )
            if row is None:
                raise WorkflowNotFoundError("Improvement ticket was not found")
            if (
                submitted_by is not None
                and row.approval.recommendation.submitted_by != submitted_by
            ):
                raise ResourceAuthorizationError("Improvement ticket is outside actor scope")
            return self._ticket(row)

    def list_audit(self, limit: int = 100) -> tuple[AuditEventV1, ...]:
        with self.database.session() as session:
            rows = session.scalars(
                select(AuditEventRecord).order_by(AuditEventRecord.created_at).limit(limit)
            ).all()
            return tuple(
                AuditEventV1(
                    event_id=row.id,
                    actor_id=row.actor_id,
                    timestamp=self._utc(row.created_at),
                    correlation_id=row.correlation_id,
                    action_type=row.action_type,
                    result=row.result,
                    artifact_type=row.artifact_type,
                    artifact_id=row.artifact_id,
                    details=json.loads(row.details_json),
                )
                for row in rows
            )

    def deliver_notifications(
        self, adapter: NotificationAdapter, principal: Principal, limit: int = 100
    ) -> tuple[int, int]:
        delivered = failed = 0
        with self.database.transaction() as session:
            rows = session.scalars(
                select(NotificationOutboxRecord)
                .where(NotificationOutboxRecord.status.in_(("PENDING", "FAILED")))
                .order_by(NotificationOutboxRecord.created_at)
                .limit(limit)
            ).all()
            for row in rows:
                row.attempt_count += 1
                row.last_attempt_at = utc_now()
                try:
                    adapter.deliver(
                        row.template, NotificationPayloadV1.model_validate_json(row.payload_json)
                    )
                except NotificationDeliveryError as error:
                    row.status = "FAILED"
                    row.last_error_code = error.error_code
                    failed += 1
                    result = "failed"
                else:
                    row.status = "DELIVERED"
                    row.last_error_code = None
                    row.delivered_at = utc_now()
                    delivered += 1
                    result = "success"
                self._audit(
                    session,
                    principal.actor_id,
                    "NOTIFICATION_DELIVERY_ATTEMPTED",
                    result,
                    "notification",
                    row.id,
                    row.correlation_id,
                    {"template": row.template, "attempt_count": row.attempt_count},
                )
        return delivered, failed

    def _execute(
        self,
        session: Session,
        approval: ApprovalRequestRecord,
        decision: ApprovalDecisionRecord,
        actor_id: str,
        correlation_id: str | None = None,
    ) -> None:
        self._transition(session, approval, ApprovalState.EXECUTING)
        correlation = correlation_id or approval.correlation_id
        self._audit(
            session,
            actor_id,
            "ACTION_EXECUTION_STARTED",
            "success",
            "approval_request",
            approval.id,
            correlation,
            {"state": ApprovalState.EXECUTING},
        )
        self._queue_notification(session, approval, "EXECUTION_STATUS_CHANGED", actor_id)
        session.flush()
        self._finish_execution(session, approval, actor_id, decision=decision)

    def _finish_execution(
        self,
        session: Session,
        approval: ApprovalRequestRecord,
        actor_id: str,
        *,
        decision: ApprovalDecisionRecord | None = None,
    ) -> None:
        recommendation = approval.recommendation
        decision = decision or approval.decisions[-1]
        allowed = self.guardrails.check_execution(
            approval_state=str(approval.state), decision=str(decision.decision)
        )
        if allowed.blocked:
            raise GuardrailBlockedError("Execution requires an APPROVED human decision")
        # Even an approved recommendation is executed only if the current policy still
        # accepts it; checked before any ticket or external side effect.
        self._require_current_policy(approval)
        payload = json.loads(recommendation.payload_json)
        action = approval.action
        if action is None:
            ticket_id = self._id("improvement")
            action = ImprovementActionRecord(
                id=self._id("action"),
                ticket_id=ticket_id,
                approval=approval,
                recommendation_id=recommendation.recommendation_id,
                recommendation_version=recommendation.version,
                recommendation_version_hash=recommendation.version_hash,
                problem_statement=payload["problem_statement"],
                evidence_references_json=recommendation.evidence_references_json,
                proposed_action=payload["suggested_process_improvement"],
                proposed_owner=approval.proposed_owner,
                priority=approval.priority,
                status=ApprovalState.EXECUTING,
                approval_reference=decision.id,
                execution_idempotency_key=f"execute:{approval.id}:{recommendation.version_hash}",
                correlation_id=approval.correlation_id,
            )
            session.add(action)
            session.flush()
        ticket = self._ticket(action)
        try:
            result = self.executor.create_ticket(ticket, action.execution_idempotency_key)
        except Exception:
            self._transition(session, approval, ApprovalState.FAILED)
            action.status = ApprovalState.FAILED
            action.result_json = self._json({"error_code": "LOCAL_EXECUTION_FAILED"})
            self._audit(
                session,
                actor_id,
                "ACTION_EXECUTION_FAILED",
                "failed",
                "improvement_action",
                action.ticket_id,
                approval.correlation_id,
                {"error_code": "LOCAL_EXECUTION_FAILED"},
            )
        else:
            self._transition(session, approval, ApprovalState.EXECUTED)
            action.status = ApprovalState.EXECUTED
            action.result_json = self._json(result)
            self._audit(
                session,
                actor_id,
                "ACTION_EXECUTION_COMPLETED",
                "success",
                "improvement_action",
                action.ticket_id,
                approval.correlation_id,
                {"state": ApprovalState.EXECUTED},
            )
        self._queue_notification(session, approval, "EXECUTION_STATUS_CHANGED", actor_id)

    def _queue_notification(
        self,
        session: Session,
        approval: ApprovalRequestRecord,
        template: str,
        actor_id: str,
    ) -> None:
        recommendation = approval.recommendation
        deduplication_key = f"{approval.id}:{template}:{approval.state}"
        if session.scalar(
            select(NotificationOutboxRecord.id).where(
                NotificationOutboxRecord.deduplication_key == deduplication_key
            )
        ):
            return
        payload = NotificationPayloadV1(
            approval_request_id=approval.id,
            recommendation_id=recommendation.recommendation_id,
            recommendation_version=recommendation.version,
            state=ApprovalState(approval.state),
            correlation_id=approval.correlation_id,
        )
        row = NotificationOutboxRecord(
            id=self._id("notification"),
            template=template,
            payload_json=payload.model_dump_json(),
            status="PENDING",
            deduplication_key=deduplication_key,
            correlation_id=approval.correlation_id,
        )
        session.add(row)
        self._audit(
            session,
            actor_id,
            "NOTIFICATION_QUEUED",
            "success",
            "notification",
            row.id,
            approval.correlation_id,
            {"template": template},
        )

    def _require_current_policy(self, approval: ApprovalRequestRecord) -> None:
        codes, provenance = self._revalidate(approval)
        if codes:
            raise RevisionRequiredError(codes, provenance)

    def _revalidate(self, approval: ApprovalRequestRecord) -> tuple[tuple[str, ...], str]:
        """Re-run the validator and guardrail on the stored, immutable recommendation.

        Always runs under the current server policy: provenance never grants trust (the
        validator code is not covered by the policy hash). It only selects the original
        evidence and tool records, and a missing or malformed row is untrusted.
        """
        recommendation = approval.recommendation
        provenance, tool_records, evidence_matches = self._provenance(approval)
        if not evidence_matches or recommendation.analysis_run_id != self.evidence.analysis_run_id:
            return ("ORIGINAL_EVIDENCE_UNAVAILABLE",), provenance
        try:
            payload = json.loads(recommendation.payload_json)
            if self._version_hash(payload) != recommendation.version_hash:
                raise ValueError("Recommendation content does not match its version hash")
            candidate = RecommendationCandidate.model_validate(payload)
        except (ValueError, TypeError):
            return ("RECOMMENDATION_INTEGRITY_FAILED",), provenance
        validated, results = validate_recommendations(
            (candidate,), self.evidence, tool_records, policy=self.guardrails.policy
        )
        decision = self.guardrails.check_recommendations(
            recommendations=validated, validation_results=results
        )
        if (
            decision.decision != Decision.REQUIRE_APPROVAL
            or not results[0].valid
            or validated[0].status != RecommendationStatus.VALIDATED
        ):
            return decision.reason_codes or ("VALIDATION_FAILED",), provenance
        return (), provenance

    def _provenance(
        self, approval: ApprovalRequestRecord
    ) -> tuple[str, tuple[ToolCallRecord, ...], bool]:
        """(status, original tool records, original evidence still served)."""
        row = approval.provenance
        if row is None:
            # Created before migration 0002: only the analysis run can be compared, and
            # no tool records were stored, so a simulation citation cannot re-validate.
            return "MISSING", (), True
        try:
            recorded = GuardrailPolicyMetadataV1(
                policy_id=row.policy_id,
                policy_schema_version=row.policy_schema_version,
                policy_version=row.policy_version,
            )
            if not _SHA256.fullmatch(row.evidence_contract_sha256 or ""):
                raise ValueError("Malformed evidence hash")
            tool_records = tuple(
                ToolCallRecord.model_validate(item)
                for item in json.loads(row.tool_call_records_json)
            )
        except (ValueError, TypeError):
            return "MALFORMED", (), False
        evidence_matches = (
            row.evidence_contract_sha256 == self._evidence_sha256
            and row.analysis_run_id == self.evidence.analysis_run_id
        )
        status = "CURRENT" if recorded == self._policy_identity else "DIFFERENT_POLICY"
        return status, tool_records, evidence_matches

    def _record_revalidation_block(
        self,
        actor_id: str,
        approval_id: str,
        correlation_id: str,
        checkpoint: str,
        error: RevisionRequiredError,
    ) -> None:
        """Audit a refused approval or execution in its own transaction; nothing else."""
        with self.database.transaction() as session:
            self._audit_revalidation_block(
                session, actor_id, approval_id, correlation_id, checkpoint, error
            )

    def _audit_revalidation_block(
        self,
        session: Session,
        actor_id: str,
        approval_id: str,
        correlation_id: str,
        checkpoint: str,
        error: RevisionRequiredError,
    ) -> None:
        self._audit(
            session,
            actor_id,
            f"{checkpoint}_REVALIDATION_BLOCKED",
            "blocked",
            "approval_request",
            approval_id,
            correlation_id,
            {
                "decision": "REVISION_REQUIRED",
                "reason_codes": ",".join(error.reason_codes),
                "policy_provenance": error.provenance,
                "guardrail_policy_version": self._policy_identity.policy_version,
            },
        )

    def _record_guardrail_block(
        self,
        principal: Principal,
        correlation_id: str,
        recommendation_id: str,
        decision: GuardrailDecisionV1,
    ) -> None:
        """Persist a rejected submission's guardrail decision; nothing else is written."""
        with self.database.transaction() as session:
            self._audit(
                session,
                principal.actor_id,
                "GUARDRAIL_DECISION",
                "blocked",
                "recommendation",
                recommendation_id,
                correlation_id,
                decision.audit_details(),
            )

    @classmethod
    def _audit(
        cls,
        session: Session,
        actor_id: str,
        action_type: str,
        result: str,
        artifact_type: str,
        artifact_id: str,
        correlation_id: str,
        details: dict[str, str | int | bool | None],
    ) -> None:
        session.add(
            AuditEventRecord(
                id=cls._id("audit"),
                actor_id=actor_id,
                action_type=action_type,
                result=result,
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                details_json=cls._json(details),
                correlation_id=correlation_id,
            )
        )
        record_operational_event(
            "business_audit_recorded",
            result=result,
            correlation_id=correlation_id,
            attributes={"action_type": action_type, "artifact_type": artifact_type},
        )

    @staticmethod
    def _get_approval(
        session: Session, approval_id: str, *, lock: bool = False
    ) -> ApprovalRequestRecord:
        # FOR UPDATE on PostgreSQL; SQLite serialises writers itself and ignores it.
        approval = session.get(ApprovalRequestRecord, approval_id, with_for_update=lock)
        if approval is None:
            raise WorkflowNotFoundError("Approval request was not found")
        return approval

    @staticmethod
    def _transition(
        session: Session, approval: ApprovalRequestRecord, target: ApprovalState
    ) -> None:
        current = ApprovalState(approval.state)
        ensure_transition(current, target)
        result = session.execute(
            update(ApprovalRequestRecord)
            .where(
                ApprovalRequestRecord.id == approval.id,
                ApprovalRequestRecord.state == current,
            )
            .values(state=target, updated_at=utc_now())
        )
        if result.rowcount != 1:
            raise WorkflowConflictError("The workflow state changed concurrently")
        approval.state = target

    @staticmethod
    def _version_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(WorkflowService._json(payload).encode()).hexdigest()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @classmethod
    def _approval_detail(cls, row: ApprovalRequestRecord) -> ApprovalDetailV1:
        recommendation = row.recommendation
        simulation = json.loads(recommendation.simulation_assumptions_json)
        return ApprovalDetailV1(
            approval_request_id=row.id,
            recommendation_id=recommendation.recommendation_id,
            recommendation_version=recommendation.version,
            recommendation_version_hash=recommendation.version_hash,
            state=ApprovalState(row.state),
            submitted_by=recommendation.submitted_by,
            proposed_owner=row.proposed_owner,
            priority=row.priority,
            evidence_references=tuple(json.loads(recommendation.evidence_references_json)),
            simulation_id=simulation.get("simulation_id") if simulation else None,
            correlation_id=row.correlation_id,
            created_at=cls._utc(row.created_at),
            updated_at=cls._utc(row.updated_at),
        )

    @classmethod
    def _ticket(cls, row: ImprovementActionRecord) -> ImprovementTicketV1:
        return ImprovementTicketV1(
            ticket_id=row.ticket_id,
            recommendation_id=row.recommendation_id,
            recommendation_version=row.recommendation_version,
            recommendation_version_hash=row.recommendation_version_hash,
            process_problem_statement=row.problem_statement,
            supporting_evidence_references=tuple(json.loads(row.evidence_references_json)),
            proposed_improvement_action=row.proposed_action,
            proposed_owner=row.proposed_owner,
            priority=row.priority,
            status=ApprovalState(row.status),
            approval_reference=row.approval_reference,
            created_at=cls._utc(row.created_at),
            correlation_id=row.correlation_id,
            execution_result=json.loads(row.result_json) if row.result_json else None,
        )
