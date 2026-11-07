# Interactive local demo (browser UI on the real backend)

A locally runnable web application over the real FastAPI routers, workflow service, validator,
guardrails, simulation engine and local ticket executor. The browser only renders server
responses. It computes no metric, and it shows a success only after the backend confirms it.
A strip at the bottom lists every server call with the acting role and the HTTP status.

## Start

```bash
scripts/prepare_public_demo_data.sh        # once: build the synthetic public demo package
scripts/run_interactive_demo.sh            # then open http://127.0.0.1:8765/app/
scripts/run_interactive_demo.sh --port 9000
```

Prerequisites: the project `.venv` and an aggregate package. By default the demo reads the
**synthetic** public demo package in `demo-data/public-synthetic-aggregates/` (git-ignored),
which `scripts/prepare_public_demo_data.sh` builds with the production pipeline from seeded,
invented event records. Its provenance reads `SYNTHETIC CI FIXTURE - NOT BPI CHALLENGE 2013 -
NOT EVIDENCE`, and the UI shows a `SYNTHETIC DATA · NOT BPI 2013` badge.

A package built from your own licensed copy of the BPI Challenge 2013 log (see
[ingestion_pipeline.md](ingestion_pipeline.md)) can be served with
`--analysis-dir <package> --expected-evidence-sha256 <independently recorded pin>`.

Stop with Ctrl+C. The disposable database is deleted on exit.

## Isolation and fail-closed startup

| Control | Implementation |
|---|---|
| Local only | Refuses to start unless `APP_ENV=local` and no cloud-hosting or credential marker is set (`CONTAINER_APP_NAME`, `WEBSITE_SITE_NAME`, `IDENTITY_ENDPOINT`, `MSI_ENDPOINT`, `KUBERNETES_SERVICE_HOST`, `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_CLIENT_ID`, `APPLICATIONINSIGHTS_CONNECTION_STRING`, …). `/demo/v2/session` re-checks this on every call |
| Separate app | `process_optimizer.demo.interactive`. `process_optimizer.main` (production) is not changed and does not import it |
| Pins | Aggregate manifest verified; served evidence contract must equal the expected pin (default: the synthetic public package, `15f30d73…`); policy must be `2.0.0` / `5a886139…`. Any mismatch aborts startup |
| Database | New temporary SQLite file per launch, never under `data/`; deleted on exit |
| Network | Binds to `127.0.0.1`; `TrustedHostMiddleware` accepts only `127.0.0.1`/`localhost`; Python-level connections to anything but loopback are refused; Foundry/Azure/App Insights/OTLP variables removed; `ALLOW_PAID_MODEL_CALLS=false` |
| Identities | Four synthetic local principals (Analyst, Process Owner, Admin, Analyst + Process Owner) with random tokens per launch, served only by the local session endpoint |
| Browser | Strict CSP (`default-src 'self'`, no inline script), no CDN, no external fonts, `no-store` |
| Adapters | `DeterministicMockProvider` (`DETERMINISTIC_MOCK_NOT_REAL_AI`), `LocalImprovementExecutor`, `LocalDemoNotificationAdapter` |

## What the UI calls

| UI section | Endpoint (existing unless marked new) |
|---|---|
| Evidence | `GET /aggregate/v1/process-overview`, `POST /aggregate/v1/{cohort-comparison, bottleneck-findings, group-handoffs, lifecycle-patterns}`, new `GET /demo/v2/evidence/provenance` |
| Investigate | new `POST /demo/v2/investigations` (real `InvestigationRunner` + mock provider + read-only guardrail preflight), then `POST /workflow/recommendations/submit` |
| What-if | `POST /aggregate/v1/simulate-group-transition-reduction` |
| Approvals | new `GET /demo/v2/approvals`, new `GET /demo/v2/approvals/{id}/recommendation`, `POST /workflow/approvals/{id}/{approve,reject,request-revision}` |
| Tickets | `GET /workflow/tickets` |
| Audit | new `GET /demo/v2/audit/latest` (newest first, same authorization as `/workflow/audit`), new `POST /demo/v2/notifications/deliver` (local adapter) |
| REVISION_REQUIRED | new `POST /demo/v2/policy/simulate-stricter` and `/demo/v2/policy/restore` (Admin; same derivation as check 22 of the 25-check demo, labelled SIMULATED, never packaged) |

