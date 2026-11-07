# Azure security design

## Identity and token validation

Production accepts bearer tokens only through `EntraAuthenticationAdapter`. It resolves Microsoft
signing keys from the configured JWKS endpoint and uses PyJWT to require an RS256 signature,
configured issuer, audience, expiration, and tenant ID. The stable subject is `oid`, falling back to
`sub`. Errors expose stable categories, not token material. Arbitrary identity headers are ignored.
Container Apps built-in authentication returns 401 before the app and enables its token store. If
the platform does not retain the original Authorization header, the app accepts only the forwarded
signed access-token header and performs the same cryptographic/claim validation; it never trusts
`X-MS-CLIENT-PRINCIPAL` or role headers.

Human delegated scopes and application roles map to explicit permissions. Application identities
must also match `ENTRA_ALLOWED_SERVICE_CLIENT_IDS`. All workflow routes reject service identities;
only humans with the relevant local business role can submit, approve, or inspect business state.
The submitter cannot approve the same immutable recommendation version. Analysts can read only
their own approvals and tickets; process owners have the review scope; audit requires admin.

Local opaque tokens are recognized only when `APP_ENV=local`. Production configuration failures
select a fail-closed adapter. `/readiness` also rejects local identities, SQLite, missing Entra settings,
or an invalid aggregate package.

## Proposed Entra application roles

| Permission | Identity | Purpose |
|---|---|---|
| `Aggregate.Read` | Foundry managed identity; authorized humans if required | Six historical aggregate tools and Evidence Contract V1 |
| `Simulation.Execute` | Foundry managed identity only when approved | Deterministic, read-only what-if calculation |
| `Workflow.Submit` | Human user | Future fine-grained submit policy |
| `Workflow.Approve` | Human process owner | Future fine-grained decision policy |
| `Workflow.Audit` | Human administrator/auditor | Future fine-grained audit policy |

Business routes enforce the existing role model, human identity type, and the matching permission
claim. Phase 7B must bind these app roles/scopes and human groups in Entra.

## Least privilege and secrets

- Foundry identity: only API app roles; no database, ACR, or business-write permission.
- Workflow identity: ACR Pull and a least-privilege PostgreSQL principal; no PostgreSQL admin.
- Database-admin identity: PostgreSQL Entra administrator, used only to bind the two database
  principals and establish the schema boundary.
- Migration identity: schema owner with DDL rights only inside `process_optimizer`; it is separate
  from both the Entra administrator and the workflow runtime identity.
- Deployment identity: narrowly scoped Contributor roles plus User Access Administrator only where
  role assignments are required. Do not grant Owner to runtime identities.
- No client secret, database password, or API key is in source or `.tfvars`. Managed identity and
  DefaultAzureCredential provide tokens. Any future non-identity secret belongs in Key Vault and a
  Container Apps secret reference.

## Aggregate API controls

Pydantic request models forbid extra keys, constrain string lengths/enums, cap results at 100 and
serialized tool payloads at 256 KiB, and
accept no path or SQL field. Responses reject `case_id`, `event_id`, `org:resource`, and resource-ID
keys. The API re-hashes the exact artifact allowlist on every call and returns correlation, analysis
run, and contract version. It uses safe 401/403/422/503 errors and never returns raw exceptions.

## Observability versus audit

OpenTelemetry records route templates, status, latency, tool name, aggregate result count, and safe
error/result categories. Application Insights is enabled only by explicit environment setting.
Full prompts, tokens, credentials, actor identifiers, business artifact IDs, and raw incident values
are excluded. Immutable business audit rows remain in the same transaction as workflow state and
are not replaced by operational traces.

Residual gaps: hosted-agent identity tokens, live telemetry redaction in Application Insights
and the agent → workflow path were never verified in Azure.
