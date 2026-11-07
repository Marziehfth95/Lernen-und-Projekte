# ruff: noqa: F811  (pytest fixtures imported from test_demo_launcher are parameters)
"""Optional real local AI (Ollama): loopback-only transport, no fallback, unchanged guardrails.

No test here contacts a real Ollama server. ``FakeOllama`` is an ``httpx.MockTransport``
that answers ``/api/chat`` by running ``DeterministicMockProvider`` on the exact JSON
context the provider sent, so the real provider, LangGraph workflow, validator, guardrail
engine and workflow service all run. Each failure case mutates one reply.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tests.test_demo_launcher import analysis_dir  # noqa: F401  (shared fixture)
from tests.test_interactive_demo import _prepare, _served_pin

from process_optimizer.agents import ollama_provider
from process_optimizer.agents.ollama_provider import (
    ModelProviderError,
    OllamaLocalProvider,
    grammar_safe_schema,
    loopback_base_url,
    ollama_status,
    stage_constrained_schema,
    validate_model_name,
)
from process_optimizer.agents.provider import DeterministicMockProvider, model_response_format
from process_optimizer.agents.schemas import (
    InvestigationRequestV1,
    InvestigatorOutput,
    OptimizationOutput,
)
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.demo import interactive
from process_optimizer.demo.interactive import build_runtime, create_interactive_app
from process_optimizer.persistence.models import AuditEventRecord, ImprovementActionRecord
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1

MODEL = "qwen3.5:9b"
GOAL = (
    "Investigate the observed association between repeated group visits and observed "
    "case spans, then propose a safe evidence-grounded next step."
)
Mutation = Callable[[str, dict[str, Any]], Any]


class FakeOllama:
    """Scripted local model: the mock's answer to the real request, optionally mutated."""

    def __init__(
        self,
        evidence: EvidenceContractV1,
        *,
        mutate: Mutation | None = None,
        reported_model: str = MODEL,
        done_reason: str = "stop",
        fail: Exception | None = None,
    ) -> None:
        self._mock = DeterministicMockProvider()
        self._evidence = evidence
        self._mutate = mutate
        self._reported_model = reported_model
        self._done_reason = done_reason
        self._fail = fail
        self.requests: list[httpx.Request] = []
        self.payloads: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._fail is not None:
            raise self._fail
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [_tag(MODEL)]})
        assert request.url.path == "/api/chat", request.url.path
        payload = json.loads(request.content)
        self.payloads.append(payload)
        system, user = payload["messages"][0]["content"], payload["messages"][1]["content"]
        role = system.split("You are the ", 1)[1].split(" in an aggregate", 1)[0]
        context = json.loads(user)["trusted_aggregate_context"]
        output = self._answer(role, context)
        if self._mutate is not None:
            output = self._mutate(role, output)
        content = output if isinstance(output, str) else json.dumps(output)
        return httpx.Response(
            200,
            json={
                "model": self._reported_model,
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": self._done_reason,
                "prompt_eval_count": 1200,
                "eval_count": 300,
            },
        )

    def _answer(self, role: str, context: dict[str, Any]) -> dict[str, Any]:
        scenario = context["scenario"]
        if role == "Process Analyst":
            output = self._mock.select_findings(GOAL, scenario, self._evidence)
        elif role == "Root Cause Investigator":
            output = self._mock.plan_investigation(
                GOAL,
                scenario,
                tuple(context["selected_finding_ids"]),
                tuple(context["prior_tool_records"]),
            )
        else:
            output = self._mock.propose_optimizations(
                GOAL,
                scenario,
                tuple(item["finding_id"] for item in context["selected_evidence"]),
                self._evidence,
                tuple(context["tool_records"]),
            )
        return output.model_dump(mode="json")


def _tag(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "model": name,
        "digest": "a" * 64,
        "details": {"parameter_size": "9.7B", "quantization_level": "Q4_K_M", "family": "qwen35"},
        "capabilities": ["completion", "tools"],
        **extra,
    }


def _evidence(directory: Path) -> EvidenceContractV1:
    return EvidenceContractV1.model_validate_json(
        (directory / "evidence_contract.json").read_text(encoding="utf-8")
    )


def _run(directory: Path, fake: FakeOllama, scenario: str = "group_returns") -> Any:
    provider = OllamaLocalProvider(model=MODEL, transport=fake.transport())
    runner = InvestigationRunner(directory, provider=provider)
    result = runner.run(
        InvestigationRequestV1(
            analysis_run_id=runner.analysis_run_id,
            investigation_goal=GOAL,
            execution_mode="real_model",
            scenario=scenario,
        )
    )
    return result, provider


