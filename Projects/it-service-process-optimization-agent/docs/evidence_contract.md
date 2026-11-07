# Evidence Contract V1

Phase 3.5 defines an immutable Pydantic contract between deterministic analytics
and future LangGraph agents. The current schema version is `1.0.0`; the deterministic
configuration version is `phase3.5-default-v1`.

## Contract contents

Every `EvidenceFindingV1` contains:

- a stable finding ID;
- canonical dataset name, URL, and SHA-256;
- a deterministic analysis run ID derived from verified input hashes and configuration;
- schema and analysis-configuration versions;
- exact metric name, definition, and unit;
- an aggregate population and allow-listed filters;
- an explicit count level, observation count, and numerator/denominator when applicable;
- measured statistics and a named baseline or comparison group;
- data limitations and `sufficient`, `insufficient`, or `context_only` status;
- the fixed interpretation `observational_non_causal`;
- stable evidence references containing artifact name, artifact SHA-256, metric, and selector.

Models are frozen and reject extra fields. Validators reject `case_id`, `event_id`,
`org:resource`, and resource-identifier fields. V1 changes require a new schema
version rather than an in-place semantic change.

The synthetic public package (`run_60efe2fdce50ecd27b41f404`) contains 8 contract
findings, of which 3 are served after small-group suppression: the group-return cohort
association, one group-handoff finding and one lifecycle context finding. “Sufficient” means it meets the configured descriptive
sample rule; it does not mean causal certainty.

## Read-only tool boundary

`AggregateAnalyticsTools` provides six typed methods over precomputed files:

| Interface | Input controls | Aggregate output |
|---|---|---|
| `get_process_overview` | No filters | One overview |
| `get_bottleneck_findings` | Exact metric/sufficiency filters; limit 1–100 | Contract findings |
| `analyze_group_handoffs` | Exact source/target groups, minimum count; limit 1–100 | Handoff rows |
| `compare_process_cohorts` | Allow-listed metric | Two group-return cohorts and difference |
| `inspect_lifecycle_patterns` | Exact activity/lifecycle filters, minimum count; limit 1–100 | Lifecycle pattern rows |
| `get_evidence_details` | Valid finding or evidence-reference ID | Matching contract evidence |

The interfaces accept no arbitrary file paths, query text, case identifiers, or
resource filters. They read only known aggregate artifacts and cannot mutate data.
Result models enforce the 100-record maximum and repeat the sensitive-field check.

## Future hosted-agent access

The future Foundry-hosted LangGraph agent will call an authenticated application
API implementing these interfaces. That API will use managed identity, explicit
authorization, private networking where required, allow-listed filters, bounded
responses, structured audit telemetry, and artifact integrity verification.
The hosted agent will not receive storage credentials or direct access to raw XES,
cases Parquet, events Parquet, SQLite, or a future cloud database.

