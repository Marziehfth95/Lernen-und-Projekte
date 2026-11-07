"""Policy BU-03: deterministic, fail-closed per-run token budget (offline, no model)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from tests.test_agent_workflow import RUN_ID, analysis_dir  # noqa: F401 - fixture re-export
from tests.test_guardrails import _paid_environment

from process_optimizer.agents.hosting import check_host_startup
from process_optimizer.agents.provider import (
    DeterministicMockProvider,
    FoundryModelProvider,
    ModelProviderError,
    _report_usage,
    capture_model_usage,
)
from process_optimizer.agents.schemas import InvestigationRequestV1, InvestigationStateV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.guardrails import Decision, GuardrailEngine, PolicyLoadError, load_policy

LIMITS = load_policy().hosted_runtime
RESERVATION = LIMITS.call_reservation_tokens  # 10000 + 2048


def _request(scenario: str = "group_returns", mode: str = "real_model") -> InvestigationRequestV1:
    return InvestigationRequestV1(
        analysis_run_id=RUN_ID,
        investigation_goal="Synthetic token budget test.",
        execution_mode=mode,
        scenario=scenario,
        iteration_budget=3,
        tool_call_budget=6,
    )


def _final_state(runner: InvestigationRunner, request: InvestigationRequestV1) -> Any:
    raw = runner.graph.invoke(runner.build_initial_state(request).model_dump(mode="python"))
    return InvestigationStateV1.model_validate(raw)


class _ReportingProvider(DeterministicMockProvider):
    """Real-mode double that reports a fixed service usage per call."""

    mode = "real_model"

    def __init__(self, tokens: int | None) -> None:
        self.tokens = tokens
        self.calls = 0

    def _spend(self) -> None:
        self.calls += 1
        _report_usage(self.tokens)

    def select_findings(self, *args: Any, **kwargs: Any) -> Any:
        self._spend()
        return super().select_findings(*args, **kwargs)

    def plan_investigation(self, *args: Any, **kwargs: Any) -> Any:
        self._spend()
        return super().plan_investigation(*args, **kwargs)

    def propose_optimizations(self, *args: Any, **kwargs: Any) -> Any:
        self._spend()
        return super().propose_optimizations(*args, **kwargs)


class _FakeChatModel:
    """Stand-in for the Foundry chat model with configurable usage and failures."""

    def __init__(self, *, usage: dict[str, int] | None = None, fail: bool = False) -> None:
        self.usage = usage
        self.fail = fail
        self.invocations = 0

    def with_structured_output(self, *args: Any, **kwargs: Any) -> _FakeChatModel:
        return self

    def invoke(self, messages: Any, config: Any) -> dict[str, Any]:
        del messages, config
        self.invocations += 1
        if self.fail:
            raise ConnectionError("synthetic transport failure")
        raw = type("Raw", (), {"usage_metadata": self.usage, "response_metadata": {}})()
        return {"parsed": None, "raw": raw}


def _foundry(monkeypatch: pytest.MonkeyPatch, model: _FakeChatModel) -> FoundryModelProvider:
    monkeypatch.setattr(
        FoundryModelProvider, "_build_model", staticmethod(lambda timeout_seconds: model)
    )
    return FoundryModelProvider()


# ---------------------------------------------------------------- engine rule


def test_reservation_must_fit_the_remaining_budget_exactly() -> None:
    engine = GuardrailEngine()
    fits = engine.check_model_call(
        calls_used=0, max_calls=6, deadline_epoch_seconds=None,
        tokens_used=LIMITS.max_run_tokens - RESERVATION,
    )
    short_by_one = engine.check_model_call(
        calls_used=0, max_calls=6, deadline_epoch_seconds=None,
        tokens_used=LIMITS.max_run_tokens - RESERVATION + 1,
    )
    assert fits.decision == Decision.ALLOW
    assert short_by_one.reason_codes == ("MODEL_TOKEN_BUDGET_EXHAUSTED",)


@pytest.mark.parametrize("token_budget", [10**9, LIMITS.max_run_tokens + 1])
def test_engine_never_widens_the_policy_budget(token_budget: int) -> None:
    decision = GuardrailEngine().check_model_call(
        calls_used=0, max_calls=6, deadline_epoch_seconds=None,
        tokens_used=LIMITS.max_run_tokens - RESERVATION + 1, token_budget=token_budget,
    )
    assert decision.blocked


def test_zero_or_negative_values_fail_closed() -> None:
    engine = GuardrailEngine()
    assert engine.check_model_call(
        calls_used=0, max_calls=6, deadline_epoch_seconds=None, token_budget=0
    ).blocked
    assert engine.check_model_call(
        calls_used=0, max_calls=6, deadline_epoch_seconds=None, tokens_used=-1
    ).blocked


def test_policy_budget_smaller_than_one_reservation_is_rejected(tmp_path: Path) -> None:
    from importlib import resources

    data = json.loads(
        resources.files("process_optimizer.guardrails").joinpath("policy.v1.json").read_text()
    )
    data["hosted_runtime"]["max_run_tokens"] = RESERVATION - 1
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PolicyLoadError):
        load_policy(path)


# -------------------------------------------------------- accounting in the graph


def test_reported_usage_is_charged_exactly(analysis_dir: Path) -> None:  # noqa: F811
    provider = _ReportingProvider(tokens=1_500)
    state = _final_state(InvestigationRunner(analysis_dir, provider), _request())

    assert state.final_decision == "VALIDATED_RECOMMENDATIONS"
    assert state.model_tokens_used == 1_500 * provider.calls
    assert state.model_calls_used == provider.calls


def test_budget_exhaustion_stops_before_the_next_call(analysis_dir: Path) -> None:  # noqa: F811
    provider = _ReportingProvider(tokens=15_000)  # 2 calls = 30000; 30000 + 12048 > 40000
    state = _final_state(InvestigationRunner(analysis_dir, provider), _request())

    assert provider.calls == 2
    assert state.model_tokens_used == 30_000
    assert state.errors[-1].code == "MODEL_TOKEN_BUDGET_EXHAUSTED"
    assert state.final_decision == "SAFE_TERMINATION"
    event = state.audit_events[-2]
    assert event.event_type == "budget_exhausted"
    assert event.safe_details["guardrail_reason_codes"] == "MODEL_TOKEN_BUDGET_EXHAUSTED"


def test_missing_usage_is_charged_the_full_reservation(analysis_dir: Path) -> None:  # noqa: F811
    provider = _ReportingProvider(tokens=None)  # service returned no usage metadata
    state = _final_state(InvestigationRunner(analysis_dir, provider), _request())

    # 3 x 12048 = 36144; a 4th reservation would exceed 40000.
    assert provider.calls == 3
    assert state.model_tokens_used == 3 * RESERVATION
    assert state.errors[-1].code == "MODEL_TOKEN_BUDGET_EXHAUSTED"


def test_provider_that_reports_nothing_is_treated_as_unknown_usage(
    analysis_dir: Path,  # noqa: F811
) -> None:
    class Silent(DeterministicMockProvider):
        mode = "real_model"

    state = _final_state(InvestigationRunner(analysis_dir, Silent()), _request())
    assert state.model_tokens_used == 3 * RESERVATION
    assert state.errors[-1].code == "MODEL_TOKEN_BUDGET_EXHAUSTED"


def test_mock_mode_is_never_charged(analysis_dir: Path) -> None:  # noqa: F811
    state = _final_state(
        InvestigationRunner(analysis_dir, DeterministicMockProvider()),
        _request(mode="mocked_llm"),
    )
    assert state.model_tokens_used == 0
    assert state.final_decision == "VALIDATED_RECOMMENDATIONS"


# -------------------------------------------------------------- model errors


def test_invalid_reply_with_usage_is_charged_its_usage(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    model = _FakeChatModel(usage={"input_tokens": 700, "output_tokens": 77, "total_tokens": 777})
    runner = InvestigationRunner(analysis_dir, _foundry(monkeypatch, model))
    state = _final_state(runner, _request())

    assert model.invocations == 1
    assert state.errors[-1].code == "MODEL_OUTPUT_INVALID"
    assert state.model_tokens_used == 777
    assert state.model_calls_used == 1


def test_usage_without_total_is_summed_from_input_and_output(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    model = _FakeChatModel(usage={"input_tokens": 400, "output_tokens": 50})
    runner = InvestigationRunner(analysis_dir, _foundry(monkeypatch, model))
    state = _final_state(runner, _request())
    assert state.model_tokens_used == 450


def test_transport_failure_is_charged_the_full_reservation(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    model = _FakeChatModel(fail=True)
    runner = InvestigationRunner(analysis_dir, _foundry(monkeypatch, model))
    state = _final_state(runner, _request())

    assert model.invocations == 1
    # A transport failure is not a model answer and must not be reported as one.
    assert state.errors[-1].code == "PROVIDER_CONNECTION_FAILED"
    assert state.model_tokens_used == RESERVATION


def test_request_larger_than_its_reservation_is_never_sent(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    model = _FakeChatModel(usage={"total_tokens": 1})
    provider = _foundry(monkeypatch, model)
    monkeypatch.setattr(
        "process_optimizer.agents.provider._estimate_request_tokens",
        lambda *args: LIMITS.max_input_tokens_per_call + LIMITS.max_output_tokens + 1,
    )

    state = _final_state(InvestigationRunner(analysis_dir, provider), _request())

    assert model.invocations == 0
    assert state.model_tokens_used == 0  # nothing was sent, nothing is charged
    assert provider.invocation_records[-1]["error_category"] == "MODEL_CALL_EXCEEDS_RESERVATION"


def test_output_cap_above_policy_is_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _FakeChatModel(usage={"total_tokens": 1})
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "4096")
    provider = _foundry(monkeypatch, model)
    with capture_model_usage() as usage, pytest.raises(ModelProviderError, match="reservation"):
        provider.select_findings("goal", "custom", _contract())
    assert model.invocations == 0
    assert usage.reports == [0]


def test_unset_output_cap_defaults_to_the_policy_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from process_optimizer.agents.provider import effective_max_output_tokens

    monkeypatch.delenv("MODEL_MAX_OUTPUT_TOKENS", raising=False)
    assert effective_max_output_tokens() == LIMITS.max_output_tokens


# ------------------------------------------------------ widening configuration


def test_runner_clamps_wider_token_budgets(
    analysis_dir: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv("MODEL_RUN_TOKEN_BUDGET", "999999")
    assert InvestigationRunner(analysis_dir)._run_token_budget == LIMITS.max_run_tokens
    monkeypatch.delenv("MODEL_RUN_TOKEN_BUDGET")
    assert (
        InvestigationRunner(analysis_dir, run_token_budget=10**7)._run_token_budget
        == LIMITS.max_run_tokens
    )
    assert InvestigationRunner(analysis_dir, run_token_budget=20_000)._run_token_budget == 20_000


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("<unset>", "MODEL_RUN_TOKEN_BUDGET_NOT_EXPLICIT"),
        ("40001", "MODEL_RUN_TOKEN_BUDGET_OUT_OF_POLICY"),
        ("0", "MODEL_RUN_TOKEN_BUDGET_OUT_OF_POLICY"),
        ("-5", "MODEL_RUN_TOKEN_BUDGET_NOT_EXPLICIT"),
    ],
)
def test_host_refuses_missing_or_wider_token_budget(value: str, code: str) -> None:
    with pytest.raises(RuntimeError, match=code):
        check_host_startup(_paid_environment(MODEL_RUN_TOKEN_BUDGET=value))


def test_other_limits_are_unchanged_by_the_token_budget() -> None:
    assert (LIMITS.max_model_calls_per_run, LIMITS.max_model_retries) == (6, 0)
    assert LIMITS.run_deadline_seconds == 240
    assert (LIMITS.max_run_tokens, RESERVATION) == (40_000, 12_048)


# -------------------------------------------------------------- isolation


def test_usage_capture_is_isolated_between_concurrent_runs() -> None:
    results: dict[str, list[int | None]] = {}
    barrier = threading.Barrier(2)

    def work(name: str, tokens: int) -> None:
        with capture_model_usage() as usage:
            barrier.wait()
            _report_usage(tokens)
            barrier.wait()
            results[name] = list(usage.reports)

    threads = [threading.Thread(target=work, args=(n, t)) for n, t in (("a", 1), ("b", 2))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {"a": [1], "b": [2]}


def _contract() -> Any:
    from tests.test_agent_workflow import _contract as contract

    return contract()