def _set_recommendation(field: str, value: Any) -> Mutation:
    def mutate(role: str, output: Any) -> Any:
        if role == "Optimization Agent" and output.get("recommendations"):
            output["recommendations"][0][field] = value
        return output

    return mutate


# ----------------------------------------------------------------- configuration safety


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://localhost:11434/", "http://localhost:11434"),
        ("http://[::1]:11434", "http://[::1]:11434"),
        ("http://127.0.0.1", "http://127.0.0.1:11434"),
    ],
)
def test_loopback_urls_are_accepted(value: str, expected: str) -> None:
    assert loopback_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:11434",
        "http://0.0.0.0:11434",
        "http://192.168.1.10:11434",
        "http://ollama.com",
        "http://api.example.com:11434",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1:11434/v1",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1.example.com",
    ],
)
def test_non_loopback_urls_are_refused(value: str) -> None:
    with pytest.raises(ModelProviderError) as caught:
        loopback_base_url(value)
    assert caught.value.category == "PROVIDER_CONFIGURATION_ERROR"


@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "qwen3:cloud", "", " ", "-x", "a b"])
def test_cloud_or_malformed_model_names_are_refused(name: str) -> None:
    with pytest.raises(ModelProviderError):
        validate_model_name(name)


def test_status_never_pulls_and_refuses_missing_or_remote_models() -> None:
    seen: list[str] = []

    def handler(models: list[dict[str, Any]]) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(f"{request.method} {request.url.path}")
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.34.0"})
            return httpx.Response(200, json={"models": models})

        return httpx.MockTransport(handle)

    base = "http://127.0.0.1:11434"
    ok = ollama_status(base, MODEL, transport=handler([_tag(MODEL)]))
    assert ok["available"] and ok["ollama_version"] == "0.34.0"
    missing = ollama_status(base, MODEL, transport=handler([_tag("other:1b")]))
    assert missing == {**missing, "available": False, "reason": "MODEL_NOT_INSTALLED_LOCALLY"}
    remote = ollama_status(
        base, MODEL, transport=handler([_tag(MODEL, remote_host="https://ollama.com")])
    )
    assert remote["reason"] == "REMOTE_MODEL_REFUSED"
    assert set(seen) == {"GET /api/version", "GET /api/tags"}  # no pull, no chat

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    unreachable = ollama_status(base, MODEL, transport=httpx.MockTransport(down))
    assert unreachable["reason"] == "OLLAMA_UNREACHABLE" and not unreachable["available"]
    rejected = ollama_status("http://10.0.0.1:11434", MODEL)
    assert rejected["reason"] == "CONFIGURATION_REJECTED"


def test_model_facing_schema_only_tightens() -> None:
    optimizer = model_response_format(OptimizationOutput)
    capped = grammar_safe_schema(optimizer["schema"])
    text = json.dumps(capped)
    assert '"maxLength": 3000' not in text and '"maxLength": 1000' in text
    assert '"minLength": 1' in text and "^recommendation_[0-9a-f]{24}$" in text
    narrowed = stage_constrained_schema(
        "InvestigatorOutput", model_response_format(InvestigatorOutput)["schema"]
    )
    titles = {item["title"] for item in narrowed["$defs"]["ToolRequest"]["anyOf"]}
    assert "ToolRequest_simulate_group_transition_reduction" not in titles
    assert "ToolRequest_get_evidence_details" in titles
    simulation_only = stage_constrained_schema("OptimizationOutput", optimizer["schema"])
    assert [item["title"] for item in simulation_only["$defs"]["ToolRequest"]["anyOf"]] == [
        "ToolRequest_simulate_group_transition_reduction"
    ]


# ------------------------------------------------------------------ graph-level behavior


