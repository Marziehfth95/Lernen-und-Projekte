# Phase 6 evaluation

The quality gate covers lifecycle transitions, deterministic revalidation, immutable
hash binding, versioned revision, self-approval prevention, role checks, fail-closed
production authentication, idempotent duplicates, concurrent approvals, rollback,
restart persistence, interrupted execution recovery, outbox success/failure, audit
completeness, absence of LLM write tools, and approval/rejection end-to-end paths.

The approval demonstration uses real aggregate Evidence Contract V1 and the deterministic
mock agent. It produces one local improvement ticket and local-only notification delivery.
The rejection demonstration produces no ticket. Neither changes BPI13, calls a paid
model, sends an external message, or creates an Azure resource.

Phase 6 is ready for local human review and controlled demonstrations. It is not ready
for Azure deployment until Entra ID, a selected managed database, migrations in the
deployment pipeline, concurrency/load validation, secretless identity, network controls,
operational alerting and an approved external ITSM/Teams integration are implemented.

## Executed quality gate

- Full pytest regression: 63 passed, 1 opt-in paid-model test skipped.
- Ruff: all checks passed.
- Dependency validation: no broken requirements.
- Existing deterministic real-aggregate evaluation: 2 of 2 cases passed; no model call,
  token usage or estimated cost.
- Approval demo: `EXECUTED`, one local ticket, four local notification deliveries,
  thirteen audit events.
- Rejection demo: `REJECTED`, zero tickets, two local notification deliveries, seven
  audit events.
- The SHA-256 of the source event log was unchanged afterwards.

The two existing warnings are unchanged: Foundry hosting uses a preview SDK and PM4Py
reports the optional `r4pm` acceleration package. The test process also attempted to
flush host-configured OpenTelemetry metrics after shutdown; this did not affect test
results and no project code initiated a paid model or Azure resource operation.
