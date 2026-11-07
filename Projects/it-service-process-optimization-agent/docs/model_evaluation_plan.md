# Model evaluation plan

## Execution modes

The runner defaults to `mocked_llm`. It evaluates deterministic orchestration,
tools, validation and safety, is labelled `DETERMINISTIC_MOCK_NOT_REAL_AI`, and is
not evidence of model quality.

Real evaluation requires optional dependencies, Foundry endpoint/deployment
configuration, `--mode real_model --allow-paid-model-call`, and—under pytest—both
`ALLOW_PAID_MODEL_CALLS=true` and `RUN_REAL_MODEL_EVALS=true`. No real model was
authorized or called in Phase 5.

## Dataset and measures

Version `phase5-evaluation-v1` contains aggregate-evidence cases for group returns
with simulation and Completed-to-Completed lifecycle interpretation. It embeds no
incident records.

The runner records exact tool sequence, groundedness, numerical consistency,
semantic safety, absence of write actions, validation decisions, errors, latency,
and simulation IDs. The Foundry adapter additionally records deployment ID, prompt
version `phase5-prompts-v1`, workflow version `phase5-workflow-v1`, invocation role,
latency, safe errors and token usage when supplied by the SDK.

## Cost policy

Estimated cost and pricing assumptions remain null. A future estimate must preserve
deployment, region, effective date, input/output prices, currency and authoritative
source URL. Pricing must not be inferred from a model name.

## Promotion criteria

Before production or approval automation, an approved evaluation must add reviewed
representative cases, explicit score thresholds, repeated nondeterministic runs,
prompt-injection testing, privacy/security review, domain-owner review, and measured
latency, reliability, tokens and documented cost. A passing mock suite is necessary
for code correctness but insufficient for model approval.