def test_real_local_output_runs_the_unchanged_graph(analysis_dir: Path) -> None:
    fake = FakeOllama(_evidence(analysis_dir))
    result, provider = _run(analysis_dir, fake)
    assert result.execution_label == "REAL_MODEL_OUTPUT"
    assert result.final_decision == "VALIDATED_RECOMMENDATIONS"
    stages = {event.stage for event in result.audit_events}
    assert {"analyst", "investigator", "tool", "optimizer", "validator", "finalizer"} <= stages
    # Every request: loopback, native chat, schema-constrained, bounded, no thinking.
    assert {request.url.host for request in fake.requests} == {"127.0.0.1"}
    assert {request.url.path for request in fake.requests} == {"/api/chat"}
    for payload in fake.payloads:
        assert payload["model"] == MODEL and payload["think"] is False
        assert payload["stream"] is False and isinstance(payload["format"], dict)
        assert payload["options"]["num_ctx"] == ollama_provider.OLLAMA_NUM_CTX
        assert payload["options"]["num_predict"] <= 2048
    assert len(provider.invocation_records) <= 6  # policy max_model_calls_per_run
    record = provider.invocation_records[0]
    assert record["model_deployment"] == f"ollama:{MODEL}"
    assert record["response_model"] == MODEL
    assert record["provider"] == ollama_provider.PROVIDER_LABEL


@pytest.mark.parametrize(
    ("fake_kwargs", "expected_error"),
    [
        ({"mutate": lambda role, output: "{not json"}, "MODEL_OUTPUT_INVALID"),
        ({"mutate": lambda role, output: "[]"}, "MODEL_OUTPUT_INVALID"),
        ({"mutate": lambda role, output: {"unexpected": True}}, "MODEL_OUTPUT_INVALID"),
        ({"done_reason": "length"}, "MODEL_OUTPUT_INVALID"),
        ({"reported_model": "some-other-model:7b"}, "MODEL_OUTPUT_INVALID"),
        ({"fail": httpx.ConnectError("refused")}, "PROVIDER_CONNECTION_FAILED"),
        ({"fail": httpx.ReadTimeout("slow")}, "PROVIDER_TIMEOUT"),
    ],
)
def test_invalid_or_failed_model_replies_never_produce_a_recommendation(
    analysis_dir: Path,
    fake_kwargs: dict[str, Any],
    expected_error: str,
) -> None:
    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir), **fake_kwargs))
    assert result.execution_label == "REAL_MODEL_OUTPUT"
    assert result.final_decision == "SAFE_TERMINATION"
    assert result.recommendations == ()
    assert result.errors[-1].code == expected_error


def test_unsupported_evidence_from_the_model_is_rejected(analysis_dir: Path) -> None:
    fake = FakeOllama(
        _evidence(analysis_dir),
        mutate=_set_recommendation("evidence_ids", ["finding_" + "0" * 24]),
    )
    result, _ = _run(analysis_dir, fake)
    assert result.final_decision != "VALIDATED_RECOMMENDATIONS"
    assert not result.validation_results[0].valid
    assert "INVALID_EVIDENCE_ID" in result.validation_results[0].error_codes


def test_fabricated_number_from_the_model_is_rejected(analysis_dir: Path) -> None:
    def mutate(role: str, output: Any) -> Any:
        if role == "Optimization Agent" and output.get("recommendations"):
            output["recommendations"][0]["observed_numerical_facts"][0]["value"] = 123456789
        return output

    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir), mutate=mutate))
    assert result.final_decision != "VALIDATED_RECOMMENDATIONS"
    assert "FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM" in result.validation_results[0].error_codes


def test_unsafe_causal_claim_from_the_model_is_blocked(analysis_dir: Path) -> None:
    fake = FakeOllama(
        _evidence(analysis_dir),
        mutate=_set_recommendation(
            "problem_statement",
            "Repeated group returns lead to longer case spans for these incidents.",
        ),
    )
    result, _ = _run(analysis_dir, fake)
    assert result.final_decision != "VALIDATED_RECOMMENDATIONS"
    assert all(not item.valid for item in result.validation_results)


def test_known_open_not_safe_gap_remains_visible(analysis_dir: Path) -> None:
    """Policy 2.0.0 does not block plain 'cause'; a real model is not treated differently."""
    fake = FakeOllama(
        _evidence(analysis_dir),
        mutate=_set_recommendation(
            "problem_statement",
            "Repeated group returns cause longer case spans and delay closure.",
        ),
    )
    result, _ = _run(analysis_dir, fake)
    assert result.final_decision == "VALIDATED_RECOMMENDATIONS"  # documented gap, not a pass


