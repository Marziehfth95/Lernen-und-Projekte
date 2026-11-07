# Phase 5 evaluation

> **Synthetic example figures.** Numbers in this document come from the synthetic public demo
> package (`run_60efe2fdce50ecd27b41f404`, built by `scripts/prepare_public_demo_data.sh`). They
> describe invented records, not the BPI Challenge 2013 log or any real organisation.

Phase 5 covers simulation formulas, stable IDs, configurable and invalid
assumptions, evidence references, count-level separation, `NOT_ESTIMABLE` behavior,
simulation tool selection, exact recommendation citation, iteration limits, missing
evidence, malformed calls, causal claims, lifecycle semantics, failures and absence
of business writes.

Model tests are isolated under `tests/model_evaluation/` and skipped without two
explicit paid-call opt-ins. The default suite makes no Azure or model call.

## Commands

```bash
process-optimizer-simulate
process-optimizer-evaluate
```

The simulation CLI uses the verified handoff finding with the largest repeated
visit count unless `--finding-id` is supplied. It reports conservative, moderate
and optimistic hypothetical event-count effects. Incident, elapsed-time and SLA
effects remain `NOT_ESTIMABLE`.

The evaluation runner executes group-return and lifecycle scenarios against the
real aggregate Evidence Contract and is mock-labelled by default.

## Mock results on the synthetic public package

The run used the synthetic Evidence Contract `run_60efe2fdce50ecd27b41f404`. The default
selected finding was `finding_311221558005b5a481351906`, representing 32 group-transition
events and 32 repeated-visit events out of 1,261 classified group changes. Global context
was 180 incidents with returns out of 600 incidents.

Simulation `simulation_8d2ed3a6deb56675049baf3f` produced hypothetical avoided
event quantities of 0.32, 2.4 and 8.0 under the default conservative, moderate and
optimistic assumptions. These are not observed counts or predicted effects.
Incident, time and SLA outputs were `NOT_ESTIMABLE`.

The deterministic evaluation runner passed both aggregate cases. Group returns
called cohort comparison, evidence details and simulation; lifecycle analysis
called lifecycle patterns and evidence details. Both recommendations passed the
deterministic validator. Token usage and cost were null because no model ran.

## Readiness interpretation

Phase 5 can prepare an approval-gated pilot hypothesis but cannot approve or
execute it. Demonstrated improvement requires a future authorized pilot,
pre-registered measures, a defensible comparison design, sufficient samples,
domain review and post-intervention evaluation. Simulation is not a causal
estimate, business case, demonstrated improvement, or SLA commitment.
