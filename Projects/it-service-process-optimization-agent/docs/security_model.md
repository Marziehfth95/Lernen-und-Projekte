# Phase 6 security model

## Identities and authorization

The local development adapter maps opaque bearer tokens to explicitly configured test
identities. Tokens are configuration, never defaults or source code. Arbitrary identity
headers are ignored. In any non-local environment, authentication fails closed because
production token validation is intentionally absent.

Roles are enforced server-side:

- `ANALYST`: view workflow results and submit deterministically valid recommendations.
- `PROCESS_OWNER`: view requests and approve, reject, or request revision.
- `ADMIN`: inspect the persistent business audit and local configuration boundary.

Microsoft Entra ID will replace the local adapter. The production adapter must validate
issuer, audience, tenant, signature and expiry, map application roles from verified
claims, use managed identity for service-to-service calls, and retain fail-closed
behavior.

## Data and trust boundaries

The workflow database stores only aggregate recommendation content, finding IDs,
simulation assumptions, workflow metadata and safe execution results. It must never
store raw XES events, case/event identifiers, resource identifiers, credentials or
secrets. Foundry-hosted agents receive no workflow database credentials and cannot
approve or execute actions.

Persistent audit records are separate from OpenTelemetry traces. API responses use safe
error codes and do not expose exceptions. Remaining production gaps include Entra ID,
database encryption/key management, retention, backup, tamper-evident audit export,
network isolation, rate limiting and security monitoring.
