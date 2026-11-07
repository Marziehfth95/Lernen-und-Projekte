"""EV-04: Hosted Agent / Workflow API guardrail-policy compatibility (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.test_agent_workflow import analysis_dir  # noqa: F401 - pytest fixture re-export
from tests.test_causal_grounding_matrix import _historical
from tests.test_guardrails import _paid_environment

from process_optimizer.agents import hosting
from process_optimizer.agents.schemas import EvidenceValidationResult
from process_optimizer.guardrails import Decision, GuardrailEngine, evidence_contract_sha256
from process_optimizer.guardrails import engine as engine_module
from process_optimizer.guardrails.compatibility import policy_metadata
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.security.auth import LocalAuthenticationAdapter, Permission, Principal
from process_optimizer.tools.client import AuthenticatedAggregateApiTools

ENGINE = GuardrailEngine()
CURRENT = policy_metadata(ENGINE.policy).model_dump()
# The policy deployed with Hosted Agent v5 and Workflow API revision --0000002.
PREVIOUS = {
    "policy_id": "process-optimizer-guardrails",
    "policy_schema_version": "1.0.0",
    "policy_version": "c2d0bbbe18830a8c627259b83c8ab5cffa9a01320f9d50ce4aa252da1f1df614",
}


class _Reached(Exception):
    pass


def _start(
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: Any,
    *,
    pin: str | None = None,
) -> list[str]:
    """Run hosting.main() up to the runner with a fake aggregate client."""
    contract = EvidenceContractV1.model_validate_json(
        (directory / "evidence_contract.json").read_text(encoding="utf-8")
    )
    calls: list[str] = []

    class Tools:
        def get_evidence_contract(self) -> EvidenceContractV1:
            calls.append("evidence_contract")
            return contract

        def get_guardrail_policy_metadata(self) -> Any:
            calls.append("guardrail_policy")
            if isinstance(metadata, Exception):
                raise metadata
            return metadata

    for key, value in _paid_environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("EVIDENCE_CONTRACT_SHA256", pin or evidence_contract_sha256(contract))
    monkeypatch.setattr(hosting, "FoundryModelProvider", lambda: object())
    monkeypatch.setattr(
        hosting.AuthenticatedAggregateApiTools, "from_environment", staticmethod(lambda: Tools())
    )

    def runner(**kwargs: Any) -> Any:
        raise _Reached

    monkeypatch.setattr(hosting, "InvestigationRunner", runner)
    hosting.main()
    return calls


def test_matching_policy_lets_the_host_start(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    with pytest.raises(_Reached):
        _start(analysis_dir, monkeypatch, CURRENT)


@pytest.mark.parametrize(
    ("metadata", "codes"),
    [
        # New agent, old API: the route does not exist.
        (None, "GUARDRAIL_POLICY_METADATA_MISSING"),
        # New agent, an API serving the previous policy.
        (PREVIOUS, "GUARDRAIL_POLICY_SCHEMA_VERSION_MISMATCH, GUARDRAIL_POLICY_VERSION_MISMATCH"),
        ({**CURRENT, "policy_version": "0" * 64}, "GUARDRAIL_POLICY_VERSION_MISMATCH"),
        ({**CURRENT, "policy_id": "other-policy"}, "GUARDRAIL_POLICY_ID_MISMATCH"),
        ("not metadata", "GUARDRAIL_POLICY_METADATA_MALFORMED"),
        (ValueError("transport"), "GUARDRAIL_POLICY_METADATA_UNAVAILABLE"),
    ],
    ids=["missing", "previous-policy", "hash", "policy-id", "malformed", "unavailable"],
)
def test_incompatible_or_unknown_policy_refuses_startup(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    metadata: Any,
    codes: str,
) -> None:
    with pytest.raises(RuntimeError, match=f"refused to start: {codes}$"):
        _start(analysis_dir, monkeypatch, metadata)


def test_evidence_pin_is_still_checked_first(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    with pytest.raises(RuntimeError, match="EVIDENCE_CONTRACT_SHA256_MISMATCH"):
        _start(analysis_dir, monkeypatch, CURRENT, pin="0" * 64)


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {**CURRENT, "extra": "x"},
        {**CURRENT, "policy_version": CURRENT["policy_version"].upper()},
        {**CURRENT, "policy_version": CURRENT["policy_version"][:63]},
        {**CURRENT, "policy_schema_version": "2.0"},
        {**CURRENT, "policy_schema_version": 2},
        {k: v for k, v in CURRENT.items() if k != "policy_version"},
        [CURRENT],
        "",
        0,
    ],
)
def test_malformed_metadata_blocks(metadata: Any) -> None:
    decision = ENGINE.check_policy_compatibility(remote_metadata=metadata)

    assert decision.decision == Decision.BLOCK
    assert decision.reason_codes == ("GUARDRAIL_POLICY_METADATA_MALFORMED",)


def test_matching_metadata_allows_and_names_no_codes() -> None:
    decision = ENGINE.check_policy_compatibility(remote_metadata=dict(CURRENT))

    assert decision.decision == Decision.ALLOW
    assert decision.reason_codes == ()
    assert decision.policy_version == CURRENT["policy_version"]


def test_compatibility_check_defects_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> tuple[str, ...]:
        raise RuntimeError("defect")

    monkeypatch.setattr(engine_module, "policy_compatibility_codes", broken)
    decision = ENGINE.check_policy_compatibility(remote_metadata=CURRENT)

    assert decision.decision == Decision.BLOCK
    assert decision.reason_codes == ("GUARDRAIL_INTERNAL_ERROR",)


def test_old_agent_output_is_rechecked_under_the_new_api_policy() -> None:
    # An old agent (policy c2d0bbbe…) does not check the API. Its output still meets
    # the new policy at submission: this model-written recommendation passed the old policy.
    candidate = _historical(
        "tests/data/model_text_regression_recommendation.json", "recommendation_0123456789abcdef"
    )
    validation = EvidenceValidationResult(
        recommendation_id=candidate.recommendation_id,
        valid=True,
        validated_evidence_ids=candidate.evidence_ids,
        error_codes=(),
        notes=(),
    )
    decision = ENGINE.check_submission(
        recommendation=candidate, validation_result=validation, identity_type="human"
    )

    assert decision.decision == Decision.BLOCK
    assert "UNSUPPORTED_INTERVENTION_CLAIM" in decision.reason_codes


def _client(handler: Any) -> AuthenticatedAggregateApiTools:
    return AuthenticatedAggregateApiTools(
        "https://aggregate.example.test", lambda: "token", transport=httpx.MockTransport(handler)
    )


def test_client_returns_none_only_for_a_missing_route() -> None:
    assert _client(lambda request: httpx.Response(404)).get_guardrail_policy_metadata() is None
    body = {"correlation_id": "c", "guardrail_policy": CURRENT}
    served = _client(lambda request: httpx.Response(200, json=body))
    assert served.get_guardrail_policy_metadata() == CURRENT
    listed = _client(lambda request: httpx.Response(200, json=[CURRENT]))
    assert listed.get_guardrail_policy_metadata() == [CURRENT]  # rejected by the engine


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(401), httpx.Response(403),
     httpx.Response(200, content=b"not json")],
)
def test_client_raises_on_any_other_failure(response: httpx.Response) -> None:
    with pytest.raises(ValueError, match="guardrail policy retrieval failed"):
        _client(lambda request: response).get_guardrail_policy_metadata()


def _api(directory: Path, permissions: frozenset[Permission]) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from process_optimizer.api.aggregate import VerifiedAggregateService
    from process_optimizer.api.aggregate import router as aggregate_router
    from process_optimizer.observability import CorrelationAndTelemetryMiddleware

    contract = EvidenceContractV1.model_validate_json(
        (directory / "evidence_contract.json").read_text(encoding="utf-8")
    )

    class Verifier:
        def __init__(self) -> None:
            self.analysis_dir = directory

        def verify(self) -> EvidenceContractV1:
            return contract

    app = FastAPI()
    app.include_router(aggregate_router)
    app.add_middleware(CorrelationAndTelemetryMiddleware)
    principal = Principal(
        actor_id="agent.identity", roles=frozenset(), permissions=permissions,
        identity_type="service",
    )
    app.state.authentication_adapter = LocalAuthenticationAdapter({"token": principal})
    app.state.aggregate_service = VerifiedAggregateService(Verifier())  # type: ignore[arg-type]
    return TestClient(app)


def test_policy_route_serves_identity_only_to_aggregate_readers(
    analysis_dir: Path,  # noqa: F811
) -> None:
    with _api(analysis_dir, frozenset({Permission.AGGREGATE_READ})) as client:
        response = client.get(
            "/aggregate/v1/guardrail-policy", headers={"Authorization": "Bearer token"}
        )
        anonymous = client.get("/aggregate/v1/guardrail-policy")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"correlation_id", "guardrail_policy"}
    assert body["guardrail_policy"] == CURRENT
    assert "pattern" not in response.text and "hosted_runtime" not in response.text
    assert anonymous.status_code == 401



def test_policy_route_reports_the_policy_enforced_at_submission(
    analysis_dir: Path, tmp_path: Path  # noqa: F811
) -> None:
    from process_optimizer.persistence.database import Database
    from process_optimizer.workflow.service import WorkflowService

    evidence = EvidenceContractV1.model_validate_json(
        (analysis_dir / "evidence_contract.json").read_text(encoding="utf-8")
    )
    service = WorkflowService(Database(f"sqlite:///{tmp_path / 'workflow.sqlite3'}"), evidence)

    assert policy_metadata(service.guardrails.policy).model_dump() == CURRENT


def test_policy_route_requires_aggregate_read(analysis_dir: Path) -> None:  # noqa: F811
    with _api(analysis_dir, frozenset()) as client:
        response = client.get(
            "/aggregate/v1/guardrail-policy", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "INSUFFICIENT_PERMISSION"}
