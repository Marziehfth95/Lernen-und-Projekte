"""Authenticated fixed-route client for the cloud aggregate API."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.simulation import SimulationRequestV1
from process_optimizer.tools.analytics import (
    AggregateToolResult,
    BottleneckFindingsInput,
    CohortComparisonInput,
    EvidenceDetailsInput,
    GroupHandoffsInput,
    LifecyclePatternsInput,
    ProcessOverviewInput,
)

TokenProvider = Callable[[], str]


class ManagedIdentityApiTokenProvider:
    """Acquire a short-lived application token using the hosted agent identity."""

    def __init__(self, scope: str, client_id: str | None = None) -> None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as error:
            raise RuntimeError("Install the project cloud extra for managed identity") from error
        self._scope = scope
        self._credential = DefaultAzureCredential(managed_identity_client_id=client_id)

    def __call__(self) -> str:
        return self._credential.get_token(self._scope).token


class AuthenticatedAggregateApiTools:
    """Expose only the seven approved aggregate operations over HTTPS."""

    _ROUTES = {
        "get_bottleneck_findings": "bottleneck-findings",
        "analyze_group_handoffs": "group-handoffs",
        "compare_process_cohorts": "cohort-comparison",
        "inspect_lifecycle_patterns": "lifecycle-patterns",
        "get_evidence_details": "evidence-details",
        "simulate_group_transition_reduction": "simulate-group-transition-reduction",
    }

    def __init__(
        self,
        base_url: str,
        token_provider: TokenProvider,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Aggregate API base URL must use HTTPS")
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    @classmethod
    def from_environment(cls) -> AuthenticatedAggregateApiTools:
        base_url = os.environ.get("AGGREGATE_API_BASE_URL", "")
        scope = os.environ.get("AGGREGATE_API_SCOPE", "")
        if not base_url or not scope:
            raise ValueError("Aggregate API URL and scope are required")
        # App-only managed/agent identity tokens use the client-credentials
        # flow, which only accepts the resource's `/.default` scope.
        if not scope.endswith("/.default"):
            raise ValueError("Aggregate API scope must be an application '/.default' scope")
        return cls(
            base_url,
            ManagedIdentityApiTokenProvider(
                scope,
                os.environ.get("AZURE_CLIENT_ID") or None,
            ),
        )

    def get_process_overview(self, request: ProcessOverviewInput) -> AggregateToolResult:
        del request
        return self._request("GET", "process-overview")

    def get_evidence_contract(self) -> EvidenceContractV1:
        """Bootstrap the graph with its immutable aggregate evidence contract."""
        try:
            response = self._client.get(
                f"{self._base_url}/aggregate/v1/evidence-contract",
                headers={"Authorization": f"Bearer {self._token_provider()}"},
            )
            response.raise_for_status()
            return EvidenceContractV1.model_validate(response.json()["evidence"])
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise ValueError("Authenticated evidence contract retrieval failed") from error

    def get_guardrail_policy_metadata(self) -> Any:
        """Return the Workflow API's policy identity, or None if the route does not exist.

        The result is untrusted; the guardrail engine validates it. Any other failure
        raises, and the host then refuses to start.
        """
        try:
            response = self._client.get(
                f"{self._base_url}/aggregate/v1/guardrail-policy",
                headers={"Authorization": f"Bearer {self._token_provider()}"},
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ValueError("Authenticated guardrail policy retrieval failed") from error
        return body.get("guardrail_policy") if isinstance(body, dict) else body

    def get_bottleneck_findings(
        self, request: BottleneckFindingsInput
    ) -> AggregateToolResult:
        return self._post("get_bottleneck_findings", request)

    def analyze_group_handoffs(self, request: GroupHandoffsInput) -> AggregateToolResult:
        return self._post("analyze_group_handoffs", request)

    def compare_process_cohorts(
        self, request: CohortComparisonInput
    ) -> AggregateToolResult:
        return self._post("compare_process_cohorts", request)

    def inspect_lifecycle_patterns(
        self, request: LifecyclePatternsInput
    ) -> AggregateToolResult:
        return self._post("inspect_lifecycle_patterns", request)

    def get_evidence_details(self, request: EvidenceDetailsInput) -> AggregateToolResult:
        return self._post("get_evidence_details", request)

    def simulate_group_transition_reduction(
        self, request: SimulationRequestV1
    ) -> AggregateToolResult:
        return self._post("simulate_group_transition_reduction", request)

    def _post(self, operation: str, request: Any) -> AggregateToolResult:
        return self._request(
            "POST",
            self._ROUTES[operation],
            json=request.model_dump(mode="json"),
        )

    def _request(self, method: str, route: str, **kwargs: Any) -> AggregateToolResult:
        try:
            response = self._client.request(
                method,
                f"{self._base_url}/aggregate/v1/{route}",
                headers={"Authorization": f"Bearer {self._token_provider()}"},
                **kwargs,
            )
            response.raise_for_status()
            return AggregateToolResult.model_validate(response.json()["result"])
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise ValueError("Authenticated aggregate API operation failed") from error
