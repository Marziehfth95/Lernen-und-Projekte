"""Migration 0001: durable recommendation approval and automation records.

The tables are frozen here as SQLAlchemy Core definitions instead of being derived from
the ORM models, so a later model change cannot silently alter what 0001 creates. A test
asserts that the models still match the cumulative migrations; any model change therefore
needs a new migration.
"""

from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

VERSION = "0001"
DESCRIPTION = "Initial workflow schema: recommendations, approvals, actions, outbox, audit"

metadata = MetaData()

recommendations = Table(
    "recommendations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("recommendation_id", String(64), index=True, nullable=False),
    Column("version", Integer, nullable=False),
    Column("version_hash", String(64), nullable=False),
    Column("analysis_run_id", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("evidence_references_json", Text, nullable=False),
    Column("simulation_assumptions_json", Text, nullable=False),
    Column("submitted_by", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("recommendation_id", "version", name="uq_recommendation_version"),
    UniqueConstraint("version_hash", name="uq_recommendation_version_hash"),
)

approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "recommendation_record_id",
        String(64),
        ForeignKey("recommendations.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("state", String(32), index=True, nullable=False),
    Column("proposed_owner", String(128), nullable=False),
    Column("priority", String(16), nullable=False),
    Column("submission_idempotency_key", String(128), nullable=False),
    Column("correlation_id", String(64), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("recommendation_record_id", name="uq_approval_recommendation"),
    UniqueConstraint("submission_idempotency_key", name="uq_submission_idempotency"),
)

approval_decisions = Table(
    "approval_decisions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "approval_request_id",
        String(64),
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column("decision", String(32), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("reason", Text, nullable=True),
    Column("idempotency_key", String(128), nullable=False),
    Column("recommendation_version_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("idempotency_key", name="uq_decision_idempotency"),
)

improvement_actions = Table(
    "improvement_actions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("ticket_id", String(64), unique=True, nullable=False),
    Column(
        "approval_request_id",
        String(64),
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("recommendation_id", String(64), nullable=False),
    Column("recommendation_version", Integer, nullable=False),
    Column("recommendation_version_hash", String(64), nullable=False),
    Column("problem_statement", Text, nullable=False),
    Column("evidence_references_json", Text, nullable=False),
    Column("proposed_action", Text, nullable=False),
    Column("proposed_owner", String(128), nullable=False),
    Column("priority", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    Column("approval_reference", String(64), nullable=False),
    Column("execution_idempotency_key", String(128), nullable=False),
    Column("result_json", Text, nullable=True),
    Column("correlation_id", String(64), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("approval_request_id", name="uq_action_approval"),
    UniqueConstraint("execution_idempotency_key", name="uq_execution_idempotency"),
)

notification_outbox = Table(
    "notification_outbox",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("template", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("status", String(24), index=True, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("last_error_code", String(64), nullable=True),
    Column("deduplication_key", String(128), nullable=False),
    Column("correlation_id", String(64), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_attempt_at", DateTime(timezone=True), nullable=True),
    Column("delivered_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("deduplication_key", name="uq_notification_dedup"),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("actor_id", String(128), nullable=False),
    Column("action_type", String(64), index=True, nullable=False),
    Column("result", String(32), nullable=False),
    Column("artifact_type", String(64), nullable=False),
    Column("artifact_id", String(64), index=True, nullable=False),
    Column("details_json", Text, nullable=False),
    Column("correlation_id", String(64), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

TABLES = tuple(metadata.sorted_tables)


def upgrade(connection: Connection) -> None:
    """Create the 0001 tables only where absent; never alters or drops anything."""
    metadata.create_all(connection, checkfirst=True)
