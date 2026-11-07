# ADR 0002: Host future LangGraph agents in Microsoft Foundry

- Status: Accepted; local Phase 4 boundary implemented, deployment not authorized
- Date: 2026-10-16

## Context

The system needs an agent layer that can accept structured process-analysis
requests, reason over governed aggregate evidence, pause for human approval, and
remain operationally separate from deterministic analytics. Phase 3 must not add
an LLM or cloud runtime.

Microsoft documents hosting LangGraph agents through
`langchain_azure_ai.agents.hosting` in Microsoft Foundry Agent Service, with both
Responses and Invocations protocols. The Invocations protocol fits the planned
structured request/response contract and custom graph state better than treating
the analytical engine as an unstructured chat endpoint.

Official reference:
[Host LangGraph agents as Foundry hosted agents](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-hosted-agents).

## Decision

Phase 4 packages the LangGraph orchestration boundary for a later authorized
deployment as a
Microsoft Foundry hosted agent and expose it through the Invocations protocol.
Requests and responses will use versioned structured models. The hosted agent may
consume `EvidenceContractV1` objects and aggregate report references, but not raw
incident events, case identifiers, event identifiers, or resource identifiers.

The current local analytics engine remains independent:

```text
validated Parquet -> deterministic local analytics -> Evidence Contract V1
                                                        |
                                     authenticated read-only aggregate API
                                                        |
                         LangGraph -> Microsoft Foundry Agent Service
                                   -> Invocations protocol
```

## Consequences

- Phase 3 remains reproducible without Azure credentials or LLM availability.
- Analytical metrics and bottleneck rules stay testable independently of prompts.
- Future agents receive a narrow, explainable evidence contract.
- The hosted agent receives no Parquet/database credentials. Managed identity,
  application authorization, private networking where required, bounded tool
  responses, artifact-hash checks, and audit telemetry are implementation gates.
- Foundry identity, networking, deployment, tracing, scaling, data residency,
  model selection, cost controls, and approval integration remain future decisions.
- Protocol and SDK behavior must be revalidated against current Microsoft
  documentation at implementation time.

## Phase 7A implementation note

Phase 4 adds a custom `InvocationsHostServer` adapter because the graph uses
`InvestigationStateV1`, not the default messages state. It overrides request
parsing, graph-input construction, and output serialization. The optional SDK was
locally exercised without Azure or a model call.

Phase 7A replaces hosted local-file tool access with an Entra-authenticated HTTPS
client. The graph bootstraps its immutable aggregate Evidence Contract through the
workflow service and calls fixed, bounded aggregate routes. The Foundry identity has
no access to Parquet, SQLite, PostgreSQL, approval routes, or action execution. The
hosting package and `azure.ai.agent` azd surface remain preview dependencies and must
be revalidated before Phase 7B.

## Non-decision

This ADR does not authorize an Azure deployment, Foundry resource creation, model
invocation, paid service use, or business-action execution.
