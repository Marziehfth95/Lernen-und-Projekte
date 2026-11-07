"""Authenticated, read-only HTTP boundary for verified aggregate analytics."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from process_optimizer.api.workflow import PrincipalDependency
from process_optimizer.guardrails import GuardrailEngine
from process_optimizer.guardrails.compatibility import (
    GuardrailPolicyMetadataV1,
    policy_metadata,
)
from process_optimizer.observability import record_operational_event
from process_optimizer.process_mining.evidence_contract import EvidenceContractV1
from process_optimizer.security.auth import (
    AuthorizationError,
    Permission,
    Principal,
    require_permission,
)
from process_optimizer.simulation import SimulationRequestV1
from process_optimizer.tools.analytics import (
    AggregateAnalyticsTools,
    AggregateToolResult,
    BottleneckFindingsInput,
    CohortComparisonInput,
    EvidenceDetailsInput,
    GroupHandoffsInput,
    LifecyclePatternsInput,
    ProcessOverviewInput,
)
from process_optimizer.tools.manifest import AggregateArtifactVerifier

router = APIRouter(prefix="/aggregate/v1", tags=["aggregate analytics"])


class AggregateApiResponseV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str
    analysis_run_id: str
    evidence_contract_version: str
    result: AggregateToolResult


class EvidenceContractApiResponseV1(BaseModel):
    """Aggregate-only contract bootstrap response for the hosted graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str
    evidence: EvidenceContractV1


class GuardrailPolicyApiResponseV1(BaseModel):
    """Policy identity only; the policy's patterns and limits are never served."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str
    guardrail_policy: GuardrailPolicyMetadataV1


class VerifiedAggregateService:
    """Verify package integrity before invoking the existing read-only tools."""

    def __init__(self, verifier: AggregateArtifactVerifier) -> None:
        self._verifier = verifier
        self.evidence = verifier.verify()
        self.analysis_run_id = self.evidence.analysis_run_id
        self.contract_version = self.evidence.schema_version
        guardrails = GuardrailEngine()
        # The same packaged policy WorkflowService enforces at submission (EV-04).
        self.guardrail_policy = guardrails.policy
        # The served contract omits findings that disclose a small group, so the
        # bootstrap route cannot bypass the suppression the tool routes apply.
        visible = guardrails.visible_finding_ids(self.evidence.findings)
        self.served_evidence = self.evidence.model_copy(
            update={
                "findings": tuple(
                    item for item in self.evidence.findings if item.finding_id in visible
                )
            }
        )
        self.tools = AggregateAnalyticsTools(
            verifier.analysis_dir,
            small_group_threshold=guardrails.policy.small_group_threshold,
        )

    def verify(self) -> None:
        self._verifier.verify()


def _aggregate_service(request: Request) -> VerifiedAggregateService:
    service = getattr(request.app.state, "aggregate_service", None)
    if service is not None:
        return service
    try:
        analysis_dir = Path(os.environ["PROCESS_OPTIMIZER_ANALYSIS_DIR"])
        manifest_path = Path(os.environ["AGGREGATE_MANIFEST_PATH"])
        service = VerifiedAggregateService(
            AggregateArtifactVerifier.from_file(analysis_dir, manifest_path)
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AGGREGATE_PACKAGE_NOT_READY"},
        ) from error
    request.app.state.aggregate_service = service
    return service


AggregateServiceDependency = Annotated[VerifiedAggregateService, Depends(_aggregate_service)]


def _authorize(principal: Principal, permission: Permission) -> None:
    try:
        require_permission(principal, permission)
    except AuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INSUFFICIENT_PERMISSION"},
        ) from error


def _response(
    request: Request,
    service: VerifiedAggregateService,
    result: AggregateToolResult,
) -> AggregateApiResponseV1:
    return AggregateApiResponseV1(
        correlation_id=request.state.correlation_id,
        analysis_run_id=service.analysis_run_id,
        evidence_contract_version=service.contract_version,
        result=result,
    )


def _execute(
    request: Request,
    service: VerifiedAggregateService,
    principal: Principal,
    operation: Callable[[object], AggregateToolResult],
    body: object,
    *,
    permission: Permission = Permission.AGGREGATE_READ,
) -> AggregateApiResponseV1:
    _authorize(principal, permission)
    try:
        service.verify()
        result = operation(body)
        record_operational_event(
            "aggregate_tool_invocation",
            result="success",
            correlation_id=request.state.correlation_id,
            attributes={"returned_count": result.returned_count},
        )
        return _response(request, service, result)
    except HTTPException:
        raise
    except Exception as error:
        record_operational_event(
            "aggregate_tool_invocation",
            result="error",
            correlation_id=request.state.correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AGGREGATE_OPERATION_UNAVAILABLE"},
        ) from error


@router.get("/evidence-contract", response_model=EvidenceContractApiResponseV1)
def evidence_contract(
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> EvidenceContractApiResponseV1:
    """Return the immutable aggregate contract; never raw case or event records."""
    _authorize(principal, Permission.AGGREGATE_READ)
    try:
        service.verify()
        return EvidenceContractApiResponseV1(
            correlation_id=request.state.correlation_id,
            evidence=service.served_evidence,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AGGREGATE_OPERATION_UNAVAILABLE"},
        ) from error


@router.get("/guardrail-policy", response_model=GuardrailPolicyApiResponseV1)
def guardrail_policy(
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> GuardrailPolicyApiResponseV1:
    """EV-04: identify the enforced guardrail policy for the agent's startup check."""
    _authorize(principal, Permission.AGGREGATE_READ)
    return GuardrailPolicyApiResponseV1(
        correlation_id=request.state.correlation_id,
        guardrail_policy=policy_metadata(service.guardrail_policy),
    )


@router.get("/process-overview", response_model=AggregateApiResponseV1)
def process_overview(
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(
        request, service, principal, service.tools.get_process_overview, ProcessOverviewInput()
    )


@router.post("/bottleneck-findings", response_model=AggregateApiResponseV1)
def bottleneck_findings(
    body: BottleneckFindingsInput,
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(request, service, principal, service.tools.get_bottleneck_findings, body)


@router.post("/group-handoffs", response_model=AggregateApiResponseV1)
def group_handoffs(
    body: GroupHandoffsInput,
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(request, service, principal, service.tools.analyze_group_handoffs, body)


@router.post("/cohort-comparison", response_model=AggregateApiResponseV1)
def cohort_comparison(
    body: CohortComparisonInput,
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(request, service, principal, service.tools.compare_process_cohorts, body)


@router.post("/lifecycle-patterns", response_model=AggregateApiResponseV1)
def lifecycle_patterns(
    body: LifecyclePatternsInput,
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(request, service, principal, service.tools.inspect_lifecycle_patterns, body)


@router.post("/evidence-details", response_model=AggregateApiResponseV1)
def evidence_details(
    body: EvidenceDetailsInput,
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(request, service, principal, service.tools.get_evidence_details, body)


@router.post("/simulate-group-transition-reduction", response_model=AggregateApiResponseV1)
def simulate_group_transition_reduction(
    body: SimulationRequestV1,
    request: Request,
    principal: PrincipalDependency,
    service: AggregateServiceDependency,
) -> AggregateApiResponseV1:
    return _execute(
        request,
        service,
        principal,
        service.tools.simulate_group_transition_reduction,
        body,
        permission=Permission.SIMULATION_EXECUTE,
    )
