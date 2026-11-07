# Guardrail Policy Coverage and Fail-Closed Review

Review date: 2026-10-30 (BU-03 added the same day); CA-04 to CA-07 and EV-04 added, CA-03 extended
and the policy schema raised to **2.0.0** on 2026-11-01. Policy file: `src/process_optimizer/guardrails/policy.v1.json`.
The effective policy version (SHA-256, k = 10) is
`5a886139a16221c9f1fc6cbcc7754e2086bbfcdfdc2ce74f2a9bdc1a15dae358`
(previously `c2d0bbbe…`). Every decision and
audit event carries this version.

Scope: the 31 policy IDs of the original guardrail design proposal (not included here),
plus CA-03, which was added for architecture decision 7, and CA-04 to CA-06, which were added
after the first real Hosted Agent run (2026-10-31), and CA-07 and EV-04 from the hardening pass.
That makes **37 policies**. No other policy ID exists in the repository.

Policy schema version **2.0.0**: 2.0.0 adds required sections (`output.intervention_claim`,
`output.inefficiency_claim`, `output.expected_outcome`, `claim_evidence`). A 2.x loader rejects a
1.x document (missing sections) and a 1.x loader rejects a 2.x document (`extra=forbid`, literal
schema version), so the change is breaking in both directions and takes a major bump, not 1.1.0.

Legend: **E** = enforced by a runtime check with a test. **C** = enforced by construction
(schema or type) with a test. No policy is deferred and none is marked not applicable.

**Result: 37 of 37 policies enforced** (32 E, 4 C, 1 C+E).

