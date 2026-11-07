# Architecture

## Scope and principles

The target product analyzes the end-to-end IT Incident Management process. It
does not diagnose or resolve an individual ticket and is not a retrieval chatbot.
Through Phase 6 the HTTP API includes liveness and an authenticated workflow surface. Local CLIs add
governed ingestion, deterministic analytics, semantic validation, an immutable
evidence contract, and bounded LangGraph investigation. A real model adapter is
available only through explicit opt-in configuration; no model was called in Phase 5.

The system begins as a modular monolith. Module boundaries allow independent
testing and later extraction if operational evidence justifies it:

1. `data` validates immutable source artifacts and creates governed Parquet datasets.
2. `process_mining` uses pandas over PM4Py-ingested records for deterministic
   metrics, semantic validation, statistical observations, and versioned evidence.
3. `agents` implements typed LangGraph state, conditional investigation, provider
   adapters, deterministic evidence validation, and the Foundry hosting boundary.
4. `graph` remains available for later cross-workflow composition and approval
   resumability.
5. `tools` exposes narrow, typed, read-only aggregate operations; future write
   operations remain separate and require authorization.
6. `security` will authenticate people, authorize actions, and redact telemetry.
7. `persistence` will hide SQLite initially and a later cloud store behind ports.
8. `observability` will emit structured logs, traces, and metrics with OpenTelemetry.
9. `evaluation` will test deterministic correctness, groundedness, safety, and
   workflow behavior.
10. `simulation` evaluates explicit non-causal assumptions over verified aggregate
    baselines and returns `NOT_ESTIMABLE` for unsupported impact measures.
11. `workflow` owns the database-authoritative approval state machine, deterministic
    ticket execution, notification outbox, and persistent business audit trail.

## Target flow (future phases)

```text
Immutable XES -> validated Parquet -> deterministic analytics
                                      -> semantic validation
                                      -> Evidence Contract V1
                                                  |
                                      authenticated aggregate API
                                                  |
Human request -> Process Analyst -> Root Cause Investigator
                                      |       ^
                                      v       | bounded evidence loop
                              typed read-only tools
                                      |
                                      v
                              Optimization Agent
                                      |
                         optional typed what-if request
                                      |
                         deterministic simulation engine
                                      |
                        hypothetical result + assumptions
                                      |
                                      v
                         deterministic Evidence Validator
                                      |
                                      v
                         DRAFT / VALIDATED / INSUFFICIENT
                                      |
                handoff of exact validated version + evidence + assumptions
                                      |
         separate authenticated workflow API -> PENDING_APPROVAL
                                  |
                  PROCESS_OWNER decision (not the agent)
                                  |
             deterministic application service -> local auditable ticket
```

Analytics must produce versioned evidence packages containing source provenance,
query parameters, metric definitions, and reproducible results. LLM output must
cite those packages and clearly distinguish evidence from assumptions. Simulation
results must label assumptions and must not be presented as observations.

## Phase 3.5 evidence boundary

The hosted-agent boundary is `EvidenceContractV1`, not events Parquet. Each finding
is immutable and carries dataset provenance, run/configuration versions, an explicit
count level, statistics, limitations, sufficiency, a non-causal interpretation, and
stable references whose hashes bind them to aggregate artifacts.

Seven allow-listed interfaces read only precomputed aggregates or calculate a
non-causal result over them:
`get_process_overview`, `get_bottleneck_findings`, `analyze_group_handoffs`,
`compare_process_cohorts`, `inspect_lifecycle_patterns`, and
`get_evidence_details`, and `simulate_group_transition_reduction`. Inputs reject
extra fields and invalid identifiers; outputs
are capped at 100 records. The interfaces cannot accept storage paths, execute
queries, read raw records, or mutate state.

In a future authorized Azure deployment, the Foundry-hosted LangGraph agent
will call an authenticated application API using managed identity. The API, not the
agent, will verify artifact integrity and enforce authorization, filters, response
bounds, redaction, and audit telemetry. Raw XES and record-level Parquet storage
remain outside the agent trust boundary.

Phase 4 implements these interfaces locally. Tool calls are allow-listed, validated
twice, bounded, and audited with stable evidence-reference IDs. The Evidence
Validator independently resolves finding IDs and numerical field paths, rejects
unsupported causal language, and prevents the `Completed → Completed` rework trap.
There is no write-action node.

## Phase 5 simulation and evaluation boundary

The optimization node may request one bounded deterministic simulation and resume
with its stable simulation ID. Recommendations cite exact assumptions and keep
hypothetical quantities separate from observed facts. The validator resolves the
citation against the audited tool result.

Evaluation uses aggregate-only cases and defaults to a deterministic provider. The
real provider can record deployment, prompt/workflow versions, latency, token usage
when available and safe errors, but cannot run without explicit paid-call approval.

## Phase 6 workflow boundary

The LangGraph host and workflow API are separate trust boundaries. Foundry may return
a validated recommendation, but it receives no workflow database credential and has
no approval or execution tool. An authenticated analyst submits the complete candidate
and audited tool records; the workflow service reruns deterministic validation against
its trusted Evidence Contract before persisting `PENDING_APPROVAL`.

SQLite is the local SQLAlchemy adapter. Migration `0001` creates recommendations,
approval requests/decisions, improvement actions, notification outbox, audit events,
and migration history. The application service owns transactions and stable interfaces,
so a later Azure database adapter does not change approval semantics. Business state
survives API and agent restarts; LangGraph checkpointing is not authoritative for writes.

## Trust boundaries

- Raw incident data is sensitive and remains outside source control.
- Hosted agents receive precomputed aggregate evidence only and no storage credentials.
- The LLM has no direct persistence credentials and cannot mutate databases.
- All write actions require authenticated human approval, typed validation,
  authorization checks, idempotency controls, and an audit record.
- Logs and traces use allow-listed fields and redact sensitive values.
- Local startup has no Azure dependency. Azure integration is configuration-driven
  and deferred.

## Phase 7A deployment boundary

The deterministic workflow runtime is separately deployable on Azure Container Apps.
The LangGraph runtime is prepared for Microsoft Foundry Agent Service and the
Invocations protocol, following Microsoft's current
[LangGraph hosted-agent guidance](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-hosted-agents).
The Foundry identity calls fixed aggregate endpoints with `Aggregate.Read` and has no
database or business-write role. The Container Apps service verifies Entra tokens,
artifact hashes and count-level-safe outputs, then owns PostgreSQL transactions for
approvals, actions, audit and outbox state. Operational OpenTelemetry traces are kept
separate from immutable business audit records.

ADR 0003 selects Azure Database for PostgreSQL Flexible Server; SQLite remains local
only. Terraform and `azure.yaml` are plans, not authorization to deploy. Network topology,
Entra app registration, PostgreSQL grants, provider initialization, image signing/scanning,
and the preview Foundry deployment must be verified in an explicitly approved Phase 7B.

Cloud status: the Terraform plans were applied once to a short-lived Azure development
environment (2026-10). The workflow API answered `/health` and `/ready` there, but the Foundry
Hosted Agent never reached a working state and hosted model inference was never proven. That
environment is not part of this repository. A separate local real-model run against an Azure
OpenAI `gpt-4.1-mini` deployment (2026-10-18) produced one recommendation that passed
deterministic validation. Its wording, with synthetic numbers and IDs, is kept as a
regression fixture in `tests/data/model_text_regression_recommendation.json`. The public
demo needs no Azure resource.
