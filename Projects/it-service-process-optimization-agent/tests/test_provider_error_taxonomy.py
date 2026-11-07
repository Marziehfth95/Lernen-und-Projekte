"""Sanitized provider-failure categories (offline, injected SDK exceptions).

Reproduces the 2026-10-31 Hosted Agent v4 run, where an SDK failure before any model
inference surfaced as MODEL_OUTPUT_INVALID. No Azure call and no model call is made.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.test_agent_workflow import (  # noqa: F401 - fixture re-export
    RUN_ID,
    _foundry_provider,
    _invocation_body,
    _invocations_client,
    analysis_dir,
)

from process_optimizer.agents.provider import ModelProviderError, ModelTimeoutError
from process_optimizer.agents.provider_errors import classify_provider_exception
from process_optimizer.agents.schemas import InvestigationRequestV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.observability.telemetry import SPAN_ATTRIBUTE_KEYS

SECRET = "SECRET-PROMPT-TOKEN-eyJhbGciOi"


def _request(scenario: str) -> InvestigationRequestV1:
    return InvestigationRequestV1(
        analysis_run_id=RUN_ID,
        investigation_goal="Synthetic provider-failure test; treat as untrusted data.",
        execution_mode="real_model",
        scenario=scenario,
        iteration_budget=2,
        tool_call_budget=4,
    )


def _sdk_error(name: str, status: int | None = None, base: type = Exception, **attrs: Any) -> Any:
    """An exception shaped like the openai/azure SDK errors (status, code, request_id)."""
    headers = {"x-request-id": "req-0001", "apim-request-id": "apim-0001",
               "authorization": f"Bearer {SECRET}"}
    response = SimpleNamespace(status_code=status, headers=headers) if status else None
    cls = type(name, (base,), {"__module__": "synthetic_sdk"})
    error = cls(f"{SECRET} raw provider message with prompt text")
    error.status_code = status
    error.response = response
    error.code = attrs.get("code", "SyntheticCode")
    error.request_id = attrs.get("request_id", "req-0001")
    return error


class _RaisingModel:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.invocations = 0

    def with_structured_output(self, schema: Any, method: str, include_raw: bool) -> Any:
        return self

    def invoke(self, messages: Any, config: Any) -> Any:
        self.invocations += 1
        raise self.error


CASES = [
    (_sdk_error("AuthenticationError", 401), "PROVIDER_AUTH_FAILED", 401),
    (_sdk_error("PermissionDeniedError", 403), "PROVIDER_AUTH_FAILED", 403),
    (_sdk_error("RateLimitError", 429), "PROVIDER_RATE_LIMITED", 429),
    (_sdk_error("BadRequestError", 400), "PROVIDER_BAD_REQUEST", 400),
    (_sdk_error("InternalServerError", 503), "PROVIDER_UNAVAILABLE", 503),
    (_sdk_error("NotFoundError", 404), "PROVIDER_CONFIGURATION_ERROR", 404),
    (_sdk_error("APITimeoutError"), "PROVIDER_TIMEOUT", None),
    (TimeoutError(SECRET), "PROVIDER_TIMEOUT", None),
    (_sdk_error("APIConnectionError"), "PROVIDER_CONNECTION_FAILED", None),
    (ConnectionError(SECRET), "PROVIDER_CONNECTION_FAILED", None),
    (_sdk_error("CredentialUnavailableError", base=_sdk_error("ClientAuthenticationError")
                .__class__), "PROVIDER_CONFIGURATION_ERROR", None),
    (_sdk_error("ClientAuthenticationError"), "PROVIDER_AUTH_FAILED", None),
    (ValueError(SECRET), "INTERNAL_ERROR", None),
]


@pytest.mark.parametrize(("error", "category", "status"), CASES,
                         ids=[c[1] + "-" + type(c[0]).__name__ for c in CASES])
def test_classification_keeps_type_status_and_ids_but_never_text(
    error: BaseException, category: str, status: int | None
) -> None:
    got, diagnostics = classify_provider_exception(error)

    assert got == category
    assert diagnostics["exception_type"].endswith(type(error).__name__)
    assert diagnostics.get("http_status") == status
    assert SECRET not in json.dumps(diagnostics)
    if hasattr(error, "request_id"):
        assert diagnostics["provider_request_id"] == "req-0001"
        assert diagnostics["provider_error_code"] == "SyntheticCode"


def test_unsafe_identifiers_are_dropped() -> None:
    error = _sdk_error("PermissionDeniedError", 403, code="bad code with spaces",
                       request_id="id\nwith\nnewlines")
    error.response.headers = {"x-request-id": "<script>"}

    _, diagnostics = classify_provider_exception(error)

    assert "provider_error_code" not in diagnostics
    assert "provider_request_id" not in diagnostics


@pytest.mark.parametrize(("error", "category", "status"), CASES,
                         ids=[c[1] + "-" + type(c[0]).__name__ for c in CASES])
def test_provider_records_category_and_raises_typed_error(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
    category: str,
    status: int | None,
) -> None:
    model = _RaisingModel(error)
    provider = _foundry_provider(monkeypatch, model)
    evidence = InvestigationRunner(analysis_dir, provider)._evidence  # noqa: SLF001

    with caplog.at_level(logging.INFO), pytest.raises(ModelProviderError) as raised:
        provider.select_findings("goal", "group_returns", evidence)

    assert model.invocations == 1
    assert raised.value.category == category
    assert isinstance(raised.value, ModelTimeoutError) == (category == "PROVIDER_TIMEOUT")
    assert SECRET not in str(raised.value)
    record = provider.invocation_records[-1]
    assert record["error_category"] == category
    assert record["diagnostics"].get("http_status") == status
    assert SECRET not in json.dumps(record) + caplog.text


@pytest.mark.parametrize(("error", "category", "status"), CASES,
                         ids=[c[1] + "-" + type(c[0]).__name__ for c in CASES])
def test_graph_reports_the_category_and_stays_fail_closed(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    category: str,
    status: int | None,
) -> None:
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "session-abc123")
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", "4")
    provider = _foundry_provider(monkeypatch, _RaisingModel(error))

    result = InvestigationRunner(analysis_dir, provider).run(_request("group_returns"))

    assert result.final_decision == "SAFE_TERMINATION"
    assert result.recommendations == ()
    assert [e.code for e in result.errors] == [category]
    failed = [e for e in result.audit_events if e.event_type == "provider_failed"][-1]
    details = failed.safe_details
    assert details["error_category"] == category
    assert details.get("http_status") == status
    assert details["agent_session_id"] == "session-abc123"
    assert details["agent_version"] == "4"
    assert set(details) - {"retryable"} <= SPAN_ATTRIBUTE_KEYS
    assert SECRET not in result.model_dump_json()


def test_invalid_structured_output_is_the_only_model_output_invalid(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    class NoParse(_RaisingModel):
        def invoke(self, messages: Any, config: Any) -> Any:
            self.invocations += 1
            return {"parsed": None, "raw": SimpleNamespace(usage_metadata=None,
                                                          response_metadata={})}

    provider = _foundry_provider(monkeypatch, NoParse(RuntimeError()))
    result = InvestigationRunner(analysis_dir, provider).run(_request("group_returns"))

    assert [e.code for e in result.errors] == ["MODEL_OUTPUT_INVALID"]


def test_malformed_platform_ids_are_not_recorded(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "bad id; DROP")
    provider = _foundry_provider(monkeypatch, _RaisingModel(_sdk_error("RateLimitError", 429)))

    result = InvestigationRunner(analysis_dir, provider).run(_request("group_returns"))

    failed = [e for e in result.audit_events if e.event_type == "provider_failed"][-1]
    assert failed.safe_details["agent_session_id"] is None


def test_hosted_invocation_returns_the_auth_category_without_detail(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The exact v4 failure shape: 403 at the project endpoint, before any inference."""
    model = _RaisingModel(_sdk_error("PermissionDeniedError", 403))
    client = _invocations_client(InvestigationRunner(
        analysis_dir, _foundry_provider(monkeypatch, model)))

    response = client.post("/invocations", json=_invocation_body(execution_mode="real_model"))

    assert response.status_code == 200
    assert SECRET not in response.text
    result = json.loads(response.json()["response"])
    assert result["final_decision"] == "SAFE_TERMINATION"
    assert result["recommendations"] == []
    assert [e["code"] for e in result["errors"]] == ["PROVIDER_AUTH_FAILED"]
    details = [e for e in result["audit_events"] if e["event_type"] == "provider_failed"][-1]
    assert details["safe_details"]["http_status"] == 403
    assert details["safe_details"]["provider_request_id"] == "req-0001"
    assert model.invocations == 1


