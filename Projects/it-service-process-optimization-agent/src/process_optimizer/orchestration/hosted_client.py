"""Analyst client: invoke the Hosted Agent, show the result, submit only on confirmation.

Both calls use the analyst's own delegated Entra token. The agent identity never reaches
`/workflow`, this client never submits automatically, and it has no approve operation:
approval stays a separate PROCESS_OWNER action on the workflow API.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from process_optimizer.agents.schemas import (
    FinalDecision,
    InvestigationRequestV1,
    InvestigationResultV1,
    RecommendationCandidate,
    RecommendationStatus,
)
from process_optimizer.workflow.schemas import (
    ApprovalDetailV1,
    Priority,
    RecommendationSubmissionV1,
)

TokenProvider = Callable[[], str]
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
# Endpoint contract adopted 2026-10-30 (architecture decision 3):
#   {project_endpoint}/agents/{agent_name}/endpoint/protocols/invocations?api-version=v1
DEFAULT_INVOCATIONS_PATH = "/agents/{agent}/endpoint/protocols/invocations"
DEFAULT_API_VERSION = "v1"


class HostedAgentError(RuntimeError):
    """Safe agent-call failure; carries the HTTP status only, never response content."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HostedAgentAuthorizationError(HostedAgentError):
    """401/403: the analyst lacks a valid token or the agent-level invoke role."""


class HostedAgentTimeoutError(HostedAgentError):
    """The invocation did not complete within the client timeout."""


class HostedAgentResponseError(HostedAgentError):
    """The agent answered, but not with a result consistent with the request."""


class WorkflowApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ConfirmationRequiredError(RuntimeError):
    """Raised when submission lacks the analyst's explicit, matching confirmation."""


class DelegatedUserTokenProvider:
    """Delegated (user) token from the signed-in Azure CLI account or a browser login."""

    def __init__(self, scope: str, *, interactive: bool = False) -> None:
        try:
            from azure.identity import AzureCliCredential, InteractiveBrowserCredential
        except ImportError as error:
            raise RuntimeError("Install the project agents or cloud extra") from error
        self._scope = scope
        self._credential = InteractiveBrowserCredential() if interactive else AzureCliCredential()

    def __call__(self) -> str:
        return self._credential.get_token(self._scope).token


