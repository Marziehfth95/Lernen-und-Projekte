# ADR 0003: Use Azure Database for PostgreSQL for cloud workflow state

- Status: Accepted for Phase 7A design; no database has been created
- Date: 2026-10-16

## Context

The multi-replica workflow API needs shared, transactional storage for recommendations,
approvals, decisions, improvement actions, audit records, and the notification outbox. SQLite
is deliberately retained only for single-process local development. The cloud choice must work
with the existing SQLAlchemy 2 models and unit-of-work boundary, support Entra authentication,
and remain operationally modest for an MVP.

## Options

| Criterion | Azure Database for PostgreSQL Flexible Server | Azure SQL Database |
|---|---|---|
| Existing SQLAlchemy migration | Native `psycopg`; no model rewrite identified | Viable, but requires SQL Server dialect/driver and container ODBC packaging |
| Transactional correctness | ACID transactions and relational constraints | ACID transactions and relational constraints |
| Passwordless workload access | Entra principal with short-lived token | Entra principal with short-lived token |
| MVP operations | Burstable tier and stop/start options for non-production | Serverless can be attractive, but driver and dialect validation add work |
| Portability | Close to local relational semantics | Adds SQL Server-specific integration surface |

## Decision

Use Azure Database for PostgreSQL Flexible Server 16 with `postgresql+psycopg`, TLS, Entra-only
authentication, and a user-assigned managed identity. A separate migration identity is the Entra
administrator. The runtime identity receives only database/schema privileges required by the
application after an administrator creates its database principal. No password is accepted in a
production URL.

The existing transaction boundary is unchanged: state transition, action record, immutable audit
record, and outbox insertion commit or roll back together. Schema changes remain explicit through
`process-optimizer-migrate --apply`; the default command is plan-only.

## Consequences

- Phase 7B must test the initial migration and concurrency against a disposable PostgreSQL server.
- PostgreSQL Entra principal/bootstrap SQL cannot be completed until tenant identities exist.
- Backups, point-in-time restore, maintenance windows, private DNS, firewall rules, and production
  high availability require environment-specific decisions.
- Terraform creates no password and therefore stores no database credential in configuration or
  state. Identity/database grants remain an authorized administrative bootstrap step.

This decision does not authorize resource creation or migration of local records.
