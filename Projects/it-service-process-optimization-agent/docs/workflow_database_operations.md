# Workflow Database: Migrations, Backup, Rollback and Recovery

Status (2026-10-30): implemented and tested offline, and against an isolated local
PostgreSQL 18.4 cluster. **Not deployed.** Azure Database for PostgreSQL Flexible Server
(v16, B1ms) is declared in Terraform but does not exist yet (`workflow_persistence =
"ephemeral_sqlite_demo"` in `dev.tfvars`).

## 1. Design

| Property | Implementation |
|---|---|
| Versioned | `persistence/migrations/__init__.py:MIGRATIONS`, an append-only registry. Each entry is a frozen SQLAlchemy Core module (`v0001_initial.py`) and is never derived from the ORM models |
| Drift guard | `test_models_match_the_frozen_migrations` compiles the models and the cumulative migrations for PostgreSQL and SQLite and requires identical DDL. A model change without a new migration fails CI |
| Idempotent | Each migration runs in one transaction together with its ledger row (`schema_migrations`). Re-running applies nothing. 0001 uses `checkfirst` and never alters or drops anything |
| Concurrency | `pg_advisory_xact_lock` serialises concurrent jobs (tested: 4 parallel runners → exactly one applies) |
| Version skew | A database containing a version this code does not know → `verify()` and `apply()` refuse ("newer than this code") |
| Separate from startup | The durable runtime calls `require_migrated()` only. The API passes `apply_migrations=False` whenever `persistence_mode() == "durable_postgresql"`, and an unmigrated database yields HTTP 503 `WORKFLOW_NOT_CONFIGURED` with no DDL (tested) |
| Explicit entry point | `python -m process_optimizer.persistence.cli` (`process-optimizer-migrate`). It defaults to read-only status, offers `--verify`, and `--apply` against a durable database also needs `WORKFLOW_MIGRATION_APPLY=true`, which only the job sets |
| Least privilege | The runtime role gets SELECT/INSERT on `recommendations`, `approval_decisions` and `audit_events`; SELECT/INSERT/UPDATE on `approval_requests`, `improvement_actions` and `notification_outbox`; SELECT on `schema_migrations`. It gets no DELETE, TRUNCATE or DDL (tested: SQLSTATE 42501 for 9 forbidden statements) |
| Existing local SQLite | Ledger structure unchanged. Databases created by the former `create_all` are recognised as 0001 and left byte-identical (tested on a synthetic copy). The original demo databases were never opened by these tests |

## 2. Durability verified (isolated PostgreSQL)

`test_postgres_workflow_records_are_durable_across_restart` submits, replays the
submission (idempotency), approves, replays the decision and executes. It then disposes
of the connection pool (a simulated restart) and reads everything back:

- approval request and state `EXECUTED`;
- immutable recommendation version hash;
- ticket and approval reference;
- submission and decision idempotency keys;
- audit chain.

Local cluster: `initdb` in the session scratchpad, TCP on 127.0.0.1:55432 only, no Unix
socket. A throwaway database and roles are created per test and dropped afterwards.

Command:

```bash
PROCESS_OPTIMIZER_TEST_POSTGRES_ADMIN_URL="postgresql+psycopg://postgres@127.0.0.1:55432/postgres" \
  .venv/bin/python -m pytest tests/test_postgres_migrations.py
```

**Not verified locally:** the Azure-only `pgaadauth_*` principal functions, Entra token
login and private-endpoint networking. These are covered only once the first Azure run
of the jobs happens.

## 3. Azure procedure (each step needs approval)

1. `terraform plan` with `workflow_persistence = "postgresql"`, `deploy_database_jobs = true`
   and the new `workflow_image` digest → review → `apply`. This creates the server,
   database, Entra admin, two jobs and the new app revision.
2. `az containerapp job start -n caj-dbboot-<suffix> -g <rg>`. Runs as `id-dbadmin`: maps
   `id-workflow` and `id-migration` to roles, revokes PUBLIC, and grants CONNECT, plus
   CREATE on the database to the migration role.
3. `az containerapp job start -n caj-migrate-<suffix> -g <rg>`. Runs as `id-migration`:
   creates the schema it owns, applies migrations, and issues the runtime grants.
4. Verify: the job execution log shows `"status": "current"`;
   `GET /workflow/configuration` (ADMIN) shows `migration_version = 0001` and
   `persistence_mode = durable_postgresql`.

## 4. Backup

- Automated backups by the Flexible Server: `backup_retention_days = 7`, geo-redundant
  `false` (Terraform). Point-in-time restore is possible within those 7 days.
- Before every future migration: note the UTC time (the PITR anchor) and run `--verify`
  to record the current level.
- A logical export (`pg_dump`) needs a host inside the VNet, which does not exist today.
  Until one is added, PITR is the only backup.

## 5. Rollback

Migrations are forward-only. There are no down-migrations, because an automatic DROP on
audit and approval data is not acceptable.

| Situation | Procedure |
|---|---|
| Migration job fails | The transaction rolls back and the ledger is unchanged. Fix the cause and re-run the job (idempotent) |
| New app image misbehaves, schema unchanged | Re-plan with the previous `workflow_image` digest |
| New app image needs rolling back after a newer migration ran | The older image refuses to start ("newer than this code"). Either roll forward with a fix, or restore the database by PITR to a time before the migration (next row) |
| Data corruption or a bad migration | PITR to a **new** server (`az postgres flexible-server restore`, within 7 days) → point `WORKFLOW_DATABASE_URL` at it through Terraform → run `caj-dbboot` and `caj-migrate` against it (idempotent) → verify. The old server stays until a reviewed destroy plan exists |
| Abandon PostgreSQL | Re-plan with `workflow_persistence = "ephemeral_sqlite_demo"` and `deploy_database_jobs = false`. Terraform would then **destroy the server and its data**, which needs separate destroy approval and an explicit backup decision first |

## 6. Recovery objectives (demo scope, not an SLA)

| | Value | Basis |
|---|---|---|
| RPO | Point-in-time within the 7-day window; exact RPO **not verified here**; confirm in Microsoft Learn before relying on it | Flexible Server automated backups; never exercised |
| RTO | Restore time plus one Terraform apply plus two job runs | Not measured |
