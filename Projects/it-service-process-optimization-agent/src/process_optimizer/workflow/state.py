"""Approval lifecycle state machine."""

from enum import StrEnum


class ApprovalState(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[ApprovalState, frozenset[ApprovalState]] = {
    ApprovalState.PENDING_APPROVAL: frozenset(
        {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.REVISION_REQUESTED}
    ),
    ApprovalState.APPROVED: frozenset({ApprovalState.EXECUTING}),
    ApprovalState.EXECUTING: frozenset({ApprovalState.EXECUTED, ApprovalState.FAILED}),
    ApprovalState.REJECTED: frozenset(),
    ApprovalState.REVISION_REQUESTED: frozenset(),
    ApprovalState.EXECUTED: frozenset(),
    ApprovalState.FAILED: frozenset({ApprovalState.EXECUTING}),
}


class InvalidTransitionError(RuntimeError):
    pass


def ensure_transition(current: ApprovalState, target: ApprovalState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Transition {current} -> {target} is not allowed")