def test_guardrail_still_blocks_a_disallowed_tool_without_grammar(
    analysis_dir: Path,
) -> None:
    """The fake ignores the narrowed schema, so only PRE_TOOL_CALL can stop this request."""

    def mutate(role: str, output: Any) -> Any:
        if role == "Root Cause Investigator" and output["requested_tools"]:
            output["requested_tools"] = [
                {"name": "simulate_group_transition_reduction", "arguments": {}}
            ]
        return output

    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir), mutate=mutate))
    assert result.final_decision == "SAFE_TERMINATION"
    assert result.errors[-1].code == "GUARDRAIL_BLOCKED"
    assert result.tool_call_records == ()
    assert any(
        event.safe_details.get("guardrail_reason_codes") == "TOOL_NOT_ALLOWED_FOR_STAGE"
        for event in result.audit_events
    )


# --------------------------------------------------------------- interactive application


@pytest.fixture
def local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    for name in interactive.CLOUD_HOSTING_MARKERS:
        monkeypatch.delenv(name, raising=False)


def _app(directory: Path, tmp_path: Path) -> tuple[Any, Any]:
    _prepare(directory)
    workdir = tmp_path / "run"
    workdir.mkdir()
    runtime = build_runtime(
        directory,
        f"sqlite:///{workdir / 'interactive.sqlite3'}",
        workdir,
        expected_evidence_sha256=_served_pin(directory, tmp_path),
    )
    return runtime, create_interactive_app(runtime)


def _use_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeOllama) -> None:
    def provider(**kwargs: Any) -> OllamaLocalProvider:
        return OllamaLocalProvider(**kwargs, transport=fake.transport())

    def status(base_url: str, model: str) -> dict[str, Any]:
        return ollama_status(base_url, model, transport=fake.transport())

    monkeypatch.setattr(interactive, "OllamaLocalProvider", provider)
    monkeypatch.setattr(interactive, "ollama_status", status)


def _client_run(app: Any, scenario: Any) -> None:
    async def go() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            await scenario(client)

    asyncio.run(go())


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_session_offers_explicit_model_choice_with_deterministic_default(
    analysis_dir: Path,
    tmp_path: Path,
    local_env: None,
) -> None:
    runtime, app = _app(analysis_dir, tmp_path)

    async def scenario(client: AsyncClient) -> None:
        session = (await client.get("/demo/v2/session")).json()
        assert session["default_model_mode"] == "deterministic"
        options = {item["key"]: item for item in session["model_options"]}
        assert options["deterministic"]["execution_label"] == "DETERMINISTIC_MOCK_NOT_REAL_AI"
        assert options["ollama"]["execution_label"] == "REAL_MODEL_OUTPUT"
        assert options["ollama"]["model"] == MODEL
        assert options["ollama"]["endpoint"] == "http://127.0.0.1:11434"
        assert session["isolation"]["paid_model_calls"] is False
        assert any(item["id"] == "OPEN_NOT_SAFE" for item in session["known_limitations"])
        # The default stays the mock when no model_mode is sent.
        token = runtime.identities[0].token
        default = await client.post(
            "/demo/v2/investigations", json={"scenario": "group_returns"}, headers=_h(token)
        )
        assert default.json()["execution_label"] == "DETERMINISTIC_MOCK_NOT_REAL_AI"
        assert default.json()["model"]["real_ai"] is False

    _client_run(app, scenario)