| ID | Checkpoint | Status | Fail-closed behaviour | Test(s) |
|---|---|---|---|---|
| IN-01 | invocation input | E | Strict schema, `extra=forbid`, `StrictBool stream` → HTTP 400 before graph | `test_hosted_invocation_http_rejects_invalid_requests_without_model_call` |
| IN-02 | invocation input | E | NFKC; control, format, bidi and unassigned characters; > 2000 chars → BLOCK | `test_input_guardrail_blocks_injection_and_smuggling` |
| IN-03 | invocation input | E | Injection, tool/schema-smuggling, URL, code-fence and base64 patterns → BLOCK; the error lists codes only | same, `test_blocked_goal_never_reaches_the_graph`, `test_hosted_http_rejects_injected_goal_without_model_call` |
| IN-04 | pre-model | C | Goal sent only as the `untrusted_investigation_goal` data field | `test_goal_is_sent_only_as_untrusted_data_never_in_the_system_prompt` |
| DM-01 | tool result / audit | C | `AggregateToolResult` and `ToolCallRecord` reject identifiers → tool error | existing `test_semantic_contract` |
| DM-02 | tool, API, model view | E | Counts 1..k-1 and complements → whole item suppressed **before** limiting; post-tool re-check blocks; evidence-contract route and model index exclude such findings | `test_small_group_detection`, `test_tools_suppress_before_limiting`, `test_overlapping_queries_…`, `test_complementary_queries_…`, `test_cohort_comparison_…`, `test_evidence_contract_route_serves_only_unsuppressed_findings`, `test_model_never_sees_findings_with_small_groups` |
| DM-03 | host startup | E | Capture variable must be exactly `"false"`; `"true"` raises before the server is built | `test_host_startup_refuses_unsafe_configuration`, `test_observability_tracing` |
| TL-01 | pre-tool | E | Stage allowlist from policy; unknown stage or tool → BLOCK | `test_stage_tool_allowlist`, `test_investigator_simulation_request_is_blocked_in_graph` |
| TL-02 | pre-tool | C | Per-tool Pydantic models, `extra=forbid` | existing `test_invalid_tool_arguments_are_rejected` |
| TL-03 | pre-tool | E | Iteration and tool-call budgets → SAFE_TERMINATION | existing budget tests |
| TL-04 | client | C | HTTPS-only fixed routes, `/.default` scope | existing client tests |
| EV-01 | API readiness | E | Manifest hash mismatch → 503 | existing phase 7 tests |
| EV-02 | host startup | E | `EVIDENCE_CONTRACT_SHA256` must be pinned (64 hex), and the fetched contract must match → refuse start | `test_evidence_contract_pin_detects_any_change`, `test_hosting_main_refuses_a_contract_that_does_not_match_the_pin` |
| EV-03 | post-model | E | Selected IDs ⊆ visible trusted IDs, else `EVIDENCE_MISSING` | existing `test_missing_evidence_terminates_safely` |
| NU-01..03 | validator | E | Validator codes → BLOCK; unknown code → BLOCK | `test_validator_suite_is_the_regression_oracle`, `test_unknown_codes_and_incomplete_validation_block` |
| SE-01 | validator | E | Semantic trap → BLOCK | oracle test |
| CA-01 | output | E | Validator patterns everywhere; extended list in assertive fields → BLOCK | `test_output_text_rules_block_savings_and_assertive_causality` |
| CA-02 | output | E | Currency, FTE, hours saved, savings, ROI, payback → BLOCK | same |
| CA-03 | output | E | A causal hypothesis is allowed only with an explicit uncertainty label (may/might/could/hypothesis/…) **in the same clause** (or a leading `Hypothesis:` prefix) **and** cited evidence, else `UNSUPPORTED_CAUSAL_CONCLUSION`. Also covers prolong, lengthen, increase, shorten | `test_hypotheses_need_an_explicit_uncertainty_label_…`, `test_labelled_causal_hypothesis_without_evidence_is_blocked`, `test_causal_grounding_matrix` (U16, U17, HYP1–HYP4) |
| CA-04 | output | E | In problem statement, improvement, expected outcome and KPI: an intervention verb near an outcome term (span, resolution, SLA, delay, cost, …), a predictive comparative ("fewer … would … sooner"), or a change verb next to a number outside a cited, labelled simulation → `UNSUPPORTED_INTERVENTION_CLAIM`. Exemptions are scoped to one match (direct negation, a preceding "does not show that"/"test whether" without a scope break, or a non-promise covering the match); in the KPI field only a claim marker ("achieved", "will", …) makes a planned measurement a claim. No exemption for measured effects: Evidence Contract V1 cannot express one | `test_causal_grounding_matrix`, `test_no_evidence_class_can_support_an_intervention_effect`, `test_claim_rule_defects_fail_closed` |
| CA-05 | output | E | Inefficient, waste(ful), unnecessary, needless, avoidable or redundant in the same four fields, unless negated or hedged directly before, or inside a preceding inquiry/conditional ("identify which", "if", "distinguish between") → `UNSUPPORTED_INEFFICIENCY_CLAIM` | `test_causal_grounding_matrix` (INE1–INE5, U12, U13) |
| CA-06 | output | E | The expected outcome must contain a non-promise ("no reduction … is promised", "does not imply", "may not reduce") or an investigation objective ("a review may identify", "requires a controlled pilot"); a cited simulation labelled as scenario/hypothetical also qualifies → `UNQUALIFIED_EXPECTED_OUTCOME` | `test_causal_grounding_matrix` (S-cases, P1–P6, SIM1–SIM4) |
| CA-07 | validator | E | Claim types declare the evidence interpretation they need: causal attribution ("is the bottleneck/reason/cause behind", "stems from", "responsible for", "is behind", "accounts for … span") needs `causal_effect_estimate`; timing effects (purpose/condition/modal + close/resolve earlier) and quantified effects (a quantity per return/visit/handoff, or framed as added/saved/lost/extra) need `intervention_effect_measurement`. A match is blocked unless a cited finding provides that interpretation; Evidence Contract V1 provides only `observational_non_causal`, so matching numbers never help. Unknown or missing interpretations → `UNKNOWN_EVIDENCE_TYPE`. Labelled hypotheses may name a candidate cause or timing effect, never a quantified effect; a conditional ("if … is the cause") exempts attribution only. Codes `UNSUPPORTED_CAUSAL_ATTRIBUTION`, `UNSUPPORTED_TIMING_EFFECT`, `UNSUPPORTED_QUANTIFIED_EFFECT` | `test_causal_grounding_matrix` (FN1–FN4, A01–A05, Q01–Q05), `test_unknown_evidence_interpretation_fails_closed`, `test_claim_is_supported_only_by_a_known_evidence_type_that_provides_it`, `test_malformed_claim_policy_refuses_to_load` |
| EV-04 | host startup | E | After the EV-02 pin check the agent reads `GET /aggregate/v1/guardrail-policy` (Aggregate.Read; policy ID, schema version and SHA-256 only) and refuses to start on a missing route, unavailable or malformed metadata, or any ID, schema or hash mismatch | `tests/test_policy_compatibility.py` |
| AU-01 | workflow API + submit guardrail | E | Service identity → 403 at the API and BLOCK in the service | `test_service_identity_cannot_reach_workflow_http`, `test_service_identity_submission_is_blocked_by_guardrail` |
| AU-02 | submit / approve | E | Server re-runs validator and guardrail → 422, audited. Submission records policy provenance (migration 0002); approval re-runs both under the current policy with the original evidence and tool records → `REVISION_REQUIRED` (409), nothing changed | `test_savings_claim_at_submit_…`, `test_tampered_numbers_at_submit_…`, `tests/test_approval_policy_revalidation.py` |
| AU-03 | output | C+E | `requires_human_approval: Literal[True]`; validated → REQUIRE_APPROVAL, never ALLOW | `test_validated_recommendation_is_never_allow` |
| AU-04 | decision | E | Self-approval, version-hash mismatch and idempotency reuse are rejected | `test_submitter_cannot_approve_…`, existing workflow tests |
| AU-05 | execute | E | Execution only from `EXECUTING` with an `APPROVED` decision, and only if the current policy still accepts the stored recommendation (approve, retry and recovery), checked before any ticket | `test_execution_without_approved_decision_is_blocked`, `test_rejected_recommendation_creates_no_ticket`, `test_failed_execution_is_not_retried_once_the_policy_blocks`, `test_interrupted_execution_is_not_recovered_once_the_policy_blocks` |
| BU-01 | host startup | E | `ALLOW_PAID_MODEL_CALLS == "true"` required | host-startup tests |
| BU-02 | pre-model | E | ≤ 6 calls per run (policy max; configuration can only lower it); ≤ 8 per process | `test_per_run_model_call_cap_stops_the_graph`, `test_runner_limits_can_only_tighten_the_policy`, existing provider cap test |
| BU-03 | pre-model | E | Per-run input+output token budget, 40,000 (policy maximum; configuration can only lower it). Each call reserves 10,000 estimated input + 2,048 output = 12,048, and the next call is blocked (`MODEL_TOKEN_BUDGET_EXHAUSTED`) unless the reservation fits. Charges reported usage, including failed attempts. Missing usage is charged the full reservation. Requests estimated above their reservation are never sent. The host requires an explicit `MODEL_RUN_TOKEN_BUDGET` | `tests/test_token_budget.py` (23 tests) |
| BU-04 | pre-model | E | `MODEL_MAX_RETRIES` must be 0 in hosting; output cap ≤ 2048, and an unset cap is sent as 2048 rather than the SDK's unbounded default; per-call timeout 60 s | host-startup tests, `test_unset_output_cap_defaults_to_the_policy_maximum` |
| BU-05 | pre-model / pre-tool | E | Deadline ≤ 240 s, checked at every step boundary | `test_execution_deadline_blocks_before_any_model_call` |
| AD-01 | all | E | Non-ALLOW decisions are recorded in graph audit events and workflow `GUARDRAIL_DECISION` rows; values are enum, ID or hash only | `test_audit_trail_never_contains_goal_text_or_model_prose`, `test_guardrail_telemetry_keys_are_allow_listed_and_free_of_text` |

