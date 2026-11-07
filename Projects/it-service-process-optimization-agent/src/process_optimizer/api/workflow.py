"""Typed, authenticated REST boundary for the durable workflow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from process_optimizer.persistence import database_from_environment
from process_optimizer.persistence.cloud import persistence_mode
from process_optimizer.persistence.database import INITIAL_SCHEMA_VERSION
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.security.auth import (
    AuthenticationAdapter,
    AuthenticationError,
    AuthorizationError,
    EntraAuthenticationAdapter,
    EntraAuthenticationSettings,
    FailClosedAuthenticationAdapter,
    LocalAuthenticationAdapter,
    Permission,
    Principal,
    Role,
    require_permission,
    require_role,
)
from process_optimizer.workflow.schemas import (
    ApprovalDetailV1,
    AuditEventV1,
    DecisionRequestV1,
    ImprovementTicketV1,
    RecommendationSubmissionV1,
    WorkflowConfigurationV1,
)
from process_optimizer.workflow.service import (
    GuardrailBlockedError,
    RecommendationValidationError,
    ResourceAuthorizationError,
    RevisionRequiredError,
    SelfApprovalError,
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowService,
)
from process_optimizer.workflow.state import ApprovalState, InvalidTransitionError

router = APIRouter(prefix="/workflow", tags=["workflow"])
bearer = HTTPBearer(auto_error=False)


def _auth_adapter(request: Request) -> AuthenticationAdapter:
    adapter = getattr(request.app.state, "authentication_adapter", None)
    if adapter is not None:
        return adapter
    environment = os.environ.get("APP_ENV", "local").lower()
    if environment == "local":
        configured = os.environ.get("LOCAL_AUTH_IDENTITIES_JSON", "{}")
        try:
            adapter = LocalAuthenticationAdapter.from_json(configured)
        except (ValueError, TypeError):
            adapter = FailClosedAuthenticationAdapter()
    elif environment == "production":
        try:
            adapter = EntraAuthenticationAdapter(
                EntraAuthenticationSettings.from_environment()
            )
        except (ValueError, RuntimeError):
            adapter = FailClosedAuthenticationAdapter()
    else:
        adapter = FailClosedAuthenticationAdapter()
    request.app.state.authentication_adapter = adapter
    return adapter


def _service(request: Request) -> WorkflowService:
    service = getattr(request.app.state, "workflow_service", None)
    if service is not None:
        return service
    try:
        database_url = os.environ.get(
            "WORKFLOW_DATABASE_URL", "sqlite:///data/local/workflow.sqlite3"
        )
        if database_url.startswith("sqlite:///") and database_url != "sqlite:///:memory:":
            Path(database_url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )
        database = database_from_environment()
        analysis_dir = Path(
            os.environ.get("PROCESS_OPTIMIZER_ANALYSIS_DIR", str(DEFAULT_ANALYSIS_DIR))
        )
        # Durable PostgreSQL is migrated only by the migration identity (bootstrap SQL
        # grants the runtime no DDL); the runtime verifies the schema and never alters it.
        service = WorkflowService.from_analysis_dir(
            database,
            analysis_dir,
            apply_migrations=persistence_mode() != "durable_postgresql",
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "WORKFLOW_NOT_CONFIGURED"},
        ) from error
    request.app.state.workflow_service = service
    return service


def _principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    try:
        token = (
            credentials.credentials
            if credentials and credentials.scheme.lower() == "bearer"
            else None
        )
        if (
            token is None
            and os.environ.get("APP_ENV", "local").lower() == "production"
            and os.environ.get("CONTAINER_APPS_AUTH_ENABLED", "false").lower() == "true"
        ):
            # Container Apps Easy Auth may forward the original signed access token via
            # its token-store header. It is still fully validated below; principal headers
            # such as X-MS-CLIENT-PRINCIPAL are never trusted.
            token = request.headers.get("x-ms-token-aad-access-token")
        return _auth_adapter(request).authenticate(token)
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTHENTICATION_REQUIRED"},
        ) from error


def _authorize(
    principal: Principal,
    *roles: Role,
    permissions: tuple[Permission, ...] = (),
) -> None:
    if principal.identity_type == "service":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "HUMAN_IDENTITY_REQUIRED"},
        )
    try:
        require_role(principal, *roles)
        if principal.identity_type == "human" and permissions:
            require_permission(principal, *permissions)
    except AuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INSUFFICIENT_ROLE"},
        ) from error


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, WorkflowNotFoundError):
        return HTTPException(status_code=404, detail={"code": error.code})
    if isinstance(error, (SelfApprovalError, ResourceAuthorizationError)):
        return HTTPException(status_code=403, detail={"code": error.code})
    if isinstance(error, (RecommendationValidationError, GuardrailBlockedError)):
        return HTTPException(status_code=422, detail={"code": error.code})
    if isinstance(error, RevisionRequiredError):
        # Codes only; the recommendation text is never echoed.
        return HTTPException(
            status_code=409,
            detail={"code": error.code, "reason_codes": list(error.reason_codes)},
        )
    if isinstance(error, (WorkflowConflictError, InvalidTransitionError)):
        return HTTPException(
            status_code=409,
            detail={"code": getattr(error, "code", "INVALID_STATE_TRANSITION")},
        )
    return HTTPException(status_code=500, detail={"code": "WORKFLOW_OPERATION_FAILED"})


PrincipalDependency = Annotated[Principal, Depends(_principal)]
ServiceDependency = Annotated[WorkflowService, Depends(_service)]


@router.post(
    "/recommendations/submit",
    response_model=ApprovalDetailV1,
    status_code=status.HTTP_201_CREATED,
)
def submit_recommendation(
    body: RecommendationSubmissionV1,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ApprovalDetailV1:
    _authorize(
        principal,
        Role.ANALYST,
        permissions=(Permission.WORKFLOW_SUBMIT,),
    )
    try:
        return service.submit(body, principal)
    except Exception as error:
        raise _translate(error) from error


@router.get("/approvals/pending", response_model=tuple[ApprovalDetailV1, ...])
def list_pending(
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> tuple[ApprovalDetailV1, ...]:
    _authorize(
        principal,
        Role.ANALYST,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE),
    )
    submitted_by = None if Role.PROCESS_OWNER in principal.roles else principal.actor_id
    return service.list_pending(submitted_by=submitted_by)


@router.get("/approvals/{approval_id}", response_model=ApprovalDetailV1)
def get_approval(
    approval_id: str,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ApprovalDetailV1:
    _authorize(
        principal,
        Role.ANALYST,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE),
    )
    try:
        submitted_by = None if Role.PROCESS_OWNER in principal.roles else principal.actor_id
        return service.get_approval(approval_id, submitted_by=submitted_by)
    except Exception as error:
        raise _translate(error) from error


def _decision(
    approval_id: str,
    body: DecisionRequestV1,
    principal: Principal,
    service: WorkflowService,
    target: ApprovalState,
) -> ApprovalDetailV1:
    _authorize(
        principal,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_APPROVE,),
    )
    try:
        return service.decide(approval_id, target, body, principal)
    except Exception as error:
        raise _translate(error) from error


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalDetailV1)
def approve(
    approval_id: str,
    body: DecisionRequestV1,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ApprovalDetailV1:
    return _decision(approval_id, body, principal, service, ApprovalState.APPROVED)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalDetailV1)
def reject(
    approval_id: str,
    body: DecisionRequestV1,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ApprovalDetailV1:
    return _decision(approval_id, body, principal, service, ApprovalState.REJECTED)


@router.post("/approvals/{approval_id}/request-revision", response_model=ApprovalDetailV1)
def request_revision(
    approval_id: str,
    body: DecisionRequestV1,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ApprovalDetailV1:
    return _decision(
        approval_id, body, principal, service, ApprovalState.REVISION_REQUESTED
    )


@router.get("/tickets", response_model=tuple[ImprovementTicketV1, ...])
def list_tickets(
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> tuple[ImprovementTicketV1, ...]:
    _authorize(
        principal,
        Role.ANALYST,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE),
    )
    submitted_by = None if Role.PROCESS_OWNER in principal.roles else principal.actor_id
    return service.list_tickets(submitted_by=submitted_by)


@router.get("/actions/{ticket_id}", response_model=ImprovementTicketV1)
def action_status(
    ticket_id: str,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ImprovementTicketV1:
    _authorize(
        principal,
        Role.ANALYST,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE),
    )
    try:
        submitted_by = None if Role.PROCESS_OWNER in principal.roles else principal.actor_id
        return service.get_ticket(ticket_id, submitted_by=submitted_by)
    except Exception as error:
        raise _translate(error) from error


@router.get("/audit", response_model=tuple[AuditEventV1, ...])
def audit_records(
    principal: PrincipalDependency,
    service: ServiceDependency,
    limit: int = 100,
) -> tuple[AuditEventV1, ...]:
    _authorize(
        principal,
        Role.ADMIN,
        permissions=(Permission.WORKFLOW_AUDIT,),
    )
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail={"code": "INVALID_LIMIT"})
    return service.list_audit(limit)


@router.get("/configuration", response_model=WorkflowConfigurationV1)
def workflow_configuration(
    request: Request,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> WorkflowConfigurationV1:
    _authorize(
        principal,
        Role.ADMIN,
        permissions=(Permission.WORKFLOW_AUDIT,),
    )
    environment_value = os.environ.get("APP_ENV", "local").lower()
    environment = environment_value if environment_value in {"local", "production"} else "other"
    return WorkflowConfigurationV1(
        environment=environment,
        authentication_mode=(
            "local_configured"
            if isinstance(_auth_adapter(request), LocalAuthenticationAdapter)
            else (
                "entra_validated"
                if isinstance(_auth_adapter(request), EntraAuthenticationAdapter)
                else "fail_closed"
            )
        ),
        database_dialect=service.database.engine.dialect.name,
        persistence_mode=persistence_mode(),
        migration_version=INITIAL_SCHEMA_VERSION,
    )
