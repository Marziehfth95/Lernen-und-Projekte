# Azure deployment architecture (Phase 7A)

No Azure resource was created in this phase. This is the deployable target and its trust boundaries.

> **Status: optional and experimental.** This target was deployed once to a short-lived
> development environment (2026-10). The workflow `/health` and `/ready` endpoints answered
> there, but the Foundry Hosted Agent never became operational, so the agent → workflow path
> below and hosted model inference are **unverified**. No Azure resource is needed for, or used
> by, the local demo.

```text
Human user -- Entra access token --> Container Apps Workflow API
                                          |  Entra token / TLS
Foundry Hosted Agent -------------------->|  aggregate-only endpoints
  LangGraph + Invocations                  |
  managed identity, no write role          +--> PostgreSQL workflow state
                                          |     managed-identity token
                                          +--> packaged aggregate artifacts

Workflow API + hosted agent --> OpenTelemetry --> Application Insights / Log Analytics
```

## Component A: Microsoft Foundry Hosted Agent

The agent image runs `python -m process_optimizer.agents.hosting` on port 8088. It uses the currently documented
`InvocationsHostServer` custom-state hooks to validate structured JSON, build
`InvestigationStateV1`, and serialize `InvestigationResultV1`. Its managed identity requests the
workflow API audience with the `Aggregate.Read` app role and, only if simulations are allowed,
`Simulation.Execute`. It retrieves Evidence Contract V1 and invokes fixed aggregate routes over
HTTPS. It receives no PostgreSQL role, business-write role, SQLite file, Parquet path, or raw BPI13
artifact. Foundry Hosted Agents and the relevant hosting SDK remain preview dependencies and must
be revalidated immediately before deployment.

## Component B: Azure Container Apps Workflow API

The workflow image serves FastAPI liveness `/health`, readiness `/readiness`, human approval endpoints,
immutable audit/outbox state, the deterministic ticket executor, and aggregate-only reads. The
service validates Entra tokens in application code even when Container Apps built-in authentication
is enabled as an outer gate. Its managed identity pulls from ACR and authenticates to PostgreSQL.
The deployment pipeline stages only the six allow-listed, hashed aggregate artifacts under
`deploy/aggregate-package`; the original XES, events, cases, and resource identifiers are excluded.

## Network and data flow requirements

- TLS is mandatory. Terraform disables PostgreSQL public network access and uses delegated private
  subnets plus private DNS. Phase 7B must validate that route and, where supported by the selected
  Foundry hosting region/preview, restrict aggregate API ingress.
- Until private Foundry-to-Container-Apps networking is validated, ingress may be public but must
  require Entra, exact audience/issuer, app-role authorization, service-principal allowlisting, and
  bounded responses.
- PostgreSQL public access remains disabled; do not weaken this to make an initial test convenient.
- ACR admin credentials remain disabled. Workloads use managed identity.
- The aggregate package manifest binds every artifact hash, analysis run, Evidence Contract version,
  and source-dataset SHA-256. Verification occurs at readiness and before every tool operation.

## Infrastructure boundaries

Terraform under `infra/terraform` exclusively owns the resource group, ACR, ACR role assignments,
Container Apps environment and Workflow API, identities, network, PostgreSQL, Log Analytics, and
Application Insights. `azure.yaml` adopts an existing Foundry project by endpoint and owns only one
prebuilt Hosted Agent version. It does not declare infrastructure or a model deployment. Never run
`azd up`, `azd provision`, or `azd down` in this split-ownership design; after explicit approval use
only the service-scoped Hosted Agent deployment command. The Foundry project's managed identity,
not the agent runtime identity, receives repository-reader access to the Terraform-owned ACR. The
dedicated agent identity is created later by Foundry and receives only aggregate API access.

## Phase 7B prerequisites and roles

- A subscription and region where the selected Foundry project, hosted-agent preview, model, and
  Container Apps features are all available, with model quota and an explicitly approved deployment
  name exposed as `FOUNDRY_MODEL_NAME`.
- A Foundry project endpoint and a deployer with the documented **Foundry Project Manager** role for
  hosted-agent deployment. Runtime model access must be limited to the project/model role required
  by the chosen service; it is not inherited by the workflow API.
- An Entra API registration with the expected application ID URI, human delegated permissions/app
  roles, `Aggregate.Read` and `Simulation.Execute` application roles, and admin consent where the
  tenant requires it. Assign only aggregate roles to the hosted-agent managed identity.
- A deployment principal able to create the listed resource types, plus User Access Administrator
  (or a custom role limited to the exact assignments) for ACR Pull and managed-identity bindings.
  Runtime identities are never Owner or Contributor.
- Registered Azure resource providers for Container Apps, Container Registry, PostgreSQL, Managed
  Identity, Operational Insights, Application Insights, and Foundry. Configure a protected remote
  Terraform backend before collaborative deployment.

Official references reviewed 2026-10-16:

- [Host LangGraph agents in Foundry](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-hosted-agents)
- [Container Apps authentication with Entra](https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra)
- [Container Apps managed identities](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity)
- [Azure Monitor OpenTelemetry for Python](https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-enable?tabs=python)
