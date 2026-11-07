"""Policy-version-aware approval and execution (offline, SQLite and PostgreSQL).

A recommendation is re-validated under the *current* policy at approval and at every
execution path. A stored approval created under an older or unknown policy can therefore
never be approved or executed once the current policy blocks it. "Old" and "new" policies
are the packaged policy loaded with one deliberate change.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from tests.test_postgres_migrations import (  # noqa: F401 - pytest fixture re-export
    _migrate,
    postgres,
    requires_postgres,
)
from tests.test_workflow import (  # noqa: F401 - pytest fixture re-export
    ANALYST,
    OWNER,
    decision,
    evidence,
    submission,
)

from process_optimizer.guardrails import GuardrailEngine, evidence_contract_sha256, load_policy
from process_optimizer.guardrails.compatibility import policy_metadata
from process_optimizer.main import app
from process_optimizer.persistence import Database
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.workflow.service import (
    RevisionRequiredError,
    SelfApprovalError,
    WorkflowConflictError,
    WorkflowService,
)
from process_optimizer.workflow.state import ApprovalState, InvalidTransitionError

POLICY_FILE = Path(load_policy.__code__.co_filename).with_name("policy.v1.json")
TEXT = {
    "problem": "Observed aggregate lifecycle pattern",
    "improvement": "Review the aggregate lifecycle pattern with the owner",
}


def _policy(tmp_path: Path, name: str, change: Any) -> GuardrailEngine:
    raw = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    change(raw)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return GuardrailEngine(load_policy(path))


@pytest.fixture
def blocking(tmp_path: Path) -> GuardrailEngine:
    """A newer policy whose output rule now blocks the fixture recommendation."""
    return _policy(
        tmp_path, "blocking",
        lambda raw: raw["output"]["assertive_causal_patterns"].append(
            r"\bReview the aggregate lifecycle\b"
        ),
    )


@pytest.fixture
def passing(tmp_path: Path) -> GuardrailEngine:
    """A newer policy (different hash) that still accepts the fixture recommendation."""
    return _policy(tmp_path, "passing", lambda raw: raw.update(small_group_threshold=11))


class CountingExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def create_ticket(self, ticket: Any, idempotency_key: str) -> dict[str, Any]:
        self.calls.append(idempotency_key)
        if self.fail:
            raise RuntimeError("adapter unavailable")
        return {"adapter": "TEST", "ticket_id": ticket.ticket_id}


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'workflow.sqlite3'}"


def _service(url: str, contract: EvidenceContractV1, guardrails: GuardrailEngine | None = None,
             executor: Any = None) -> WorkflowService:
    return WorkflowService(Database(url), contract, guardrails=guardrails, executor=executor)


def _rows(url: str, *, skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    path = url.removeprefix("sqlite:///")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )]
        return {name: sorted(map(repr, connection.execute(f'SELECT * FROM "{name}"')))
                for name in sorted(names) if name not in skip}
    finally:
        connection.close()


def _sql(url: str, statement: str) -> None:
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(statement))
    engine.dispose()


def _audit(service: WorkflowService, action: str) -> list[dict[str, Any]]:
    return [event.details for event in service.list_audit(1000) if event.action_type == action]


# ------------------------------------------------------------------- provenance


def test_submission_records_the_policy_evidence_and_tool_records(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    url = _url(tmp_path)
    service = _service(url, evidence)
    pending = service.submit(submission(), ANALYST)

    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM approval_policy_provenance")).mappings().one()
    engine.dispose()
    identity = policy_metadata(service.guardrails.policy)
    assert row["approval_request_id"] == pending.approval_request_id
    assert (row["policy_id"], row["policy_schema_version"], row["policy_version"]) == (
        identity.policy_id, identity.policy_schema_version, identity.policy_version
    )
    assert row["policy_schema_version"] == "2.0.0"
    assert row["evidence_contract_sha256"] == evidence_contract_sha256(evidence)
    assert row["analysis_run_id"] == evidence.analysis_run_id
    assert json.loads(row["tool_call_records_json"]) == []


def test_current_policy_approval_executes_exactly_once(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    executor = CountingExecutor()
    service = _service(_url(tmp_path), evidence, executor=executor)
    pending = service.submit(submission(), ANALYST)

    approved = service.decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )
    replay = service.decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )

    assert approved.state == replay.state == ApprovalState.EXECUTED
    assert len(executor.calls) == 1 and len(service.list_tickets()) == 1


# ------------------------------------------------- older policy at approval time


def test_pending_approval_from_an_older_policy_that_now_blocks_requires_revision(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    url = _url(tmp_path)
    pending = _service(url, evidence).submit(submission(), ANALYST)
    executor = CountingExecutor()
    newer = _service(url, evidence, guardrails=blocking, executor=executor)
    before = _rows(url, skip=("audit_events",))

    for attempt in ("approve-1", "approve-1", "approve-2"):  # replays and a new key
        with pytest.raises(RevisionRequiredError) as raised:
            newer.decide(pending.approval_request_id, ApprovalState.APPROVED,
                         decision(pending, key=attempt), OWNER)
        assert raised.value.code == "REVISION_REQUIRED"
        assert "UNSUPPORTED_CAUSAL_STATEMENT" in raised.value.reason_codes
        assert raised.value.provenance == "DIFFERENT_POLICY"

    assert _rows(url, skip=("audit_events",)) == before  # nothing approved or mutated
    assert newer.get_approval(pending.approval_request_id).state == ApprovalState.PENDING_APPROVAL
    assert executor.calls == [] and newer.list_tickets() == ()
    blocked = _audit(newer, "APPROVAL_REVALIDATION_BLOCKED")
    assert len(blocked) == 3
    assert blocked[0]["policy_provenance"] == "DIFFERENT_POLICY"
    assert blocked[0]["guardrail_policy_version"] == blocking.policy.version

    # A human can still reject or ask for a revision; neither executes anything.
    revised = newer.decide(pending.approval_request_id, ApprovalState.REVISION_REQUESTED,
                           decision(pending, key="revise"), OWNER)
    assert revised.state == ApprovalState.REVISION_REQUESTED and executor.calls == []


def test_older_approval_that_still_passes_is_approved(
    tmp_path: Path, evidence: EvidenceContractV1, passing: GuardrailEngine  # noqa: F811
) -> None:
    url = _url(tmp_path)
    pending = _service(url, evidence).submit(submission(), ANALYST)
    executor = CountingExecutor()

    approved = _service(url, evidence, guardrails=passing, executor=executor).decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )

    assert approved.state == ApprovalState.EXECUTED and len(executor.calls) == 1


# ------------------------------------------- missing, malformed, unknown provenance


def test_missing_provenance_is_revalidated_not_trusted(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    accepted, refused = (f"sqlite:///{tmp_path / name}" for name in ("a.sqlite3", "b.sqlite3"))
    first = _service(accepted, evidence).submit(submission(), ANALYST)
    second = _service(refused, evidence).submit(submission(), ANALYST)
    for url in (accepted, refused):  # as created before migration 0002
        _sql(url, "DELETE FROM approval_policy_provenance")

    approved = _service(accepted, evidence).decide(
        first.approval_request_id, ApprovalState.APPROVED, decision(first), OWNER
    )
    assert approved.state == ApprovalState.EXECUTED
    with pytest.raises(RevisionRequiredError) as raised:
        _service(refused, evidence, guardrails=blocking).decide(
            second.approval_request_id, ApprovalState.APPROVED, decision(second), OWNER
        )
    assert raised.value.provenance == "MISSING"


@pytest.mark.parametrize(
    "tamper",
    [
        "policy_version = 'not-a-hash'",
        "policy_schema_version = 'two'",
        "policy_id = 'Bad Id'",
        "evidence_contract_sha256 = 'abc'",
        "tool_call_records_json = '{not json'",
        "tool_call_records_json = '[{\"tool_name\": \"x\"}]'",
    ],
)
def test_malformed_provenance_blocks(
    tmp_path: Path, evidence: EvidenceContractV1, tamper: str  # noqa: F811
) -> None:
    url = _url(tmp_path)
    service = _service(url, evidence, executor=(executor := CountingExecutor()))
    pending = service.submit(submission(), ANALYST)
    _sql(url, f"UPDATE approval_policy_provenance SET {tamper}")

    with pytest.raises(RevisionRequiredError) as raised:
        service.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending),
                       OWNER)

    assert raised.value.reason_codes == ("ORIGINAL_EVIDENCE_UNAVAILABLE",)
    assert raised.value.provenance == "MALFORMED" and executor.calls == []


def test_unknown_policy_provenance_is_revalidated_under_the_current_policy(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    url = _url(tmp_path)
    service = _service(url, evidence)
    pending = service.submit(submission(), ANALYST)
    _sql(url, f"UPDATE approval_policy_provenance SET policy_version = '{'0' * 64}'")

    approved = service.decide(pending.approval_request_id, ApprovalState.APPROVED,
                              decision(pending), OWNER)

    assert approved.state == ApprovalState.EXECUTED


# ---------------------------------------------------- execution after a change


def test_failed_execution_is_not_retried_once_the_policy_blocks(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    url = _url(tmp_path)
    failing = CountingExecutor(fail=True)
    service = _service(url, evidence, executor=failing)
    pending = service.submit(submission(), ANALYST)
    failed = service.decide(pending.approval_request_id, ApprovalState.APPROVED,
                            decision(pending), OWNER)
    assert failed.state == ApprovalState.FAILED
    before = _rows(url, skip=("audit_events",))
    executor = CountingExecutor()

    for _ in range(2):
        with pytest.raises(RevisionRequiredError):
            _service(url, evidence, guardrails=blocking, executor=executor).retry_failed_execution(
                pending.approval_request_id, "corr-retry", OWNER
            )

    assert _rows(url, skip=("audit_events",)) == before
    assert executor.calls == []
    assert len(_audit(service, "EXECUTION_REVALIDATION_BLOCKED")) == 2


def test_interrupted_execution_is_not_recovered_once_the_policy_blocks(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    url = _url(tmp_path)
    service = _service(url, evidence, executor=CountingExecutor(fail=True))
    pending = service.submit(submission(), ANALYST)
    service.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER)
    _sql(url, "UPDATE approval_requests SET state = 'EXECUTING'")  # crash mid-execution
    executor = CountingExecutor()

    assert _service(url, evidence, guardrails=blocking, executor=executor
                    ).recover_interrupted_executions(OWNER) == 0
    assert executor.calls == []
    assert service.get_approval(pending.approval_request_id).state == ApprovalState.EXECUTING
    assert len(_audit(service, "EXECUTION_REVALIDATION_BLOCKED")) == 1

    # Under a policy that still accepts it, recovery completes it exactly once.
    current = _service(url, evidence, executor=executor)
    assert current.recover_interrupted_executions(OWNER) == 1
    assert len(executor.calls) == 1
    assert current.get_approval(pending.approval_request_id).state == ApprovalState.EXECUTED


def test_executed_records_are_never_changed_by_a_newer_policy(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    url = _url(tmp_path)
    service = _service(url, evidence)
    pending = service.submit(submission(), ANALYST)
    service.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER)
    before = _rows(url, skip=("audit_events",))
    newer = _service(url, evidence, guardrails=blocking, executor=(executor := CountingExecutor()))

    replay = newer.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending),
                          OWNER)  # idempotent replay of the stored decision
    with pytest.raises((WorkflowConflictError, InvalidTransitionError)):
        newer.decide(pending.approval_request_id, ApprovalState.APPROVED,
                     decision(pending, key="new-key"), OWNER)
    with pytest.raises(InvalidTransitionError):
        newer.retry_failed_execution(pending.approval_request_id, "corr", OWNER)

    assert replay.state == ApprovalState.EXECUTED
    assert _rows(url, skip=("audit_events",)) == before and executor.calls == []


# ----------------------------------------------- integrity, evidence, separation


def test_altered_recommendation_content_is_refused(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    url = _url(tmp_path)
    service = _service(url, evidence, executor=(executor := CountingExecutor()))
    pending = service.submit(submission(), ANALYST)
    _sql(url, "UPDATE recommendations SET payload_json = "
              "replace(payload_json, 'with the owner', 'immediately')")

    with pytest.raises(RevisionRequiredError) as raised:
        service.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending),
                       OWNER)
    with pytest.raises(WorkflowConflictError, match="outdated"):
        service.decide(pending.approval_request_id, ApprovalState.APPROVED,
                       decision(pending.model_copy(update={"recommendation_version_hash":
                                                           "f" * 64}), key="k2"), OWNER)

    assert raised.value.reason_codes == ("RECOMMENDATION_INTEGRITY_FAILED",)
    assert executor.calls == []


def test_changed_or_unavailable_original_evidence_requires_revision(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    url = _url(tmp_path)
    pending = _service(url, evidence).submit(submission(), ANALYST)
    finding = evidence.findings[0]
    changed = evidence.model_copy(update={"findings": (
        finding.model_copy(update={"data_limitations": ("A different limitation.",)}),
    )})
    rerun = evidence.model_copy(update={"analysis_run_id": "run_" + "c" * 24})

    for served in (changed, rerun):
        with pytest.raises(RevisionRequiredError) as raised:
            _service(url, served).decide(pending.approval_request_id, ApprovalState.APPROVED,
                                         decision(pending), OWNER)
        assert raised.value.reason_codes == ("ORIGINAL_EVIDENCE_UNAVAILABLE",)


def test_self_approval_is_refused_before_any_policy_check(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    service = _service(_url(tmp_path), evidence)
    pending = service.submit(submission(), ANALYST)

    with pytest.raises(SelfApprovalError):
        service.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending),
                       ANALYST)
    assert _audit(service, "APPROVAL_REVALIDATION_BLOCKED") == []


def test_blocked_audit_events_carry_codes_and_hashes_only(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    url = _url(tmp_path)
    pending = _service(url, evidence).submit(submission(), ANALYST)
    newer = _service(url, evidence, guardrails=blocking)
    with pytest.raises(RevisionRequiredError):
        newer.decide(pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER)

    events = [e for e in newer.list_audit(1000) if e.action_type.endswith("REVALIDATION_BLOCKED")]
    rendered = json.dumps([e.model_dump(mode="json") for e in events])
    assert set(events[0].details) == {
        "decision", "reason_codes", "policy_provenance", "guardrail_policy_version"
    }
    assert not any(fragment in rendered for fragment in TEXT.values())
    assert "Bearer" not in rendered and "token" not in rendered.lower()


def test_api_returns_revision_required_with_codes_only(
    tmp_path: Path, evidence: EvidenceContractV1, blocking: GuardrailEngine  # noqa: F811
) -> None:
    from process_optimizer.security.auth import LocalAuthenticationAdapter

    url = _url(tmp_path)
    pending = _service(url, evidence).submit(submission(), ANALYST)
    app.state.workflow_service = _service(url, evidence, guardrails=blocking)
    app.state.authentication_adapter = LocalAuthenticationAdapter({"owner": OWNER})
    body = decision(pending).model_dump(mode="json")

    async def call() -> Any:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/workflow/approvals/{pending.approval_request_id}/approve",
                json=body, headers={"Authorization": "Bearer owner"},
            )

    try:
        response = asyncio.run(call())
    finally:
        del app.state.workflow_service
        del app.state.authentication_adapter

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVISION_REQUIRED"
    assert "UNSUPPORTED_CAUSAL_STATEMENT" in response.json()["detail"]["reason_codes"]
    assert not any(fragment in response.text for fragment in TEXT.values())


# ------------------------------------------------------------------ concurrency


def test_concurrent_approvals_and_retries_create_one_ticket(
    tmp_path: Path, evidence: EvidenceContractV1  # noqa: F811
) -> None:
    executor = CountingExecutor()
    service = _service(_url(tmp_path), evidence, executor=executor)
    pending = service.submit(submission(), ANALYST)

    def attempt(index: int) -> str:
        try:
            if index % 2:
                return service.retry_failed_execution(
                    pending.approval_request_id, f"corr-{index}", OWNER
                ).state
            return service.decide(pending.approval_request_id, ApprovalState.APPROVED,
                                  decision(pending, key=f"k-{index}"), OWNER).state
        except (WorkflowConflictError, InvalidTransitionError):
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, range(6)))

    assert outcomes.count(ApprovalState.EXECUTED) >= 1
    assert len(executor.calls) == 1 and len(service.list_tickets()) == 1


@requires_postgres
def test_postgres_policy_change_and_races_keep_one_outcome(
    postgres: dict[str, str], evidence: EvidenceContractV1, tmp_path: Path  # noqa: F811
) -> None:
    _migrate(postgres)
    blocking = _policy(tmp_path, "pg-blocking", lambda raw: raw["output"][
        "assertive_causal_patterns"].append(r"\bReview the aggregate lifecycle\b"))
    old = WorkflowService(Database(postgres["runtime_url"]), evidence, apply_migrations=False)
    stale = old.submit(submission(suffix=" stale", key="stale"), ANALYST)
    other = submission(suffix=" fresh", key="fresh")
    fresh = old.submit(other.model_copy(update={"recommendation": other.recommendation.model_copy(
        update={"recommendation_id": "recommendation_" + "c" * 24})}), ANALYST)
    executor = CountingExecutor()
    newer = WorkflowService(Database(postgres["runtime_url"]), evidence, guardrails=blocking,
                            executor=executor, apply_migrations=False)
    current = WorkflowService(Database(postgres["runtime_url"]), evidence, executor=executor,
                              apply_migrations=False)

    def attempt(args: tuple[WorkflowService, Any, str]) -> str:
        service, pending, key = args
        try:
            return service.decide(pending.approval_request_id, ApprovalState.APPROVED,
                                  decision(pending, key=key), OWNER).state
        except RevisionRequiredError:
            return "REVISION_REQUIRED"
        except (WorkflowConflictError, InvalidTransitionError):
            return "CONFLICT"

    jobs = [(newer, stale, f"s-{i}") for i in range(4)] + [
        (current, fresh, f"f-{i}") for i in range(4)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, jobs))

    assert outcomes[:4] == ["REVISION_REQUIRED"] * 4
    assert outcomes[4:].count(ApprovalState.EXECUTED) >= 1
    assert set(outcomes[4:]) <= {ApprovalState.EXECUTED, "CONFLICT"}
    assert len(executor.calls) == 1 and len(current.list_tickets()) == 1
    assert current.get_approval(stale.approval_request_id).state == ApprovalState.PENDING_APPROVAL
    for service in (old, newer, current):
        service.database.engine.dispose()
