# Local end-to-end demo (API, workflow, guardrails; SQLite and PostgreSQL)

This demo exercises the real FastAPI app, workflow service, validator, guardrails and
migrations end to end against a **disposable** database. It runs entirely on the laptop. Nothing
in it can reach Azure, a model, a ticketing system or a notification channel. It complements the
browser walkthrough in [interactive_demo.md](interactive_demo.md).

## Command

```bash
scripts/run_local_e2e_demo.sh            # both backends
scripts/run_local_e2e_demo.sh sqlite
scripts/run_local_e2e_demo.sh postgres
```

Exit code 0 means every check passed on every backend that ran, 1 means a check failed, and 2
means a precondition is missing. Add `--report <file>` to a direct
`.venv/bin/python -I scripts/local_e2e_demo.py` call to keep the JSON report. Writing a report
under `data/` is refused. With the wrapper, set `PO_DEMO_REPORT_DIR` instead. Any further
options are passed to the driver for every backend:

- `--analysis-dir` selects another verified package.
- `--expected-evidence-sha256` sets that package's independently recorded pin.
- `--expected-digest` fails the run unless the result digest equals a reviewed value.

CI uses these with a synthetic package; see [ci.md](ci.md).

Prerequisites:

- The project virtual environment in `.venv` (`pip install -e '.[dev]'`).
- An aggregate package. The default is the synthetic public demo package in
  `demo-data/public-synthetic-aggregates/`, built by `scripts/prepare_public_demo_data.sh`
  (git-ignored; seeded, invented records processed by the production pipeline). A package
  built from your own licensed copy of the dataset needs `--analysis-dir` and its own
  `--expected-evidence-sha256`; verify it with
  `process-optimizer-package-aggregates --output-dir <package> --verify-only`.
- For PostgreSQL: the `cloud` extra (psycopg; `pip install -e '.[dev,cloud]'`) and `initdb` and
  `pg_ctl` on `PATH` (tested with PostgreSQL 18). The script creates a
  temporary cluster on `127.0.0.1:55433` (override with `PO_DEMO_PG_PORT`). It has no Unix
  socket and trust authentication on loopback only, and it is stopped and deleted on exit,
  including after a failure or Ctrl+C.

Expected end of a successful run:

```
[KNOWN GAP] unsupported 'cause' claim reached human review: http=201; process owner decision=REJECTED (not counted as a pass)
25/25 checks passed on sqlite; known gaps: 1 (open, not counted); result digest ed4b8f0eb26fc8ee
[CLEANUP] removed temporary directory
...
[INFO] runtime role refused 7/7 write/DDL probes on audit, provenance and tickets
25/25 checks passed on postgres; known gaps: 1 (open, not counted); result digest ed4b8f0eb26fc8ee
[CLEANUP] removed temporary directory and database
[CLEANUP] stopped and deleted the temporary PostgreSQL cluster
```

The result digest hashes every observed value (HTTP status, codes, states, counts). It holds no
IDs or timestamps, so it is identical across runs and across both backends for the same commit
and aggregate package. A changed digest means a behaviour change. The digest above belongs to
the synthetic public package (reviewed value in `tests/data/synthetic_ci_aggregate_fixture.json`).

## Isolation

- **Store:** a new SQLite file in a fresh temporary directory, or a new database in the temporary
  cluster. The driver refuses an existing file, any path under `data/`, and any PostgreSQL host
  other than `127.0.0.1`. The protected `data/local/*.sqlite3` databases are never opened.
- **Identities:** five synthetic local bearer tokens (analyst, process owner, admin, a user with
  both roles, and a service identity that mirrors the hosted agent). They are valid only inside
  this process.
- **Data:** the approved aggregate-only package. The API verifies its manifest, and check 4 verifies
  the evidence-contract pin.
- **No outbound traffic:** Python-level connections to anything other than loopback are refused
  for the whole run. Foundry, Azure and Application Insights environment variables are removed,
  and `ALLOW_PAID_MODEL_CALLS=false` is set.
- **Local adapters only:** the model is `DeterministicMockProvider`, tickets go to the local
  executor, and notifications go to `LocalDemoNotificationAdapter`.
- **Cleanup:** the temporary directory, the PostgreSQL database, both demo roles and the cluster
  are removed on success and on failure.

## What is real, locally tested or mocked