def require_delegated_token(token: str, *, now: float | None = None) -> str:
    """Refuse app-only or expired tokens client-side; servers still validate fully.

    The signature is not checked here (the client holds no keys); this only prevents a
    service identity or a stale token from being used by mistake.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as error:
        raise PermissionError("A delegated user access token is required") from error
    if not isinstance(claims, dict) or not claims.get("scp") or claims.get("idtyp") == "app":
        raise PermissionError("A delegated user access token is required")
    expires = claims.get("exp")
    if isinstance(expires, int | float) and expires <= (time.time() if now is None else now):
        raise PermissionError("The delegated user access token has expired")
    return token


class HostedAgentClient:
    """POST one non-streaming invocation and validate the typed result."""

    def __init__(
        self,
        project_endpoint: str,
        agent_name: str,
        token_provider: TokenProvider,
        *,
        api_version: str = DEFAULT_API_VERSION,
        invocations_path: str = DEFAULT_INVOCATIONS_PATH,
        timeout_seconds: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not project_endpoint.startswith("https://"):
            raise ValueError("Foundry project endpoint must use HTTPS")
        self._url = project_endpoint.rstrip("/") + invocations_path.format(
            agent=quote(agent_name, safe="")
        )
        self._params = {"api-version": api_version}
        self._token_provider = token_provider
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    @classmethod
    def for_signed_in_analyst(
        cls, project_endpoint: str, agent_name: str, *, interactive: bool = False
    ) -> HostedAgentClient:
        """Use the analyst's own Entra sign-in for the Foundry data plane."""
        return cls(
            project_endpoint,
            agent_name,
            DelegatedUserTokenProvider(FOUNDRY_SCOPE, interactive=interactive),
        )

    @property
    def url(self) -> str:
        return self._url

    def invoke(self, request: InvestigationRequestV1) -> InvestigationResultV1:
        """One invocation. This method never submits anything to the workflow API."""
        body = {**request.model_dump(mode="json"), "stream": False}
        token = require_delegated_token(self._token_provider())
        try:
            response = self._client.post(
                self._url,
                params=dict(self._params),
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException as error:
            raise HostedAgentTimeoutError("Hosted agent invocation timed out") from error
        except httpx.HTTPError as error:
            raise HostedAgentError("Hosted agent invocation failed") from error
        if response.status_code in {401, 403}:
            raise HostedAgentAuthorizationError(
                "Not authorized to invoke this agent (token or agent-level role)",
                response.status_code,
            )
        if response.status_code != 200:
            raise HostedAgentError("Hosted agent rejected the invocation", response.status_code)
        try:
            envelope = response.json()
            payload = envelope["response"] if isinstance(envelope, dict) else None
            if isinstance(payload, str):
                result = InvestigationResultV1.model_validate_json(payload)
            else:
                result = InvestigationResultV1.model_validate(payload)
        except (ValueError, KeyError, ValidationError) as error:
            raise HostedAgentResponseError(
                "Hosted agent returned an invalid result", 200
            ) from error
        _check_consistent(request, result)
        return result


class WorkflowApiClient:
    """Submit-only workflow client; there is deliberately no approve or reject method."""

    def __init__(
        self,
        base_url: str,
        token_provider: TokenProvider,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Workflow API base URL must use HTTPS")
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def submit(self, submission: RecommendationSubmissionV1) -> ApprovalDetailV1:
        token = require_delegated_token(self._token_provider())
        try:
            response = self._client.post(
                f"{self._base_url}/workflow/recommendations/submit",
                json=submission.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            raise WorkflowApiError("Workflow submission failed") from error
        if response.status_code != 201:
            raise WorkflowApiError(
                "Workflow API rejected the submission",
                response.status_code,
                _error_code(response),
            )
        return ApprovalDetailV1.model_validate(response.json())


class AnalystOrchestrator:
    """Invoke → present → explicit confirmation → submit with the analyst's token."""

    def __init__(self, agent: HostedAgentClient, workflow: WorkflowApiClient) -> None:
        self._agent = agent
        self._workflow = workflow

    def investigate(self, request: InvestigationRequestV1) -> InvestigationResultV1:
        return self._agent.invoke(request)

    @staticmethod
    def submittable(result: InvestigationResultV1) -> tuple[RecommendationCandidate, ...]:
        """Recommendations the analyst may submit; any doubt yields none."""
        if result.final_decision != FinalDecision.VALIDATED_RECOMMENDATIONS:
            return ()
        valid_ids = {item.recommendation_id for item in result.validation_results if item.valid}
        return tuple(
            item
            for item in result.recommendations
            if item.status == RecommendationStatus.VALIDATED
            and item.recommendation_id in valid_ids
            and item.requires_human_approval is True
        )

    def submit_reviewed(
        self,
        result: InvestigationResultV1,
        recommendation_id: str,
        *,
        confirmation: str | None,
        proposed_owner: str,
        priority: Priority,
        idempotency_key: str,
        correlation_id: str,
    ) -> ApprovalDetailV1:
        """Submit one reviewed recommendation; the analyst must retype its exact ID."""
        if confirmation != recommendation_id:
            raise ConfirmationRequiredError(
                "Submission requires the analyst to confirm the exact recommendation ID"
            )
        candidates = {item.recommendation_id: item for item in self.submittable(result)}
        candidate = candidates.get(recommendation_id)
        if candidate is None:
            raise ConfirmationRequiredError("Recommendation is not a validated, submittable result")
        submission = RecommendationSubmissionV1(
            analysis_run_id=result.analysis_run_id,
            recommendation=candidate,
            tool_call_records=result.tool_call_records,
            proposed_owner=proposed_owner,
            priority=priority,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        return self._workflow.submit(submission)


def _check_consistent(request: InvestigationRequestV1, result: InvestigationResultV1) -> None:
    """Reject a well-formed result that does not answer this request."""
    expected_label = (
        "REAL_MODEL_OUTPUT" if request.execution_mode == "real_model"
        else "DETERMINISTIC_MOCK_NOT_REAL_AI"
    )
    if (
        result.analysis_run_id != request.analysis_run_id
        or result.investigation_goal != request.investigation_goal
        or result.execution_label != expected_label
        or result.iterations_used > request.iteration_budget
        or result.tool_calls_used > request.tool_call_budget
    ):
        raise HostedAgentResponseError("Hosted agent result does not match the request", 200)


def _error_code(response: httpx.Response) -> str | None:
    try:
        detail: Any = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else None
