# Approval workflow

Phase 6 introduces a database-authoritative lifecycle:

```text
validated recommendation -> PENDING_APPROVAL
PENDING_APPROVAL -> APPROVED -> EXECUTING -> EXECUTED
PENDING_APPROVAL -> REJECTED
PENDING_APPROVAL -> REVISION_REQUESTED -> new validated version -> PENDING_APPROVAL
EXECUTING -> FAILED -> EXECUTING (explicit retry only)
```

All other transitions are rejected. Submission reruns the Phase 5 deterministic
validator against trusted Evidence Contract V1. The stored SHA-256 binds the complete
recommendation payload, evidence references, and simulation citation. A decision must
repeat that exact hash. Altered and stale versions fail with a conflict.

Only `ANALYST` identities submit. Only `PROCESS_OWNER` identities decide, and the
submitter cannot decide their own request. Revision closes the old request; a changed,
revalidated recommendation becomes the next immutable version. Approval immediately
invokes the deterministic application service, which creates at most one local ticket
using unique approval and idempotency constraints.

SQLite is authoritative locally. If execution is interrupted after entering
`EXECUTING`, `recover_interrupted_executions` resumes through the same idempotent
executor boundary. LangGraph memory is never the source of truth for approval or action
state.