The new `/demo/v2/*` routes reuse the existing `_authorize` role checks and the `WorkflowService`
scoping. They add no business logic.

## What is real, what is mocked, what is missing

- **Real:** aggregate tools and suppression, evidence pins, LangGraph graph and tool calls,
  validator, guardrail engine (input, output and submission checkpoints), what-if engine,
  RBAC, separation of duties, idempotency, revalidation before execution, ticket creation in
  the disposable database, audit and local notification outbox.
- **Mocked:** the analyst, investigator and optimizer reasoning steps (deterministic mock
  provider). The scenario selects the output; the goal text is only screened by the input
  guardrail.
- **Simulated:** the stricter policy used to demonstrate `REVISION_REQUIRED`.
- **Not available, so not shown:** process variants (no variant artifact in the aggregate
  package). Records and findings that fall below the small-group threshold are withheld by
  the server, and the page shows that they were withheld rather than working around it.
  With the synthetic public package, 5 of 8 findings are withheld and no
  high-duration transition finding exists; the "Bottleneck candidates" card explains that no
  transition met the rule instead of showing an empty table.
  (With the original BPI 2013 package, the whole `process-overview` record was withheld
  instead, because it contains counts below the threshold.)
- **Not implemented, so not offered in what-if:** elapsed-time and SLA impact (the engine
  reports `NOT_ESTIMABLE`), and any intervention other than reducing repeated group handoffs.
- **Known gap `OPEN_NOT_SAFE`:** a plain "cause" claim passes policy 2.0.0 and reaches human
  review. The UI labels it, and the walkthrough below shows the owner rejecting it.

## Walkthrough

1. **Analyst · 1 Evidence:** headline figures, the group-return cohort comparison, handoffs,
   lifecycle patterns and the provenance card with the withheld-findings count.
2. **Analyst · 2 Investigate:** *Repeated group returns*, wording *Unsafe: causal claim* →
   Run → Submit. The guardrail blocks it, explains why and stores nothing.
3. **Analyst:** wording *As generated* → Run → Submit → *Resubmit identical request*. The
   recommendation waits for human approval; the replay returns the same approval request.
4. **Analyst · 3 What-if:** select a handoff finding and run the simulation. Elapsed-time
   and SLA effects are reported as `NOT_ESTIMABLE`.
5. **4 Approvals:** try *Approve* as Analyst (403), then approve as Process Owner. The policy
   is re-checked, exactly one local ticket is created, a replay changes nothing, and a
   second approval with a new key is refused (409).
6. **Known gap:** as Analyst submit wording *KNOWN GAP*; as Process Owner reject it. The
   ticket count does not change.
7. **Admin · 6 Audit:** the audit trail records codes, never recommendation text. The Admin
   cannot read process evidence or approve.

Optional: as **Analyst + Process Owner**, submit and try to approve your own request →
`SELF_APPROVAL_PROHIBITED`. As **Admin**, *Activate simulated stricter policy* for a pending
request; as **Process Owner**, approve → `409 REVISION_REQUIRED`, no ticket; then *Request
revision* and *Restore pinned policy*.

Restart the app for a clean database.

## Verification

Public synthetic package (2026-11-06, this repository):

- Full offline suite with a disposable PostgreSQL 18: 796 passed, 0 skipped, 1 deselected
  (opt-in paid-model test).
- `scripts/run_local_e2e_demo.sh both`: 25/25 on SQLite and PostgreSQL; result digest equals
  the reviewed value in `tests/data/synthetic_ci_aggregate_fixture.json`.
- HTTP walkthrough of the running app (evidence, both investigations, what-if, guardrail
  variants, submit, RBAC, self-approval, idempotent approval, rejection, policy
  revalidation, audit): all checks passed; the page rendered without console errors.

Historical (2026-11-02, original BPI 2013 package, not reproducible from this repository):
a headless-Chrome walkthrough of the same UI passed 28/28 checks, and the 25-check demo
passed on SQLite and PostgreSQL.
