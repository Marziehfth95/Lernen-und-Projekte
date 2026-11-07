"""SQLAlchemy records for the durable Phase 6 business workflow."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RecommendationRecord(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("recommendation_id", "version", name="uq_recommendation_version"),
        UniqueConstraint("version_hash", name="uq_recommendation_version_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    version_hash: Mapped[str] = mapped_column(String(64))
    analysis_run_id: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    evidence_references_json: Mapped[str] = mapped_column(Text)
    simulation_assumptions_json: Mapped[str] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    approvals: Mapped[list[ApprovalRequestRecord]] = relationship(back_populates="recommendation")


class ApprovalRequestRecord(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("recommendation_record_id", name="uq_approval_recommendation"),
        UniqueConstraint("submission_idempotency_key", name="uq_submission_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recommendation_record_id: Mapped[str] = mapped_column(
        ForeignKey("recommendations.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), index=True)
    proposed_owner: Mapped[str] = mapped_column(String(128))
    priority: Mapped[str] = mapped_column(String(16))
    submission_idempotency_key: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    recommendation: Mapped[RecommendationRecord] = relationship(back_populates="approvals")
    decisions: Mapped[list[ApprovalDecisionRecord]] = relationship(back_populates="approval")
    action: Mapped[ImprovementActionRecord | None] = relationship(back_populates="approval")
    provenance: Mapped[ApprovalPolicyProvenanceRecord | None] = relationship(
        back_populates="approval"
    )


class ApprovalPolicyProvenanceRecord(Base):
    """Append-only: the policy, evidence and tool records a submission was accepted under."""

    __tablename__ = "approval_policy_provenance"

    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), primary_key=True
    )
    policy_id: Mapped[str] = mapped_column(String(64))
    policy_schema_version: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(64))
    evidence_contract_sha256: Mapped[str] = mapped_column(String(64))
    analysis_run_id: Mapped[str] = mapped_column(String(64))
    tool_call_records_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    approval: Mapped[ApprovalRequestRecord] = relationship(back_populates="provenance")


class ApprovalDecisionRecord(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_decision_idempotency"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    recommendation_version_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    approval: Mapped[ApprovalRequestRecord] = relationship(back_populates="decisions")


class ImprovementActionRecord(Base):
    __tablename__ = "improvement_actions"
    __table_args__ = (
        UniqueConstraint("approval_request_id", name="uq_action_approval"),
        UniqueConstraint("execution_idempotency_key", name="uq_execution_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), unique=True)
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False
    )
    recommendation_id: Mapped[str] = mapped_column(String(64))
    recommendation_version: Mapped[int] = mapped_column(Integer)
    recommendation_version_hash: Mapped[str] = mapped_column(String(64))
    problem_statement: Mapped[str] = mapped_column(Text)
    evidence_references_json: Mapped[str] = mapped_column(Text)
    proposed_action: Mapped[str] = mapped_column(Text)
    proposed_owner: Mapped[str] = mapped_column(String(128))
    priority: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    approval_reference: Mapped[str] = mapped_column(String(64))
    execution_idempotency_key: Mapped[str] = mapped_column(String(128))
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    approval: Mapped[ApprovalRequestRecord] = relationship(back_populates="action")


class NotificationOutboxRecord(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (UniqueConstraint("deduplication_key", name="uq_notification_dedup"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32))
    artifact_type: Mapped[str] = mapped_column(String(64))
    artifact_id: Mapped[str] = mapped_column(String(64), index=True)
    details_json: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SchemaMigrationRecord(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
