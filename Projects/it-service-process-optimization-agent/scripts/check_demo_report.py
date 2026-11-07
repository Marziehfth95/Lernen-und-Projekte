"""CI gate for local_e2e_demo.py reports: fail on any unreviewed behaviour change.

    python scripts/check_demo_report.py --expected tests/data/synthetic_ci_aggregate_fixture.json \
        <report-sqlite.json> <report-postgres.json>

Each report must contain exactly the reviewed 25 checks, all passed, recommendations labelled
DETERMINISTIC_MOCK_NOT_REAL_AI, the known causal-claim gap still reported as OPEN_NOT_SAFE
and rejected by a human, and the reviewed result digest. Closing the gap or changing any
observed status, code, state or count changes the digest, so this gate fails until the new
behaviour is reviewed and the expected values are updated deliberately.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_CHECKS = (
    "health",
    "aggregate_requires_authentication",
    "admin_lacks_aggregate_read",
    "EV-02_evidence_contract_pin",
    "EV-04_policy_compatibility",
    "aggregate_tool_cohort_comparison",
    "investigation_group_returns",
    "investigation_completed_lifecycle",
    "guardrail_blocks_causal_claim",
    "guardrail_blocks_unsupported_effect_claim",
    "validator_blocks_unknown_evidence",
    "service_identity_cannot_submit",
    "analyst_submits",
    "idempotent_resubmission",
    "analyst_without_approve_permission_blocked",
    "self_approval_blocked_for_dual_role_user",
    "admin_cannot_approve",
    "service_identity_cannot_approve",
    "owner_approves_and_executes",
    "exactly_once_ticket",
    "rejection_creates_no_ticket",
    "revision_case_submitted_under_current_policy",
    "REVISION_REQUIRED_on_policy_upgrade",
    "owner_requests_revision",
    "audit_trail",
)


def problems(report: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    found: list[str] = []
    names = [step["step"] for step in report.get("steps", [])]
    if tuple(names) != REQUIRED_CHECKS:
        found.append(f"check list differs from the reviewed 25: {names}")
    failed = [step["step"] for step in report.get("steps", []) if not step.get("passed")]
    if failed or report.get("failures") or report.get("passed") != len(REQUIRED_CHECKS):
        found.append(f"failed checks: {failed or report.get('failures')}")
    labels = {
        step.get("execution_label")
        for step in report.get("steps", [])
        if step["step"].startswith("investigation_")
    }
    if labels != {"DETERMINISTIC_MOCK_NOT_REAL_AI"}:
        found.append(f"recommendations not labelled as deterministic mock: {labels}")
    gaps = report.get("known_gaps", [])
    gap_ok = (
        len(gaps) == 1
        and gaps[0].get("status") == "OPEN_NOT_SAFE"
        and gaps[0].get("reached_human_review") is True
        and gaps[0].get("human_decision") == "REJECTED"
    )
    if not gap_ok:
        found.append(f"known causal-claim gap changed; review before updating: {gaps}")
    if report.get("backend") == "postgres":
        refused = report.get("postgres_runtime_privileges_refused", {})
        if len(refused) != 7 or not all(refused.values()):
            found.append(f"runtime role was not refused every write/DDL probe: {refused}")
    if report.get("result_digest") != expected["expected_demo_result_digest"]:
        found.append(
            f"result digest {report.get('result_digest')} != reviewed "
            f"{expected['expected_demo_result_digest']}"
        )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args(argv)
    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    status = 0
    for path in args.reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        issues = problems(report, expected)
        backend = report.get("backend", "?")
        if issues:
            status = 1
            for issue in issues:
                print(f"[FAIL] {backend}: {issue}", file=sys.stderr)
        else:
            gap = report["known_gaps"][0]
            print(
                f"[PASS] {backend}: 25/25 checks, mock-labelled, digest reviewed; "
                f"KNOWN GAP still open ({gap['status']}, human decision {gap['human_decision']})"
            )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
