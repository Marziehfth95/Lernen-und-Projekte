# Policy-version-aware approval and execution

## Problem

A submission was validated once, but the approval request did not record what it was
validated under. `decide()` checked only the recommendation version hash, and execution
checked only the approval state. An approval left pending under an older guardrail policy
could therefore be approved and executed after an upgrade without meeting the new rules.
The submission's tool records were not stored either, so a faithful re-check was impossible.

## Behaviour

**Provenance (migration 0002).** Every new submission writes one row to
`approval_policy_provenance` in the same transaction as the recommendation and the approval
request:

- the policy ID, schema version and SHA-256 policy hash;
- the evidence-contract hash and analysis run the service trusted;
- the validated tool-call records.

**Revalidation.** Approval, failed-execution retry and crash recovery all run the same step
before any decision, state change, ticket or external call:

1. The stored payload must still match its version hash (`RECOMMENDATION_INTEGRITY_FAILED`).
2. The original evidence must still be the evidence the server trusts: the same analysis run
   and, when provenance exists, the same contract hash (`ORIGINAL_EVIDENCE_UNAVAILABLE`).
3. The full validator and guardrail run again under the **current** server policy, with the
   original tool records.

It runs even when the recorded policy equals the current one, because validator code is
not covered by the policy hash. Provenance never grants trust:

| Provenance | Handling |
|---|---|
| `CURRENT` / `DIFFERENT_POLICY` | Revalidated with the stored tool records |
| `MISSING` (created before 0002) | Revalidated; analysis run must match; no tool records, so a simulation citation cannot pass |
| `MALFORMED` | Refused (`ORIGINAL_EVIDENCE_UNAVAILABLE`) |

**Refusal.** A failed revalidation raises `REVISION_REQUIRED` (HTTP 409, reason codes only).
The transaction rolls back: no decision, no state change, no ticket, no notification. A
separate audit row (`APPROVAL_REVALIDATION_BLOCKED` or `EXECUTION_REVALIDATION_BLOCKED`)
records only the reason codes, the provenance status and the current policy hash. A human
can still reject the request or ask for a revision. An impossible transition (for example
approving an executed request) is refused as such, before any policy check.

**Concurrency.** Decision and retry lock the approval row (`SELECT … FOR UPDATE` on
PostgreSQL), then re-check idempotency, revalidate and change state in one transaction.
Conditional state updates, unique idempotency keys, one action per approval and the
execution idempotency key are unchanged. SQLite ignores `FOR UPDATE` and relies on its
single-writer lock and the conditional updates.

Separation of duties, RBAC, submission/decision idempotency and the recommendation version
hash are unchanged.

## Database and release notes

**Migration 0002** creates only `approval_policy_provenance`; it alters, rewrites or deletes
nothing. The runtime role gets SELECT and INSERT on it (append-only), never UPDATE, DELETE or
DDL. Migration 0001 is unchanged.

**Local SQLite migrates automatically.** In `local` and `ephemeral_sqlite_demo` modes the
Workflow API applies pending migrations at startup. The first start of this code against an
existing local file adds the empty provenance table to that file. Existing rows are kept, and
their approvals are then treated as `MISSING` and revalidated, so an approval that the
current policy would block can no longer execute. Start new code against a copy of an existing
local database, never the original. Tests use temporary databases only.

**Durable PostgreSQL prerequisite.** The durable runtime never migrates (`require_migrated()`
refuses an unmigrated schema). Before the new Workflow API revision starts, the migration job
must apply 0001 and 0002 and re-run `grant_runtime`, so the new table gets append-only grants.

**Rollback incompatibility.** Code that predates 0002 refuses a database that already
records 0002 ("database is newer than this code"). Rolling the Workflow API back after the
migration therefore fails closed instead of silently ignoring provenance.

**Non-destructive recovery (roll forward).** Do not drop the table or delete its ledger row
in a durable database: provenance is evidence of what was accepted.

1. Fix forward. Build both images again from a corrected commit; 0002 stays applied.
2. If the previous API must run temporarily, run it against a separate, fresh ephemeral
   database or a restored point-in-time copy, never the migrated database. The migrated
   database stays untouched for the roll-forward.
3. Route the Hosted Agent back first and roll the API back second (see
   `docs/guardrail_policy_coverage.md`, "Deployment dependency (EV-04)").

Today the cloud Workflow API uses ephemeral SQLite with no stored records, and PostgreSQL is
not provisioned.
