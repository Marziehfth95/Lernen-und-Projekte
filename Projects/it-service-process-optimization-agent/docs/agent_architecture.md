# Phase 4 agent architecture

Phases 4 and 5 add bounded reasoning and hypothetical simulation over Evidence
Contract V1. They do not change the
ingestion or process-mining calculations, execute business actions, or give a model
access to raw incident records.

## Components

```text
InvestigationRequestV1
        |
        v
Process Analyst -- no trusted finding --> safe termination
        |
        v
Root Cause Investigator <-----------------------+
        |                                       |
        | validated tool request                | more evidence needed,
        v                                       | budgets available
Read-only aggregate tool executor --------------+
        |
        v
Optimization Agent -> DRAFT recommendation
        | optional validated simulation request
        v
Deterministic What-if Engine -> assumptions + simulation ID -> Optimization Agent
        |
        v
Deterministic Evidence Validator
        |                       |
        | exact claims valid    | invalid ID, number, semantics, or causality
        v                       v
VALIDATED recommendation       INSUFFICIENT_EVIDENCE
        |
        v
Human approval required; no action executor exists in Phase 4
```

The graph uses actual conditional LangGraph edges. It can repeat the investigator
and tool nodes, terminates when iteration or tool budgets are exhausted, and never
promotes a recommendation based on model self-assessment.

## State and output contracts

`InvestigationStateV1` is the versioned graph state. It contains the analysis run
ID, untrusted investigation goal, selected findings, pending and completed tool
calls, hypotheses, recommendation candidates, deterministic validation results,
safe errors, audit events, iteration/tool budgets, and final decision.

All externally visible results are frozen Pydantic models. Recommendations contain
stable IDs, cited evidence, exact field-addressed numerical facts, explicitly
separate hypotheses, limitations, a proposed improvement, uncertain expected
outcome, follow-up KPI, implementation risk, status, and
`requires_human_approval=true`.

## Provider modes

- `DeterministicMockProvider` is the default for tests and demonstrations. Results
  are labelled `DETERMINISTIC_MOCK_NOT_REAL_AI` and are byte-stable.
- `FoundryModelProvider` is opt-in. It uses `DefaultAzureCredential`,
  `AIProjectClient`, `get_bearer_token_provider`, and `ChatOpenAI` against
  `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL_NAME`. It is never initialized by
  the default local path.

Both the demo CLI and hosted entry point require explicit paid-call authorization
before real model execution. Retrieved descriptions and the user goal are presented
to the model as untrusted data. System instructions prohibit treating their content
as instructions, causal proof, or authorization.

## Foundry Invocations boundary

The implementation follows Microsoft's current
[LangGraph hosted-agent documentation](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-hosted-agents),
checked on 2026-10-16. Microsoft documents `langchain-azure-ai[hosting]>=1.2.9`,
`InvocationsHostServer`, and overriding `parse_request` and `build_input` for custom
graph state.

`HostedInvocationRequestV1` is parsed by a custom `InvocationsHostServer` subclass.
`build_input` maps it to `InvestigationStateV1`; `parse_output` maps the graph result
back to `InvestigationResultV1`. This avoids assuming that the default
messages-based host accepts arbitrary state.

The locally installed preview SDK 1.2.10 performs its default messages-schema check
before dispatching to the documented `build_input` override. The subclass therefore
also overrides that check after providing the complete typed conversion. This is a
preview-SDK compatibility workaround and must be revalidated on every SDK upgrade.

The local readiness check verifies that Evidence Contract V1 loads and, for real
mode, that both Foundry environment variables exist. It does not contact Azure.
Foundry hosting, identity/RBAC, network paths, durable checkpointers, and protocol
deployment remain unverified until an explicitly approved integration environment
exists.
