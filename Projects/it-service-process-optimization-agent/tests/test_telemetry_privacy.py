"""Hosted Agent telemetry cannot carry raw SDK exception text (offline)."""

from __future__ import annotations

import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.test_agent_workflow import _foundry_provider, analysis_dir  # noqa: F401
from tests.test_provider_error_taxonomy import CASES, SECRET, _RaisingModel

from process_optimizer.agents import telemetry_privacy
from process_optimizer.agents.provider import ModelProviderError
from process_optimizer.agents.provider_errors import classify_provider_exception
from process_optimizer.agents.workflow import InvestigationRunner


def test_every_installed_auto_instrumentor_is_disabled() -> None:
    options = telemetry_privacy.disabled_instrumentation_options()

    assert set(options) == set(telemetry_privacy.installed_instrumentation_names())
    assert all(value == {"enabled": False} for value in options.values())
    assert {"langchain", "openai"} <= set(options)


def test_sensitive_data_recording_cannot_be_reenabled(monkeypatch: pytest.MonkeyPatch) -> None:
    core = pytest.importorskip("azure.ai.agentserver.core")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(core, "configure_observability", lambda **kw: seen.update(kw))

    telemetry_privacy.configure_private_observability(
        connection_string=None,
        log_level=None,
        enable_sensitive_data=True,  # what the host passes when the env var is not "false"
        instrumentation_options={"langchain": {"enabled": True}, "custom": {"x": 1}},
    )

    assert seen["enable_sensitive_data"] is False
    assert seen["instrumentation_options"]["langchain"] == {"enabled": False}
    assert seen["instrumentation_options"]["custom"] == {"x": 1}


def test_an_active_instrumentor_refuses_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    class Active:
        is_instrumented_by_opentelemetry = True

    class Inactive:
        is_instrumented_by_opentelemetry = False

    def broken() -> Any:
        raise ImportError("library not installed")

    fake = [SimpleNamespace(name="langchain", load=lambda: Active),
            SimpleNamespace(name="httpx", load=lambda: Inactive),
            SimpleNamespace(name="django", load=broken)]
    monkeypatch.setattr(telemetry_privacy, "entry_points", lambda group: fake)

    assert telemetry_privacy.active_auto_instrumentations() == ("langchain",)
    with pytest.raises(RuntimeError, match="TELEMETRY_AUTO_INSTRUMENTATION_ACTIVE langchain"):
        telemetry_privacy.require_no_auto_instrumentation()


def test_host_creation_fails_closed_when_instrumentation_is_active(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    pytest.importorskip("langchain_azure_ai.agents.hosting")
    from process_optimizer.agents import hosting
    from process_optimizer.agents.provider import DeterministicMockProvider

    monkeypatch.setattr(telemetry_privacy, "active_auto_instrumentations", lambda: ("openai",))

    with pytest.raises(RuntimeError, match="TELEMETRY_AUTO_INSTRUMENTATION_ACTIVE openai"):
        hosting.create_invocations_host(
            InvestigationRunner(analysis_dir, DeterministicMockProvider())
        )


def test_hosted_runtime_leaves_no_auto_instrumentor_active(
    analysis_dir: Path,  # noqa: F811
) -> None:
    pytest.importorskip("langchain_azure_ai.agents.hosting")
    from process_optimizer.agents.hosting import create_invocations_host
    from process_optimizer.agents.provider import DeterministicMockProvider

    create_invocations_host(InvestigationRunner(analysis_dir, DeterministicMockProvider()))

    assert telemetry_privacy.active_auto_instrumentations() == ()


@pytest.mark.parametrize(("error", "category", "status"), CASES,
                         ids=[c[1] + "-" + type(c[0]).__name__ for c in CASES])
def test_provider_errors_do_not_chain_sdk_text(
    analysis_dir: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    category: str,
    status: int | None,
) -> None:
    provider = _foundry_provider(monkeypatch, _RaisingModel(error))
    evidence = InvestigationRunner(analysis_dir, provider)._evidence  # noqa: SLF001

    with pytest.raises(ModelProviderError) as raised:
        provider.select_findings("goal", "group_returns", evidence)

    assert raised.value.__cause__ is None and raised.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(raised.value))
    assert SECRET not in rendered
    assert raised.value.category == classify_provider_exception(error)[0] == category