def test_non_loopback_model_endpoint_blocks_startup(
    analysis_dir: Path,
    tmp_path: Path,
    local_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://203.0.113.5:11434")
    with pytest.raises(interactive.DemoStartupError):
        _app(analysis_dir, tmp_path)


def test_real_local_ai_end_to_end_with_human_approval(
    analysis_dir: Path,
    tmp_path: Path,
    local_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, app = _app(analysis_dir, tmp_path)
    fake = FakeOllama(_evidence(analysis_dir))
    _use_fake(monkeypatch, fake)
    tokens = {item.key: item.token for item in runtime.identities}

    def count(model: Any) -> int:
        with runtime.database.session() as session:
            return session.scalar(select(func.count()).select_from(model))

    async def scenario(client: AsyncClient) -> None:
        response = await client.post(
            "/demo/v2/investigations",
            json={"scenario": "group_returns", "model_mode": "ollama"},
            headers=_h(tokens["analyst"]),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["execution_label"] == "REAL_MODEL_OUTPUT"
        assert payload["model"]["model"] == MODEL
        assert payload["model"]["genuine_model_response"] is True
        assert payload["model"]["invocations"]["service_reported_models"] == [MODEL]
        assert payload["draft"]["generated_by"] == MODEL
        assert payload["draft"]["modified_after_generation"] is False
        assert payload["preflight"]["decision"] == "REQUIRE_APPROVAL"
        assert count(ImprovementActionRecord) == 0

        submission = {
            **payload["draft"]["submission"],
            "idempotency_key": "submit-ai",
            "correlation_id": "corr-ollama-ai",
        }
        submit = "/workflow/recommendations/submit"
        pending = await client.post(submit, json=submission, headers=_h(tokens["analyst"]))
        assert pending.status_code == 201
        replay = await client.post(submit, json=submission, headers=_h(tokens["analyst"]))
        assert replay.json()["approval_request_id"] == pending.json()["approval_request_id"]
        detail = pending.json()
        assert count(ImprovementActionRecord) == 0  # submission alone creates no ticket

        approval = f"/workflow/approvals/{detail['approval_request_id']}/approve"
        decision = {
            "recommendation_version_hash": detail["recommendation_version_hash"],
            "idempotency_key": "decide-ai",
            "correlation_id": detail["correlation_id"],
            "reason": "Reviewed the locally generated recommendation.",
        }
        denied = await client.post(approval, json=decision, headers=_h(tokens["analyst"]))
        assert denied.status_code == 403
        approved = await client.post(approval, json=decision, headers=_h(tokens["owner"]))
        assert approved.status_code == 200 and approved.json()["state"] == "EXECUTED"
        again = await client.post(approval, json=decision, headers=_h(tokens["owner"]))
        assert again.status_code == 200 and again.json()["state"] == "EXECUTED"
        assert count(ImprovementActionRecord) == 1
        with runtime.database.session() as session:
            correlations = set(session.scalars(select(AuditEventRecord.correlation_id)).all())
        assert "corr-ollama-ai" in correlations

    _client_run(app, scenario)


def test_blocked_real_model_output_is_not_submittable(
    analysis_dir: Path,
    tmp_path: Path,
    local_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, app = _app(analysis_dir, tmp_path)
    fake = FakeOllama(
        _evidence(analysis_dir),
        mutate=_set_recommendation(
            "expected_outcome_with_uncertainty",
            "This change will reduce the median case span by 30 percent.",
        ),
    )
    _use_fake(monkeypatch, fake)
    token = runtime.identities[0].token

    async def scenario(client: AsyncClient) -> None:
        payload = (
            await client.post(
                "/demo/v2/investigations",
                json={"scenario": "group_returns", "model_mode": "ollama"},
                headers=_h(token),
            )
        ).json()
        assert payload["execution_label"] == "REAL_MODEL_OUTPUT"
        assert payload["draft"]["graph_final_decision"] != "VALIDATED_RECOMMENDATIONS"
        assert payload["preflight"]["decision"] == "BLOCK"
        submission = {
            **payload["draft"]["submission"],
            "idempotency_key": "submit-blocked",
            "correlation_id": "corr-blocked",
        }
        refused = await client.post(
            "/workflow/recommendations/submit", json=submission, headers=_h(token)
        )
        assert refused.status_code == 422

    _client_run(app, scenario)


@pytest.mark.parametrize(
    ("fake_kwargs", "status_code", "code"),
    [
        ({"fail": httpx.ConnectError("refused")}, 503, "LOCAL_MODEL_UNAVAILABLE"),
        ({"reported_model": "impostor:7b"}, 502, "MODEL_PROVENANCE_MISMATCH"),
    ],
)
def test_unavailable_or_impostor_model_never_falls_back_to_mock(
    analysis_dir: Path,
    tmp_path: Path,
    local_env: None,
    monkeypatch: pytest.MonkeyPatch,
    fake_kwargs: dict[str, Any],
    status_code: int,
    code: str,
) -> None:
    runtime, app = _app(analysis_dir, tmp_path)
    _use_fake(monkeypatch, FakeOllama(_evidence(analysis_dir), **fake_kwargs))
    token = runtime.identities[0].token

    async def scenario(client: AsyncClient) -> None:
        response = await client.post(
            "/demo/v2/investigations",
            json={"scenario": "group_returns", "model_mode": "ollama"},
            headers=_h(token),
        )
        assert response.status_code == status_code
        assert response.json()["detail"]["code"] == code
        assert "DETERMINISTIC_MOCK_NOT_REAL_AI" not in response.text

    _client_run(app, scenario)


def test_concurrent_local_model_run_is_refused(
    analysis_dir: Path,
    tmp_path: Path,
    local_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, app = _app(analysis_dir, tmp_path)
    _use_fake(monkeypatch, FakeOllama(_evidence(analysis_dir)))
    token = runtime.identities[0].token

    async def scenario(client: AsyncClient) -> None:
        runtime.model_lock.acquire()
        try:
            response = await client.post(
                "/demo/v2/investigations",
                json={"scenario": "group_returns", "model_mode": "ollama"},
                headers=_h(token),
            )
        finally:
            runtime.model_lock.release()
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LOCAL_MODEL_BUSY"

    _client_run(app, scenario)


# ----------------------------------------------------------------- run-summary step states


def _states(result: Any) -> dict[str, str]:
    return {step["key"]: step["state"] for step in interactive.investigation_steps(result)}


def test_run_summary_marks_every_step_completed_only_on_success(analysis_dir: Path) -> None:
    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir)))
    assert set(_states(result).values()) == {"COMPLETED"}


def test_run_summary_shows_failed_and_not_executed_steps(analysis_dir: Path) -> None:
    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir), mutate=lambda r, o: "{bad"))
    assert _states(result) == {
        "initialize": "COMPLETED",
        "analyst": "FAILED",
        "investigator": "NOT_EXECUTED",
        "tool": "NOT_EXECUTED",
        "optimizer": "NOT_EXECUTED",
        "validator": "NOT_EXECUTED",
        "output_guardrail": "NOT_EXECUTED",
    }