Engine-level fail-closed behaviour (all tested): an unknown checkpoint, a missing context
argument, an exception inside a rule, an empty decision set, an unknown validator code, an
incomplete validation result and a missing, malformed or out-of-range policy all resolve to
**BLOCK**. When policy loading fails, the host does not start.

## Known residual limits (stated, not hidden)

- **Deadline granularity.** The 240 s deadline is checked before each model call and each
  tool call. A call that is already running is bounded by its own timeout (model 60 s,
  aggregate API 15 s), so the worst-case wall clock is about 240 + 60 s.
- **Token estimate.** Reservations use a 3-characters-per-token estimate. In the real runs the observed input was at most 4,403 tokens per call, well below the 10,000 reservation. The estimate itself was not compared with real prompts.
- **Pattern-based text rules.** Paraphrased causal, savings, intervention or inefficiency
  claims can pass. CA-06 catches unqualified expected outcomes regardless of wording, but
  the problem statement, improvement and KPI fields have no such requirement. Against policy
  a0b4adeb…, 3 of 10 held-out unsafe paraphrases (plus one hypothesis case) reached
  REQUIRE_APPROVAL; CA-07 closes them, so that set (`causal_grounding_heldout_v1.json`) is no
  longer independent. Known false positive: "Incidents with reduced handoffs show shorter
  observed case spans" (asserted in `tests/test_causal_grounding_matrix.py`). The pre-existing CA-01 list still blocks an
  epistemic "due to" ("uncertainty remains due to …").
