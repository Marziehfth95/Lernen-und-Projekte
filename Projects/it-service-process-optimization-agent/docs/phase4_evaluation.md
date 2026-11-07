# Phase 4 evaluation

> **Synthetic example figures.** Numbers in this document come from the synthetic public demo
> package (`run_60efe2fdce50ecd27b41f404`, built by `scripts/prepare_public_demo_data.sh`). They
> describe invented records, not the BPI Challenge 2013 log or any real organisation.

## Automated deterministic coverage

The Phase 4 suite uses synthetic aggregate fixtures only and tests:

- correct tool selection for group-return and lifecycle investigations;
- repeated tool calls and a second investigation pass;
- iteration-budget termination;
- invalid tool arguments;
- missing evidence IDs;
- fabricated numerical claims;
- unsupported causal claims;
- the `Completed → Completed` rework semantic trap;
- incomplete investigations;
- valid grounded recommendations;
- model timeouts and error redaction;
- rejection of non-allow-listed write tools;
- byte-stable mock output;
- custom Foundry Invocations state/input/output adaptation when the optional SDK is installed.

These tests do not call a model or Azure. The deterministic provider is clearly
labelled and is not evidence of model quality.

## Evidence Contract demonstrations

Both scenarios run locally against the synthetic public package
(`run_60efe2fdce50ecd27b41f404`) in deterministic mock mode.

### Scenario A: repeated group visits

The graph selected the group-return cohort finding, made three tool calls (cohort
comparison, evidence details and the what-if simulation) and validated these facts:

- 180 incidents are in the group-return cohort;
- median observed span is 1,080,649 seconds with returns;
- median observed span is 310,287 seconds without returns.

Recommendation: conduct a human-led stratified routing review. Only if reviewers
identify a coherent avoidable pattern should a small reversible routing pilot be
prepared for approval. Track incident-level return rate and observed-span
median/p90 in matched cohorts. No causal effect or expected numerical reduction is
claimed.

### Scenario B: Completed-to-Completed

The graph called `inspect_lifecycle_patterns` and `get_evidence_details`. It
validated 244 event transitions, all of which belong to the dominant
`Resolved → Closed` pair and remain within the same group.

Recommendation: make lifecycle-qualified labels mandatory during bottleneck review
and do not classify the dominant sequence as rework without a domain-approved rule.
Review minority lifecycle patterns separately. The expected outcome is improved
analytical precision, not a claimed reduction in elapsed time.

Both recommendations remained non-executing, were deterministically validated,
and require human approval.

## Azure and Foundry status

The optional hosting SDK was installed locally and the custom Invocations adapter
was exercised without a model call. No live Foundry project, deployment, model,
managed identity, RBAC assignment, hosted session, network control, or Application
Insights integration was tested. Real-model quality, latency, token use, and cost
therefore remain unverified.

The installed `InvocationsHostServer` is explicitly marked preview by Microsoft.
Version 1.2.10 required a narrow schema-validation override for the documented
custom-state pattern; this is tracked as an upgrade and production-readiness risk.
