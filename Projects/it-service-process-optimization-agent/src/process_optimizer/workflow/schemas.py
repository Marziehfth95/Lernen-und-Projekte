"""Strict external contracts for approval, automation, notification and audit."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from process_optimizer.agents.schemas import RecommendationCandidate, ToolCallRecord
from process_optimizer.workflow.state import ApprovalState

WORKFLOW_SCHEMA_VERSION = "1.0.0"
SafeId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RecommendationSubmissionV1(FrozenModel):
    schema_version: Literal["1.0.0"] = WORKFLOW_SCHEMA_VERSION
    analysis_run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    recommendation: RecommendationCandidate
    tool_call_records: tuple[ToolCallRecord, ...] = Field(default_factory=tuple, max_length=20)
    proposed_owner: SafeId
    priority: Priority
    idempotency_key: SafeId
    correlation_id: SafeId

    @model_validator(mode="after")
    def prohibit_sensitive_record_fields(self) -> RecommendationSubmissionV1:
        serialized = self.model_dump_json().lower()
        forbidden = ('"case_id"', '"event_id"', "org:resource", "model_credential", "secret")
        if any(token in serialized for token in forbidden):
            raise ValueError("Submission contains prohibited record-level or secret fields")
        return self


class DecisionRequestV1(FrozenModel):
    recommendation_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: SafeId
    correlation_id: SafeId
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalDetailV1(FrozenModel):
    schema_version: Literal["1.0.0"] = WORKFLOW_SCHEMA_VERSION
    approval_request_id: str
    recommendation_id: str
    recommendation_version: int = Field(ge=1)
    recommendation_version_hash: str
    state: ApprovalState
    submitted_by: str
    proposed_owner: str
    priority: Priority
    evidence_references: tuple[str, ...]
    simulation_id: str | None
    correlation_id: str
    created_at: datetime
    updated_at: datetime


class ImprovementTicketV1(FrozenModel):
    schema_version: Literal["1.0.0"] = WORKFLOW_SCHEMA_VERSION
    ticket_id: str
    recommendation_id: str
    recommendation_version: int
    recommendation_version_hash: str
    process_problem_statement: str
    supporting_evidence_references: tuple[str, ...]
    proposed_improvement_action: str
    proposed_owner: str
    priority: Priority
    status: ApprovalState
    approval_reference: str
    created_at: datetime
    correlation_id: str
    execution_result: dict[str, str] | None = None


class AuditEventV1(FrozenModel):
    event_id: str
    actor_id: str
    timestamp: datetime
    correlation_id: str
    action_type: str
    result: str
    artifact_type: str
    artifact_id: str
    details: dict[str, str | int | bool | None]


class NotificationPayloadV1(FrozenModel):
    approval_request_id: str
    recommendation_id: str
    recommendation_version: int
    state: ApprovalState
    correlation_id: str


class WorkflowConfigurationV1(FrozenModel):
    environment: Literal["local", "production", "other"]
    authentication_mode: Literal["local_configured", "entra_validated", "fail_closed"]
    database_dialect: str
    persistence_mode: Literal["local", "durable_postgresql", "ephemeral_sqlite_demo"]
    migration_version: str
    external_ticket_delivery_enabled: Literal[False] = False
    external_notification_delivery_enabled: Literal[False] = False
