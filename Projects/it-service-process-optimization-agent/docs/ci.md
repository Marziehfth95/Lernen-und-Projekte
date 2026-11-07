# Continuous integration (offline)

[`ci/github-actions-ci.yml`](../ci/github-actions-ci.yml) is a GitHub Actions workflow written for
a repository whose root is this project. In this portfolio monorepo it is kept as a reference and
is **not active** (GitHub only runs workflows under the repository root's `.github/workflows/`).
Copied to `.github/workflows/ci.yml` of a standalone repository, it runs on every pull request
and on pushes to `main`. It is offline:

- no repository secret and no Azure credential;
- a read-only `GITHUB_TOKEN` (`contents: read`; checkout does not persist it);
- no OIDC token and no deployment permission;
- no artifact upload;
- no model call (`ALLOW_PAID_MODEL_CALLS=false`, and the one paid test is deselected).

The only network use is checking out the code and downloading the hash-locked packages from PyPI.

## Steps

| Step | What it proves |
|---|---|
| Install from `deploy/pip-bootstrap.lock`, `ci/build-requirements.lock` and `ci/requirements.lock` with `--require-hashes`, then the project with `--no-deps` | Exact, hash-verified dependencies. Runtime pins equal the agent and workflow image locks |
| Ruff on `src`, `tests` and the CI and demo scripts | Lint |
| Generate the synthetic aggregate package and `--check` it | The fixture is reproducible and equals the reviewed values |
| Validator suite and mocked agent evaluation on the synthetic package | 25/25 and 2/2, with the model-free and mock labels asserted |
| Disposable PostgreSQL cluster (the runner's preinstalled binaries, `127.0.0.1:55434`, no Unix socket) | PostgreSQL tests really run |
| Guardrail and approval regression tests | Policy, causal grounding, policy compatibility and revalidation tests run as their own step |
| Full offline suite, `-m "not model"` | Fails if any test is skipped, so PostgreSQL and SDK paths can't be skipped silently |
| Local end-to-end demo on SQLite and PostgreSQL (its own temporary cluster on `127.0.0.1:55433`) | The same 25 checks as locally, against the synthetic package and its reviewed pin |
| `scripts/check_demo_report.py` | Regression gate (below) |

## Synthetic aggregate package

`scripts/generate_synthetic_aggregate_package.py` builds the package in the run. Nothing derived from
real data is committed.

- **Inputs:** 600 invented cases from a seeded generator. Groups, products and organisations
  are `SYNTH-*`, and the activity and lifecycle vocabulary is the one the analysis already expects.
  No BPI Challenge record, original ticket or protected database is read.
- **Build:** the real `run_process_mining`, aggregate manifest and deployable-package builder. So
  the schema, manifest verification, served-contract filtering and EV-02/EV-04 checks are the
  production ones.
- **Provenance:** every finding's dataset says `SYNTHETIC CI FIXTURE - NOT BPI CHALLENGE 2013 - NOT
  EVIDENCE` with source URL `https://example.invalid/synthetic-ci-fixture`. The generator refuses to
  finish otherwise, and refuses to write under `data/` or `deploy/aggregate-package/`.
- **Small-group suppression:** one rare group (`SYNTH-RARE`) is used by only four cases. Of
  8 findings, 5 are suppressed from the served contract, so the demo runs against the suppressed view.
- **Reviewed values** live in
  [`tests/data/synthetic_ci_aggregate_fixture.json`](../tests/data/synthetic_ci_aggregate_fixture.json):
  - every artifact hash and the served-contract pin (an independent EV-02 pin, never derived
    from the package under test);
  - the finding counts;
  - the expected demo result digest.

Parquet files embed the writer version, so these values belong to `ci/requirements.lock`. They were
recorded in a linux/amd64 container with that lock and are identical on macOS with the same pins.

## Regression gate

The run fails unless each demo report has:

- exactly the reviewed 25 checks, all passed;
- both investigations labelled `DETERMINISTIC_MOCK_NOT_REAL_AI`;
- the known causal-claim gap still `OPEN_NOT_SAFE`, having reached human review and been rejected;
- on PostgreSQL, all 7 runtime-role write and DDL probes refused;
- the reviewed result digest.

The digest covers every observed status, code, state and count. Any behaviour change therefore fails
CI until someone reviews it and updates the expected values deliberately. That includes a weakened
guardrail, a changed error code, or the causal gap being closed.

## Updating the reviewed values

Only after reviewing the behaviour change:

```bash
.venv/bin/python -I scripts/generate_synthetic_aggregate_package.py \
  --output-dir /tmp/synthetic-aggregates --write-expected /tmp/fixture.json
.venv/bin/python -I scripts/local_e2e_demo.py --analysis-dir /tmp/synthetic-aggregates \
  --expected-evidence-sha256 "$(jq -r .served_evidence_sha256 /tmp/fixture.json)" --report /tmp/r.json
```

Then copy the generator values and `result_digest` into the fixture file, and explain the change in
the commit. If `ci/requirements.lock` changes pandas, pyarrow or numpy, regenerate on linux/amd64.

## Running the CI steps locally

```bash
.venv/bin/python -I scripts/generate_synthetic_aggregate_package.py \
  --output-dir /tmp/synthetic-aggregates --check tests/data/synthetic_ci_aggregate_fixture.json
PO_DEMO_REPORT_DIR=/tmp/demo-reports scripts/run_local_e2e_demo.sh both \
  --analysis-dir /tmp/synthetic-aggregates \
  --expected-evidence-sha256 "$(jq -r .served_evidence_sha256 tests/data/synthetic_ci_aggregate_fixture.json)" \
  --expected-digest "$(jq -r .expected_demo_result_digest tests/data/synthetic_ci_aggregate_fixture.json)"
.venv/bin/python -I scripts/check_demo_report.py --expected tests/data/synthetic_ci_aggregate_fixture.json \
  /tmp/demo-reports/report-sqlite.json /tmp/demo-reports/report-postgres.json
```

## Not covered by CI

- Real model behaviour.
- Entra token validation against a live tenant.
- Azure deployment.
- The real aggregate package. Its local demo digest is `4793b9fb…`; see
  [local_e2e_demo.md](local_e2e_demo.md).
