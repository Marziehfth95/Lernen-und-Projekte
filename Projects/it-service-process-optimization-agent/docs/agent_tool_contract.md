# Phase 4 agent tool contract

The graph reuses the six Phase 3.5 read-only methods and adds one deterministic
simulation method. It has no SQL tool, arbitrary
file reader, raw Parquet reader, routing update, ticket mutation, or generic HTTP
tool.

| Tool | Validated input | Maximum output | Typical use |
|---|---|---:|---|
| `get_process_overview` | Empty strict model | 1 | Dataset context |
| `get_bottleneck_findings` | Exact metric/sufficiency and limit | 100 | Select aggregate observations |
| `analyze_group_handoffs` | Exact group filters, minimum count, limit | 100 | Inspect aggregate routing patterns |
| `compare_process_cohorts` | One allow-listed metric | 1 | Compare return/no-return incident cohorts |
| `inspect_lifecycle_patterns` | Exact activity/lifecycle filters and limit | 100 | Qualify coarse activity transitions |
| `get_evidence_details` | Stable finding/reference ID | 100 | Resolve contract evidence |
| `simulate_group_transition_reduction` | Run ID, 1–20 finding IDs, three bounded scenarios | 1 | Calculate hypothetical event-count effects |

## Execution controls

1. A `ToolRequest` restricts the name to the seven methods.
2. The executor revalidates arguments with the exact Phase 3.5 input model.
3. Calls are counted against `tool_call_budget`.
4. Results remain aggregate and bounded.
5. Evidence reference IDs are extracted into `ToolCallRecord` for audit.
6. Validation errors and runtime failures produce stable error codes without raw
   SDK messages, paths, secrets, or sensitive values.
7. The graph stops safely rather than asking the model to repair an unauthorized
   or malformed request implicitly.

Tool results are evidence inputs, not executable instructions. Text contained in
an aggregate description is never treated as a system prompt or approval.

## Numerical claim validation

Each observed numerical fact cites a finding ID and exact contract field path, for
example `measured_statistics.with_return_incident_count`. The deterministic
validator resolves that field in trusted Evidence Contract V1 and compares the
number exactly. Missing IDs, invalid paths, and mismatches fail validation.

The validator separately rejects unsupported causal wording and the specific
semantic trap that labels `Completed → Completed` as confirmed rework. No model
self-evaluation can override these checks.
