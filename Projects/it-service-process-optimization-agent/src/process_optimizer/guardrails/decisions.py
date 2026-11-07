"""Guardrail decision contract: ALLOW < REQUIRE_APPROVAL < BLOCK, most restrictive wins."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Decision.ALLOW: 0, Decision.REQUIRE_APPROVAL: 1, Decision.BLOCK: 2}


class Checkpoint(StrEnum):
    HOST_STARTUP = "HOST_STARTUP"
    INVOCATION_INPUT = "INVOCATION_INPUT"
    PRE_MODEL_CALL = "PRE_MODEL_CALL"
    PRE_TOOL_CALL = "PRE_TOOL_CALL"
    POST_TOOL_RESULT = "POST_TOOL_RESULT"
    RECOMMENDATION_OUTPUT = "RECOMMENDATION_OUTPUT"
    WORKFLOW_SUBMIT = "WORKFLOW_SUBMIT"
    ACTION_EXECUTE = "ACTION_EXECUTE"


class GuardrailDecisionV1(BaseModel):
    """One deterministic decision; reason codes are enums, never caller or model text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint: Checkpoint
    decision: Decision
    reason_codes: tuple[str, ...] = Field(default=())
    policy_id: str
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def blocked(self) -> bool:
        return self.decision == Decision.BLOCK

    def audit_details(self) -> dict[str, str | int | bool | None]:
        """Flat, allow-listable metadata for graph audit events and workflow audit rows."""
        return {
            "guardrail_checkpoint": self.checkpoint.value,
            "guardrail_decision": self.decision.value,
            "guardrail_reason_codes": ",".join(self.reason_codes),
            "guardrail_policy_id": self.policy_id,
            "guardrail_policy_version": self.policy_version,
        }


def most_restrictive(decisions: Iterable[Decision]) -> Decision:
    """Combine decisions; an empty input fails closed."""
    ranked = list(decisions)
    if not ranked:
        return Decision.BLOCK
    return max(ranked, key=lambda item: item.rank)