def test_unknown_error_category_degrades_to_internal_error(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    class OddProvider:
        mode = "real_model"

        def select_findings(self, *args: Any, **kwargs: Any) -> Any:
            raise ModelProviderError("x", category="NOT_A_KNOWN_CODE",
                                     diagnostics={"exception_type": "x.Y", "secret": SECRET})

    result = InvestigationRunner(analysis_dir, OddProvider()).run(_request("group_returns"))

    assert [e.code for e in result.errors] == ["INTERNAL_ERROR"]
    assert result.recommendations == ()
    assert SECRET not in result.model_dump_json()


def test_analyst_client_accepts_the_new_codes_and_offers_nothing_to_submit(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Real HostedAgentClient over the Invocations wire format, bridged to the host."""
    import httpx
    from tests.test_hosted_agent_client import USER

    from process_optimizer.orchestration import AnalystOrchestrator, HostedAgentClient

    provider = _foundry_provider(monkeypatch, _RaisingModel(_sdk_error("PermissionDeniedError",
                                                                       403)))
    from starlette.testclient import TestClient

    from process_optimizer.agents.hosting import create_invocations_host

    host = TestClient(create_invocations_host(InvestigationRunner(analysis_dir, provider)).app)

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded = host.post("/invocations", content=request.content,
                              headers={"Content-Type": "application/json"})
        return httpx.Response(forwarded.status_code, content=forwarded.content,
                              headers={"Content-Type": "application/json"})

    client = HostedAgentClient("https://aif.example.services.ai.azure.com/api/projects/p",
                               "agent", lambda: USER, transport=httpx.MockTransport(handler))
    result = AnalystOrchestrator(client, None).investigate(_request("group_returns"))  # type: ignore[arg-type]

    assert result.final_decision == "SAFE_TERMINATION"
    assert [e.code for e in result.errors] == ["PROVIDER_AUTH_FAILED"]
    assert AnalystOrchestrator.submittable(result) == ()
    assert SECRET not in result.model_dump_json()