- **Independent evaluation (CA-07).** A set of 15 unsafe and 12 safe statements, written and
  hashed before CA-07 was implemented (`tests/data/causal_grounding_independent_v2.json`), was
  evaluated once: 9/15 unsafe blocked, 10/12 safe passed. False negatives: "are what holds up
  incident resolution", "the choke point that stretches case spans", "Group returns slow
  incidents down" (hypothesis), "Days of span eliminated by the routing change" (KPI), "it is
  the reason incidents drag on", "Returns double the time to close an incident". False
  positive: "Ask the group leads to explain …" (CA-01 "leads to"). The same author wrote the rules and
  the set, so it is not fully independent. No rule was tuned after that run.
- **Suppression scope.** Suppression is item-level within a result. No cross-artifact
  marginal totals are exposed for handoff or lifecycle rows. If a later tool returns such
  totals, that tool needs secondary suppression.
- The process-wide call cap (8) is per host process. A new sandbox session gets a fresh cap.

## Deployment dependency (EV-04)

The Hosted Agent and the Workflow API ship the same `process_optimizer.guardrails` package
(`COPY src` in both images) and must be built from the same commit. Deployed today: agent
v5 and Workflow API `--0000002`, both on policy `c2d0bbbe…` (schema 1.0.0).

| Combination | Behaviour |
|---|---|
| New agent, old API (no route) | Agent refuses to start: `GUARDRAIL_POLICY_METADATA_MISSING` |
| New agent, API on another policy | Agent refuses: schema/version/ID mismatch codes |
| Old agent (v5), new API | Agent starts (it has no EV-04 check); every submission is re-validated under the new policy, so non-compliant output is rejected (422) |
| Matching | Agent starts |

Staged rollout (each step separately approved):

1. Build both images from one reviewed commit; record both digests and the policy hash.
2. Deploy the Workflow API revision first. Agent v5 stays disabled and needs no change; the
   API's submit path now enforces the new policy. Smoke test: `/health`, the evidence-contract
   route and `/aggregate/v1/guardrail-policy` (expect the recorded hash).
3. Create agent v6 from the new image with the unchanged EV-02 pin, keep it disabled, route
   FixedRatio v6 = 100 % while disabled. On the first enable, the host either starts (hashes
   match) or refuses before serving any request.
4. Rollback order is the reverse: route the agent back to v5 first, then roll the API back to
   `--0000002` if needed. Rolling back only the API while v6 is routed makes v6 refuse to start
   (fail closed, no incorrect output).

Stored approvals are re-checked under the current policy at approval, retry and recovery
(migration 0002 records each submission's policy provenance); a refusal returns
`REVISION_REQUIRED` and changes nothing. Executed records are never changed. See
`docs/approval_policy_revalidation.md` for the migration prerequisite, the local SQLite
auto-migration and the rollback constraints.
