#!/usr/bin/env bash
# Build the SYNTHETIC public demo aggregate package used by the interactive demo and the
# local end-to-end demo by default:
#
#   scripts/prepare_public_demo_data.sh
#
# The package is produced by the production pipeline (process mining, evidence contract,
# aggregate manifest, deployable-package builder) from invented, seeded event records. No BPI
# Challenge record and no private file is read. Its provenance is labelled
# "SYNTHETIC CI FIXTURE - NOT BPI CHALLENGE 2013 - NOT EVIDENCE".
#
# The result is compared with the reviewed values in tests/data/synthetic_ci_aggregate_fixture.json
# (artifact hashes, finding counts, served-evidence pin). Any difference fails the script.
# The output directory demo-data/public-synthetic-aggregates/ is git-ignored and is replaced
# on every run.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
OUT="$REPO/demo-data/public-synthetic-aggregates"
FIXTURE="$REPO/tests/data/synthetic_ci_aggregate_fixture.json"

mkdir -p "$REPO/demo-data"
rm -rf "$OUT"
"$PYTHON" -I "$REPO/scripts/generate_synthetic_aggregate_package.py" \
  --output-dir "$OUT" --check "$FIXTURE"
echo "[READY] synthetic public demo package: $OUT"