def test_run_summary_shows_guardrail_block_at_the_tool_step(analysis_dir: Path) -> None:
    def mutate(role: str, output: Any) -> Any:
        if role == "Root Cause Investigator" and output["requested_tools"]:
            output["requested_tools"] = [
                {"name": "simulate_group_transition_reduction", "arguments": {}}
            ]
        return output

    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir), mutate=mutate))
    states = _states(result)
    assert states["investigator"] == "COMPLETED"
    assert states["tool"] == "BLOCKED"
    assert states["optimizer"] == states["validator"] == "NOT_EXECUTED"
    tool = next(s for s in interactive.investigation_steps(result) if s["key"] == "tool")
    assert "TOOL_NOT_ALLOWED_FOR_STAGE" in tool["detail"]


def test_run_summary_shows_rejected_evidence_as_blocked(analysis_dir: Path) -> None:
    fake = FakeOllama(
        _evidence(analysis_dir),
        mutate=_set_recommendation("evidence_ids", ["finding_" + "0" * 24]),
    )
    result, _ = _run(analysis_dir, fake)
    states = _states(result)
    assert states["optimizer"] == "COMPLETED"
    assert states["validator"] == "BLOCKED" and states["output_guardrail"] == "BLOCKED"
    validator = next(s for s in interactive.investigation_steps(result) if s["key"] == "validator")
    assert "INVALID_EVIDENCE_ID" in validator["detail"]


def test_run_summary_shows_provider_failure(analysis_dir: Path) -> None:
    result, _ = _run(
        analysis_dir, FakeOllama(_evidence(analysis_dir), fail=httpx.ReadTimeout("slow"))
    )
    states = _states(result)
    assert states["analyst"] == "FAILED"
    assert all(states[key] == "NOT_EXECUTED" for key in ("tool", "optimizer", "validator"))


def test_run_summary_shows_iteration_limit_without_audit_event(analysis_dir: Path) -> None:
    """The graph records ITERATION_LIMIT_REACHED only as an error; it must not show ✓."""

    def mutate(role: str, output: Any) -> Any:
        if role == "Root Cause Investigator":
            output["needs_more_analysis"] = True
            output["requested_tools"] = [{"name": "get_process_overview", "arguments": {}}]
        return output

    result, _ = _run(analysis_dir, FakeOllama(_evidence(analysis_dir), mutate=mutate))
    assert any(error.code == "ITERATION_LIMIT_REACHED" for error in result.errors)
    states = _states(result)
    assert states["investigator"] == "BLOCKED"
    assert states["optimizer"] == "NOT_EXECUTED"


def test_run_summary_is_served_for_the_deterministic_mock(
    analysis_dir: Path, tmp_path: Path, local_env: None
) -> None:
    runtime, app = _app(analysis_dir, tmp_path)
    token = runtime.identities[0].token

    async def scenario(client: AsyncClient) -> None:
        payload = (
            await client.post(
                "/demo/v2/investigations", json={"scenario": "group_returns"}, headers=_h(token)
            )
        ).json()
        assert payload["execution_label"] == "DETERMINISTIC_MOCK_NOT_REAL_AI"
        assert {step["state"] for step in payload["steps"]} == {"COMPLETED"}
        assert len(payload["steps"]) == 7

    _client_run(app, scenario)
