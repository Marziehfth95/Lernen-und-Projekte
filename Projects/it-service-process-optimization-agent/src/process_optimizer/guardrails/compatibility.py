"""EV-04: the Hosted Agent and the Workflow API must enforce the same guardrail policy.

The Workflow API publishes only the policy identity (ID, schema version and SHA-256
version), never its patterns or runtime configuration, on an authenticated aggregate
route. The agent compares it with its own packaged policy at startup and refuses to
serve on any mismatch, missing metadata or malformed metadata.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from process_optimizer.guardrails.policy import GuardrailPolicyV1


class GuardrailPolicyMetadataV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    policy_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    policy_schema_version: str = Field(pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def policy_metadata(policy: GuardrailPolicyV1) -> GuardrailPolicyMetadataV1:
    return GuardrailPolicyMetadataV1(
        policy_id=policy.policy_id,
        policy_schema_version=policy.policy_schema_version,
        policy_version=policy.version,
    )


def policy_compatibility_codes(local: GuardrailPolicyV1, remote: Any) -> tuple[str, ...]:
    """Reason codes for a remote policy that differs from ``local``; empty if identical."""
    if remote is None:
        return ("GUARDRAIL_POLICY_METADATA_MISSING",)
    try:
        metadata = GuardrailPolicyMetadataV1.model_validate(remote)
    except ValidationError:
        return ("GUARDRAIL_POLICY_METADATA_MALFORMED",)
    codes = []
    if metadata.policy_id != local.policy_id:
        codes.append("GUARDRAIL_POLICY_ID_MISMATCH")
    if metadata.policy_schema_version != local.policy_schema_version:
        codes.append("GUARDRAIL_POLICY_SCHEMA_VERSION_MISMATCH")
    if metadata.policy_version != local.version:
        codes.append("GUARDRAIL_POLICY_VERSION_MISMATCH")
    return tuple(codes)
