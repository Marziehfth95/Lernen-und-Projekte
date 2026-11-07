"""Microsoft Foundry Invocations boundary for the custom investigation state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from process_optimizer.agents.provider import FoundryModelProvider, foundry_model_deployment_name
from process_optimizer.agents.schemas import FrozenModel, InvestigationRequestV1
from process_optimizer.agents.telemetry_privacy import (
    configure_private_observability,
    require_no_auto_instrumentation,
)
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.guardrails import (
    EVIDENCE_CONTRACT_SHA256_ENV,
    GENAI_CONTENT_CAPTURE_ENV,
    GuardrailEngine,
    check_evidence_contract_pin,
)
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.tools.client import AuthenticatedAggregateApiTools


class HostedInvocationRequestV1(InvestigationRequestV1):
    """Custom `/invocations` request body validated before graph execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    # Strict like the SDK default parser: "yes" must not silently enable SSE.
    stream: StrictBool = False


class ReadinessStatusV1(FrozenModel):
    """Non-sensitive readiness result for hosting probes."""

    status: Literal["ready", "not_ready"]
    evidence_contract_loaded: bool
    provider_mode: Literal["mocked_llm", "real_model"]
    foundry_configuration_present: bool
    guardrail_policy_version: str | None = None


def create_invocations_host(runner: InvestigationRunner) -> Any:
    """Create the SDK host with custom request, state-input, and output adapters."""
    # The agent server enables prompt/response capture unless this is "false"; it reads
    # the variable while the host is constructed, so set the safe default first and
    # refuse an explicit opt-in rather than silently overriding it.
    os.environ.setdefault(GENAI_CONTENT_CAPTURE_ENV, "false")
    if os.environ[GENAI_CONTENT_CAPTURE_ENV] != "false":
        raise RuntimeError(f"{GENAI_CONTENT_CAPTURE_ENV} must be 'false' for this host")
    try:
        from azure.ai.agentserver.invocations import InvocationAgentServerHost
        from langchain_azure_ai.agents.hosting import InvocationsHostServer
    except ImportError as error:
        raise RuntimeError(
            "Install the project agents extra to use the Foundry Invocations host"
        ) from error

    class ProcessOptimizerInvocationsHost(InvocationsHostServer):  # type: ignore[misc]
        @staticmethod
        def _validate_graph_schema(graph: Any) -> None:
            # SDK 1.2.10 validates before dispatching to the documented build_input
            # override. This subclass owns the complete custom-state conversion.
            del graph

        async def parse_request(self, request: Any) -> tuple[str, bool]:
            # SDK 1.2.10 treats any non-string message as structured HITL resume
            # items and rejects it without a pending interrupt, so the fully
            # validated request crosses the SDK boundary as canonical JSON text.
            # Every ValueError raised here becomes a controlled HTTP 400.
            try:
                data = await request.json()
            except ValueError as error:
                raise ValueError("Request body must be a valid JSON object") from error
            parsed = _validate_invocation(HostedInvocationRequestV1, data)
            if parsed.stream:
                # SSE runs the full graph (billing model tokens) but the custom state
                # adapter emits no result events, so the caller receives nothing.
                raise ValueError("Streaming is not supported; set stream to false")
            investigation = InvestigationRequestV1.model_validate(
                parsed.model_dump(exclude={"stream"})
            )
            # Reject run-ID and execution-mode mismatches before graph dispatch.
            runner.build_initial_state(investigation)
            return investigation.model_dump_json(), False

        def build_input(self, message: str) -> dict[str, Any]:
            request = _validate_invocation(InvestigationRequestV1, message)
            return runner.build_initial_state(request).model_dump(mode="python")

        def parse_output(self, output: Any) -> str:
            return runner.build_result(output).model_dump_json()

    # Own the agent-server host so its observability setup disables all third-party
    # auto-instrumentation, which records raw SDK exception text (see telemetry_privacy).
    host = ProcessOptimizerInvocationsHost(
        runner.graph,
        app=InvocationAgentServerHost(configure_observability=configure_private_observability),
    )
    require_no_auto_instrumentation()
    return host


def _validate_invocation[RequestModel: BaseModel](
    schema: type[RequestModel], value: Any
) -> RequestModel:
    """Validate a request without echoing caller-supplied values in the error."""
    try:
        if isinstance(value, str):
            return schema.model_validate_json(value)
        return schema.model_validate(value)
    except ValidationError as error:
        fields = sorted(
            {".".join(str(part) for part in item["loc"]) or "body" for item in error.errors()}
        )
        raise ValueError(f"Invalid invocation request fields: {', '.join(fields)}") from None


def check_readiness(runner: InvestigationRunner, provider_mode: str) -> ReadinessStatusV1:
    """Check local evidence and configuration without contacting Azure or a model."""
    foundry_present = bool(
        os.environ.get("FOUNDRY_PROJECT_ENDPOINT") and foundry_model_deployment_name()
    )
    is_real = provider_mode == "real_model"
    ready = bool(runner.analysis_run_id) and (not is_real or foundry_present)
    return ReadinessStatusV1(
        status="ready" if ready else "not_ready",
        evidence_contract_loaded=bool(runner.analysis_run_id),
        provider_mode="real_model" if is_real else "mocked_llm",
        foundry_configuration_present=foundry_present,
        guardrail_policy_version=runner.guardrail_policy_version,
    )


def check_host_startup(environment: dict[str, str] | None = None) -> GuardrailEngine:
    """HOST_STARTUP guardrail: paid calls, retries, caps and deadline must be explicit.

    Runs before any credential, network or model client is created.
    """
    engine = GuardrailEngine()
    decision = engine.check_host_startup(
        environment=dict(os.environ if environment is None else environment),
        provider_mode="real_model",
    )
    if decision.blocked:
        raise RuntimeError(
            "Hosted runtime refused to start: " + ", ".join(decision.reason_codes)
        )
    return engine


def main() -> None:
    """Start the real-model Invocations host only after explicit, bounded cost opt-ins."""
    engine = check_host_startup()
    analysis_dir = Path(os.environ.get("PROCESS_OPTIMIZER_ANALYSIS_DIR", DEFAULT_ANALYSIS_DIR))
    provider = FoundryModelProvider()
    tools = AuthenticatedAggregateApiTools.from_environment()
    evidence = tools.get_evidence_contract()
    # EV-02: the agent version pins the exact contract it was reviewed against.
    if not check_evidence_contract_pin(evidence, os.environ.get(EVIDENCE_CONTRACT_SHA256_ENV)):
        raise RuntimeError("Hosted runtime refused to start: EVIDENCE_CONTRACT_SHA256_MISMATCH")
    # EV-04: refuse to serve unless the Workflow API enforces this exact policy.
    try:
        remote_policy = tools.get_guardrail_policy_metadata()
    except ValueError as error:
        raise RuntimeError(
            "Hosted runtime refused to start: GUARDRAIL_POLICY_METADATA_UNAVAILABLE"
        ) from error
    compatibility = engine.check_policy_compatibility(remote_metadata=remote_policy)
    if compatibility.blocked:
        raise RuntimeError(
            "Hosted runtime refused to start: " + ", ".join(compatibility.reason_codes)
        )
    runner = InvestigationRunner(
        analysis_dir=analysis_dir,
        provider=provider,
        tools=tools,
        evidence=evidence,
        guardrails=engine,
    )
    create_invocations_host(runner).run(port=int(os.environ.get("PORT", "8088")))


if __name__ == "__main__":
    main()
