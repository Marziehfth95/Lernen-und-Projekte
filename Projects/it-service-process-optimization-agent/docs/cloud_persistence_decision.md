# Cloud persistence implementation guide

The binding decision is [ADR 0003](adr/0003-postgresql-cloud-persistence.md): Azure Database for
PostgreSQL Flexible Server 16.

Production configuration uses a URL with no password:

```text
APP_ENV=production
WORKFLOW_DATABASE_AUTH_MODE=entra
WORKFLOW_DATABASE_URL=postgresql+psycopg://<entra-principal>@<server>.postgres.database.azure.com/process_optimizer?sslmode=require
AZURE_CLIENT_ID=<workflow-user-assigned-managed-identity-client-id>
```

`database_from_environment` rejects SQLite, other drivers, embedded passwords, and non-Entra auth
in production. SQLAlchemy's `do_connect` event obtains a fresh token for the PostgreSQL scope
`https://ossrdbms-aad.database.windows.net/.default`. Pool pre-ping avoids handing a dead pooled
connection to the application.

## Migration and rollback

1. An authorized migration identity creates the runtime database principal and grants only connect,
   schema usage/create as needed, and DML on application tables/sequences.
2. Run `process-optimizer-migrate` first; it reports a plan and makes no change.
3. Back up or create a restore point, then run `process-optimizer-migrate --apply` from a controlled
   job using the migration identity.
4. Deploy the workflow revision with zero traffic, verify `/ready`, then shift traffic.
5. Roll back by shifting traffic to the prior Container Apps revision. Database migrations require
   a separately reviewed down/forward-fix plan; destructive automatic downgrade is not provided.

The initial migration remains additive. Phase 7B must test PostgreSQL DDL, concurrent decisions,
idempotency constraints, transaction rollback, outbox atomicity, restart recovery, and token refresh
against a disposable non-production server. No local record migration is planned or authorized.

## Non-production exception: ephemeral SQLite demo mode

PostgreSQL B1ms creation failed twice in one region (`RegionalAllocationFailed`, 2026-10-17).
For a non-production demonstration only, Terraform can skip PostgreSQL with
`workflow_persistence = "ephemeral_sqlite_demo"` (default `postgresql`; rejected for `prod`).
The workflow app then sets `WORKFLOW_PERSISTENCE_MODE=ephemeral_sqlite_demo`,
`WORKFLOW_DATABASE_AUTH_MODE=none` and a SQLite file under `/tmp/workflow-demo/`, and runs exactly
one replica (`min_replicas = max_replicas = 1`). Entra validation, Easy Auth, the read-only
aggregate API and Evidence Contract V1 validation are unchanged. `/readiness` and
`/workflow/configuration` report `ephemeral_sqlite_demo`.

What this mode does **not** provide:

- Durability. Every container restart, new revision (including any env change such as
  `aggregate_service_client_ids`), platform move or deletion erases all approvals, tickets and audit
  rows. There is no backup or restore.
- Scale-out. SQLite is container-local; a second replica would hold a separate database.
- Exactly-once ticket creation across restarts. Within one database file, unique constraints and
  conditional state updates allow at most one ticket per approval, and the audit rows commit in the
  same transaction as the state change. A retry after a restart sees an empty database.

Do not use this mode for any production or user-study data.
