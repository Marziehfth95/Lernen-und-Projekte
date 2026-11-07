# Process-mining metric definitions

| Metric | Definition | Denominator / unit | Interpretation limit |
|---|---|---|---|
| Case count | Distinct Phase 2 `case_id` values | Cases | A case is the source XES trace, not a verified resolved incident |
| Event count | Phase 2 event rows | Events | No events are synthesized or removed |
| Observed case span | Maximum timestamp minus minimum timestamp within a case | Calendar seconds | Not verified resolution or active-work time |
| Events per case | Count of source events by case | Events/case | Includes all observed lifecycle states |
| Activity frequency | Count of exact `concept:name` value | Events | Lifecycle is not merged into activity |
| Variant | Exact source-ordered activity tuple | Cases and share of cases | Coarse because activity has four observed values |
| Directly-follows frequency | Count of consecutive source-ordered activity pairs | Pairs | Does not imply causality |
| Transition elapsed time | Target timestamp minus source timestamp | Calendar seconds | Includes waiting and inactivity |
| Group handoff | Consecutive non-null `org:group` values that differ | Handoffs | Routing observation, not staffing or ownership intent |
| Source handoff share | Pattern count divided by all classified handoffs from its source group | Fraction | Small source populations can be unstable; minimum count applies |
| Repeated group visit | Handoff to a group already observed earlier in the case | Handoffs | Does not prove avoidable rework |
| Repeated activity | Activity occurrences after its first occurrence in a case | Events beyond first | May reflect legitimate lifecycle progression |
| Immediate loop | Consecutive identical activity values | Pairs | Coarse activity can hide distinct lifecycle transitions |
| Comparative cohort span | Distribution of observed case spans among cases in a dimension cohort | Calendar seconds | Organization-involved cohorts overlap when cases have several values |
| Temporal cohort | Cases grouped by UTC quarter of first event | Cases | Earlier cohorts are very small and span beyond cohort boundaries |

All duration distributions report observation count, median, p75, and p90; output
tables additionally include mean, minimum, and maximum. Medians and quantiles are
preferred for comparison because the observed durations are strongly skewed.

