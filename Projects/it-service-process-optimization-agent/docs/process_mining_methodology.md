# Phase 3 process-mining methodology

## Analytical boundary

Phase 3 is a deterministic local analytics engine. It reads only manifest-verified
Phase 2 Parquet artifacts and never calls an LLM, LangGraph, Microsoft Foundry, or
Azure. The `org:resource` column is deliberately excluded from the analysis load
and from evidence packages.

The analysis refuses to run if any Phase 2 cases, events, schema, or quality file
does not match its SHA-256 value in `ingestion_manifest.json`.

## Activity and lifecycle semantics

The activity definition is the exact original XES `concept:name`, stored as
`original_event__concept:name`. Observed values are `Accepted`, `Completed`,
`Queued`, and `Unmatched`. Values are neither renamed nor combined.

The original `lifecycle:transition` is retained as a separate contextual field. It
has more detailed observed values, but Phase 3 does not silently substitute it for
the activity or interpret values such as `Resolved` and `Closed` as verified case
outcomes. The activity-level process model is therefore deliberately coarse.

## Ordering and time

Events are ordered by `event_index` inside each `case_id`, using a stable sort.
Timestamps do not reorder events. This preserves the original XES trace order and
provides deterministic behavior if timestamps tie.

All exported timestamps are UTC. A case's observed span is its last timestamp minus
its first timestamp. A transition elapsed time is the target event timestamp minus
the source event timestamp for two consecutive source-ordered events. These are
calendar elapsed times and include waiting, inactivity, weekends, and any unobserved
work. They are not SLA performance, resolution time, or active processing time.

## Computations

- Variants group exact ordered activity sequences and report frequency, coverage,
  and cumulative coverage.
- Directly-follows analysis uses every consecutive activity pair and reports event
  and case counts plus elapsed-time median, p75, p90, mean, minimum, and maximum.
- A handoff is a consecutive event pair where both `org:group` values exist and
  differ. Missing values are counted as unclassified and never imputed.
- A repeated group visit is a handoff whose target group appeared earlier in the
  same case.
- Repeated activity counts occurrences beyond the first occurrence of that activity
  in a case. An immediate loop is a consecutive identical activity pair.
- Product, impact, and organization-country cohorts are non-overlapping because the
  observed value is constant within each case. `organization involved` uses
  explicitly overlapping observed-value cohorts because a case can contain more
  than one value.
- Temporal cohorts use the UTC calendar quarter of each case's first observed event.
  A case span may extend beyond its cohort quarter.

Pandas is used for these explicit computations. PM4Py remains the governed XES
parser in Phase 2; using pandas here avoids hidden activity remapping and makes each
aggregation and ordering rule reviewable.

## Bottleneck-candidate rules

Default thresholds are configurable through the CLI:

| Rule | Default |
|---|---:|
| Minimum observations per pattern | 30 |
| Transition median / global median | 2.0 |
| Transition p90 / global p90 | 1.5 |
| Handoff share among source group's handoffs | 0.20 |
| Return-to-visited-group rate | 0.20 |
| Maximum findings | 50 |

The duration baseline is all non-negative consecutive-event elapsed times. A
duration pattern qualifies when its median ratio or p90 ratio meets its threshold.
A handoff qualifies when its source share or repeated-visit rate meets its threshold.
Patterns below the minimum count are excluded and counted in the evidence package.

The ranking score is a triage heuristic, not a business metric:

```text
duration score = max(median ratio / median threshold,
                     p90 ratio / p90 threshold) * ln(1 + observations)

handoff score = max(source share / source-share threshold,
                    repeated-visit rate / repeat threshold) * ln(1 + observations)
```

Every result is classified `observation_not_confirmed_cause`. Ranking does not prove
delay, waste, service failure, or causality.

## Reproducibility

The CLI produces sorted deterministic tables, stable SHA-256-derived variant and
finding IDs, and JSON with sorted keys. It writes files atomically. No wall-clock
run timestamp is embedded in analytical outputs, so the same governed inputs,
configuration, code, and dependency versions produce byte-identical artifacts.

