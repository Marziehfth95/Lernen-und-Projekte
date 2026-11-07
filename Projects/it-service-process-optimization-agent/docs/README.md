# Documentation

Example figures in these documents come from the synthetic public demo package
(`run_60efe2fdce50ecd27b41f404`). They describe invented records, not the BPI Challenge 2013
log. The only dataset figures kept are the publisher's own reference metadata (file size, MD5,
approximate case and event counts), which let you verify your own download.

## Demo

- [interactive_demo.md](interactive_demo.md): browser demo, isolation, walkthrough
- [interactive_demo_real_local_ai.md](interactive_demo_real_local_ai.md): optional real local LLM via Ollama
- [local_e2e_demo.md](local_e2e_demo.md): the scripted 25-check demo on SQLite and PostgreSQL

## Architecture and design

- [architecture.md](architecture.md): scope, modules and phase boundaries
- [agent_architecture.md](agent_architecture.md), [agent_tool_contract.md](agent_tool_contract.md): LangGraph agent and tools
- [evidence_contract.md](evidence_contract.md): the contract between analytics and the agent
- [automation_architecture.md](automation_architecture.md), [approval_workflow.md](approval_workflow.md): approval and execution
- [approval_policy_revalidation.md](approval_policy_revalidation.md): policy-version-aware approval
- [adr/](adr/): architecture decision records

## Analytics and methodology

- [ingestion_pipeline.md](ingestion_pipeline.md): governed XES ingestion
- [process_mining_methodology.md](process_mining_methodology.md), [metric_definitions.md](metric_definitions.md)
- [what_if_methodology.md](what_if_methodology.md): non-causal what-if simulation

## Safety, security and governance

- [guardrail_policy_coverage.md](guardrail_policy_coverage.md): every guardrail policy and its tests
- [security_model.md](security_model.md), [data_governance.md](data_governance.md)
- [model_evaluation_plan.md](model_evaluation_plan.md), [phase4_evaluation.md](phase4_evaluation.md),
  [phase5_evaluation.md](phase5_evaluation.md), [phase6_evaluation.md](phase6_evaluation.md)

## Persistence and CI

- [workflow_database_operations.md](workflow_database_operations.md): migrations, backup, recovery
- [ci.md](ci.md): the offline reference CI workflow

## Optional Azure boundary (experimental)

The hosted agent never became operational, and hosted model inference was never proven.
None of this is needed for the demo.

- [azure_deployment_architecture.md](azure_deployment_architecture.md)
- [azure_security_design.md](azure_security_design.md)
- [cloud_persistence_decision.md](cloud_persistence_decision.md)