| Component | In this demo | Verified where |
|---|---|---|
| Aggregate package and evidence contract (default: synthetic public package, run `run_60efe2fdce50ecd27b41f404`, 3 of 8 findings served) | Real code over synthetic data, read-only | Locally |
| FastAPI routes, RBAC, workflow service, migrations 0001–0002 | Real code, in-process ASGI | Locally (this demo and the test suite) |
| Authentication | Local opaque tokens | Entra JWT validation is unit-tested; it was also observed returning 401/403 in an earlier Azure development deployment, which is not part of this repository's demo |
| EV-02 evidence pin and EV-04 policy compatibility | Real functions | Locally (the startup checks also ran in an earlier Azure development deployment; model inference there was never proven) |
| Recommendation reasoning | `DeterministicMockProvider`, labelled `DETERMINISTIC_MOCK_NOT_REAL_AI` | **Mocked.** No model is called |
| Validator and guardrail policy 2.0.0 (`5a886139…`) | Real | Locally; one known gap below |
| Policy upgrade for `REVISION_REQUIRED` | Packaged policy plus one extra pattern, written to the temporary directory | **Simulated**, never packaged |
| Ticket executor and notifications | Local executor and local adapter | Local only, no external system |
| PostgreSQL least privilege (separate migration and runtime roles) | Real, disposable cluster | Locally. Azure PostgreSQL is not provisioned |

## The 25 checks

| # | Check | Expected |
|---|---|---|
| 1 | Health | 200 |
| 2 | Aggregate without a token | 401 |
| 3 | Admin on aggregates | 403 `INSUFFICIENT_PERMISSION` |
| 4 | EV-02: evidence contract read by the service identity | 200, served findings returned, pin matches (synthetic package: 3 findings, `15f30d73…`) |
| 5 | EV-04: guardrail policy identity | 200, schema 2.0.0, `5a886139…`, compatible |
| 6 | Aggregate tool `cohort-comparison` | 200, same analysis run |
| 7–8 | Mock investigations `group_returns` and `completed_lifecycle` | `VALIDATED_RECOMMENDATIONS`, validator 1/1, with 3 and 2 tool calls |
| 9 | Causal claim with "lead to" | 422 `GUARDRAIL_BLOCKED` |
| 10 | Effect claim "will reduce … by 30 percent" | 422 `GUARDRAIL_BLOCKED` |
| — | **Known gap:** "cause longer case spans and delay closure" | 201: it reaches human review, the owner rejects it, and it is **not** counted |
| 11 | Unknown evidence ID | 422 `RECOMMENDATION_NOT_VALIDATED` |
| 12 | Service identity submits | 403 `HUMAN_IDENTITY_REQUIRED` |
| 13–14 | Analyst submits, then resubmits the identical request | 201 `PENDING_APPROVAL`, then the same approval ID |
| 15 | Analyst approves | 403 `INSUFFICIENT_ROLE` |
| 16 | User with both roles approves their own request | 403 `SELF_APPROVAL_PROHIBITED` |
| 17–18 | Admin or service identity approves | 403 `INSUFFICIENT_ROLE` / `HUMAN_IDENTITY_REQUIRED` |
| 19 | Process owner approves (revalidated under the current policy) | 200 `EXECUTED` |
| 20 | Same decision replayed, then a new decision key | 200 with the same state, then 409 `INVALID_STATE_TRANSITION`; exactly **1 ticket** |
| 21 | Rejection | 200 `REJECTED`; still 1 ticket |
| 22–23 | Pending approval meets the stricter policy | 409 `REVISION_REQUIRED` with reason codes; no text echoed; stays `PENDING_APPROVAL`; 1 ticket |
| 24 | Owner requests a revision | 200 `REVISION_REQUESTED` |
| 25 | Audit trail and notifications | Admin 200, analyst 403; 45 events including `APPROVAL_REVALIDATION_BLOCKED` (codes only); 12 local notifications, 0 failed |

The PostgreSQL run also checks, outside the 25, that the runtime role is refused all of the
following:

- UPDATE and DELETE on `audit_events`;
- UPDATE and DELETE on `approval_policy_provenance`;
- DELETE on `improvement_actions`;
- DROP TABLE and CREATE TABLE.

## Known limitation: causal-claim detection (open)

Guardrail policy 2.0.0 blocks assertive causal wording only through the patterns `leads to`,
`drives`, `due to`, `will reduce/shorten/eliminate/improve/cut/prevent`, `eliminates` and
`guarantees`. It does **not** recognise the plain verb "cause(s)" or agentive effect verbs such
as "delay closure". So a recommendation whose problem statement says *"Repeated group returns
cause longer case spans and delay closure."* passes validation and reaches `PENDING_APPROVAL`.

The demo shows this deliberately and reports it as `OPEN_NOT_SAFE`; it never counts it as a
pass. The only control is the human approver, and in the demo the process owner rejects it. This
is the same class as the false negatives of the independent v2 evaluation set and the open P1
(agentive link) proposal. The planned fix is a held-out evaluation set first, then a policy rule;
it is not part of this demo.

## Other limitations

- No model runs in this demo. Real local models are exercised only in the interactive demo
  ([interactive_demo_real_local_ai.md](interactive_demo_real_local_ai.md)).
- The policy upgrade is simulated with a temporary derived policy.
- Concurrency is not exercised here. The test suite covers it
  (`test_concurrent_approvals_and_retries_create_one_ticket`,
  `test_postgres_policy_change_and_races_keep_one_outcome`).
- The network guard covers Python sockets. libpq connections are limited to `127.0.0.1` by the
  URL check.
