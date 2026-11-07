# What-if simulation methodology

> **Synthetic example figures.** Numbers in this document come from the synthetic public demo
> package (`run_60efe2fdce50ecd27b41f404`, built by `scripts/prepare_public_demo_data.sh`). They
> describe invented records, not the BPI Challenge 2013 log or any real organisation.

## Purpose and interpretation

The Phase 5 simulator evaluates transparent arithmetic consequences of stated
assumptions. It does not modify source records, learn a causal effect, predict an
incident outcome, or demonstrate an improvement.

| Category | Meaning | Phase 5 treatment |
|---|---|---|
| Descriptive analytics | What was observed historically | Evidence Contract V1 and Phase 3.5 aggregates |
| Observational association | A measured difference without causal identification | Group-return cohort comparison |
| Hypothetical intervention | Arithmetic conditional on explicit assumptions | Output labelled `hypothetical_non_causal` |
| Demonstrated improvement | Effect established after an approved intervention | Not available in Phase 5 |

## Baseline and count levels

The primary simulation selects one to twenty Evidence Contract findings with metric
`high_frequency_or_repeated_group_handoff`. Each supplies an event-transition
observation count, an event-level `repeated_visit_count`, the common denominator of
classified group-change events (1,261 in the synthetic package), and stable aggregate
references.

Phase 3.5 separately verifies repeated-group-visit events, incidents with at least one
repeated group visit (180 of 600 in the synthetic package) and total incidents. Event and
incident counts remain different typed fields and are never substituted.

## Formula and assumptions

```text
hypothetical events avoided
  = selected repeated-group-visit event baseline
  × eligible-event-share assumption
  × assumed-reduction-share assumption

hypothetical events remaining
  = selected repeated-group-visit event baseline
  - hypothetical events avoided
```

The result may be fractional because it is an expected arithmetic quantity under
assumptions, not an observed count. Values are rounded to three decimals.

| Scenario | Eligible event share | Assumed reduction share | Combined share |
|---|---:|---:|---:|
| Conservative | 10% | 10% | 1% |
| Moderate | 25% | 30% | 7.5% |
| Optimistic | 50% | 50% | 25% |

These configurable defaults are not estimated from BPI13. Validation requires each
named scenario exactly once, rates between zero and one, and monotonic combined
effects.

## Sensitivity and `NOT_ESTIMABLE`

Sensitivity output reports the minimum and maximum hypothetical avoided-event
counts across the scenarios. This is an assumption range, not a confidence,
credible, or prediction interval.

The following remain `NOT_ESTIMABLE`:

- distinct incidents affected, because transition aggregates can overlap;
- elapsed-time impact, because removing a handoff does not show that its interval
  disappears or that end-to-end span changes;
- SLA impact, because no verified SLA target or breach definition is present.

The entire result is `NOT_ESTIMABLE` if required run, provenance, finding, metric,
denominator, repeated-visit, or population inputs are missing or inconsistent. No
value is imputed.

## Reproducibility

Inputs and outputs are frozen strict Pydantic models. The stable simulation ID is
derived from the complete validated request. Results include Evidence Contract
references and the semantic-report SHA-256. The engine reads aggregates only and
has no write path to XES, Parquet records, routing systems, or databases.
