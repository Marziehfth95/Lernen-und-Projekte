"""Migration 0002: guardrail-policy provenance for each approval request.

Records the policy, the trusted evidence and the validated tool records a recommendation
was accepted under, so approval and execution can re-validate it under the current policy.
The table is append-only for the runtime role (SELECT, INSERT). Existing tables are not
altered: approval requests created before 0002 simply have no provenance row, and the
service treats that as untrusted rather than current.
"""

from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
)

VERSION = "0002"
DESCRIPTION = "Guardrail policy provenance for approval requests"

metadata = MetaData()

# Referenced only to resolve the foreign key; 0002 never creates or alters it.
_approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", String(64), primary_key=True),
)

approval_policy_provenance = Table(
    "approval_policy_provenance",
    metadata,
    Column(
        "approval_request_id",
        String(64),
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("policy_id", String(64), nullable=False),
    Column("policy_schema_version", String(16), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("evidence_contract_sha256", String(64), nullable=False),
    Column("analysis_run_id", String(64), nullable=False),
    Column("tool_call_records_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

TABLES = (approval_policy_provenance,)


def upgrade(connection: Connection) -> None:
    """Create the provenance table only where absent; never alters or drops anything."""
    approval_policy_provenance.create(connection, checkfirst=True)
