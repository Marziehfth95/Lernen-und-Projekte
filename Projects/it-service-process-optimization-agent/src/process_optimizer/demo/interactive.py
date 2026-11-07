"""Interactive LOCAL demo application: real routers, real services, synthetic identities.

This module is never imported by the production application (``process_optimizer.main``).
It builds a separate FastAPI app that mounts the unchanged production routers
(``/health``, ``/workflow/*``, ``/aggregate/v1/*``) and adds one demo-only router
(``/demo/v2/*``). That router adds no business logic of its own. Each endpoint calls an
existing component:

* investigations use ``InvestigationRunner`` with ``DeterministicMockProvider`` (the
  default, labelled ``DETERMINISTIC_MOCK_NOT_REAL_AI``) or, only when the caller selects
  it explicitly, ``OllamaLocalProvider`` on the loopback Ollama endpoint (labelled
  ``REAL_MODEL_OUTPUT``). There is no fallback between them: an unavailable or failing
  local model is reported as such, never replaced by mock output;
* the guardrail preflight calls the same ``validate_recommendations`` and
  ``GuardrailEngine.check_submission`` that ``WorkflowService.submit`` calls, and writes nothing;
* approval reads go through ``WorkflowService.get_approval``, which applies the same actor
  scoping as ``/workflow/approvals/{id}``;
* the REVISION_REQUIRED demonstration swaps in a stricter policy exactly as
  ``scripts/local_e2e_demo.py`` check 22 does. The policy is labelled SIMULATED and can be
  restored.

Submitting, approving, rejecting, requesting revision, listing tickets and reading the audit
trail are not duplicated here. The browser calls the existing ``/workflow/*`` routes.

The app refuses to start unless ``APP_ENV=local``, no cloud-hosting marker is present,
the aggregate manifest verifies, and the served evidence contract and the guardrail policy
match their independently recorded pins. The role switcher (``/demo/v2/session``) exists only
in this app, and the app answers only on loopback host names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from process_optimizer import __version__
from process_optimizer.agents.demo import invocation_summary
from process_optimizer.agents.ollama_provider import (
    LOCAL_PROTOCOL_VERSION,
    PROVIDER_LABEL,
    OllamaLocalProvider,
    configured_ollama_base_url,
    configured_ollama_model,
    ollama_status,
)
from process_optimizer.agents.provider import DeterministicMockProvider, ModelProviderError
from process_optimizer.agents.schemas import InvestigationRequestV1
from process_optimizer.agents.validation import validate_recommendations
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.api.aggregate import VerifiedAggregateService
from process_optimizer.api.aggregate import router as aggregate_router
from process_optimizer.api.health import router as health_router
from process_optimizer.api.workflow import PrincipalDependency, _authorize
from process_optimizer.api.workflow import router as workflow_router
from process_optimizer.demo.launcher import _SCENARIO_GOALS
from process_optimizer.guardrails import (
    Decision,
    GuardrailEngine,
    check_evidence_contract_pin,
    evidence_contract_sha256,
    load_policy,
)
from process_optimizer.guardrails.compatibility import policy_metadata
from process_optimizer.observability import CorrelationAndTelemetryMiddleware
from process_optimizer.persistence import Database
from process_optimizer.persistence.models import ApprovalRequestRecord
from process_optimizer.security.auth import (
    AuthorizationError,
    LocalAuthenticationAdapter,
    Permission,
    Principal,
    Role,
    require_permission,
)
from process_optimizer.tools.manifest import AggregateArtifactVerifier
from process_optimizer.workflow.notifications import LocalDemoNotificationAdapter
from process_optimizer.workflow.service import WorkflowError, WorkflowService

# Independently recorded pins of the approved package and policy (identical to the values
# in scripts/local_e2e_demo.py). A package is never trusted by its own hash.
# The default evidence pin is the served evidence contract of the reproducible SYNTHETIC
# public demo package (scripts/prepare_public_demo_data.sh; reviewed values in
# tests/data/synthetic_ci_aggregate_fixture.json). A package built from a licensed copy of
# the original dataset needs its own independently recorded pin (--expected-evidence-sha256).
PINNED_EVIDENCE_SHA256 = "15f30d73f4a92ecc7daaf9e3bdccbc651dcafccfb9a1a96c799aecb2c46ca408"
PINNED_POLICY_SHA256 = "5a886139a16221c9f1fc6cbcc7754e2086bbfcdfdc2ce74f2a9bdc1a15dae358"
PINNED_POLICY_SCHEMA_VERSION = "2.0.0"
EXECUTION_LABEL = "DETERMINISTIC_MOCK_NOT_REAL_AI"
REAL_EXECUTION_LABEL = "REAL_MODEL_OUTPUT"
# Same graph budgets as the deterministic investigations (InvestigationRequestV1 defaults);
# the pinned policy additionally caps model calls, tokens and wall time per run.
REAL_MODEL_TIMEOUT_SECONDS = 60.0

# Any of these means the process runs in a hosted environment, where the role switcher
# must never exist.
CLOUD_HOSTING_MARKERS = (
    "CONTAINER_APP_NAME",
    "CONTAINER_APP_ENV_DNS_SUFFIX",
    "WEBSITE_SITE_NAME",
    "WEBSITE_INSTANCE_ID",
    "IDENTITY_ENDPOINT",
    "MSI_ENDPOINT",
    "KUBERNETES_SERVICE_HOST",
    "FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_CLIENT_ID",
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

_STATIC_DIR = Path(__file__).parent / "interactive_static"


class DemoStartupError(RuntimeError):
    """A precondition of the interactive demo is not met; the app is not built."""


# ------------------------------------------------------------------ synthetic identities


@dataclass(frozen=True)
class DemoIdentity:
    key: str
    label: str
    description: str
    principal: Principal
    token: str


def synthetic_identities() -> tuple[DemoIdentity, ...]:
    """Fresh random bearer tokens per launch; meaningful only to this local process."""

    def make(
        key: str,
        label: str,
        description: str,
        actor: str,
        roles: set[Role],
        permissions: set[Permission],
    ) -> DemoIdentity:
        return DemoIdentity(
            key=key,
            label=label,
            description=description,
            principal=Principal(
                actor_id=actor,
                roles=frozenset(roles),
                permissions=frozenset(permissions),
                identity_type="local",
            ),
            token=f"local-demo-{key}-{secrets.token_urlsafe(18)}",
        )

    # Same actor IDs, roles and permissions as scripts/local_e2e_demo.py.
    return (
        make(
            "analyst",
            "Analyst",
            "Reads evidence, runs investigations and what-if scenarios, submits recommendations.",
            "demo.analyst",
            {Role.ANALYST},
            {Permission.WORKFLOW_SUBMIT, Permission.AGGREGATE_READ, Permission.SIMULATION_EXECUTE},
        ),
        make(
            "owner",
            "Process Owner",
            "Reviews pending recommendations; approves, rejects or requests a revision.",
            "demo.process_owner",
            {Role.PROCESS_OWNER},
            {Permission.WORKFLOW_APPROVE, Permission.AGGREGATE_READ},
        ),
        make(
            "admin",
            "Admin",
            "Reads the audit trail and configuration; cannot approve or read process evidence.",
            "demo.admin",
            {Role.ADMIN},
            {Permission.WORKFLOW_AUDIT},
        ),
        make(
            "dual",
            "Analyst + Process Owner",
            "Holds both roles; the backend still forbids approving their own submission.",
            "demo.dual_role_user",
            {Role.ANALYST, Role.PROCESS_OWNER},
            {Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE, Permission.AGGREGATE_READ},
        ),
    )


# ------------------------------------------------------------------------ runtime state


@dataclass
class DemoRuntime:
    analysis_dir: Path
    workdir: Path
    database: Database
    baseline_service: WorkflowService
    aggregate_service: VerifiedAggregateService
    identities: tuple[DemoIdentity, ...]
    evidence_sha256: str
    expected_evidence_sha256: str = PINNED_EVIDENCE_SHA256
    policy_mode: Literal["PINNED", "SIMULATED_STRICTER"] = "PINNED"
    simulated_for_approval: str | None = None
    notes: list[str] = field(default_factory=list)
    ollama_model: str = ""
    ollama_base_url: str = ""
    # One local-model investigation at a time; a second request gets 409, never a mock.
    model_lock: threading.Lock = field(default_factory=threading.Lock)


def assert_local_environment(environ: dict[str, str] | None = None) -> None:
    """Fail closed outside an explicit local environment."""
    env = os.environ if environ is None else environ
    if env.get("APP_ENV", "").lower() != "local":
        raise DemoStartupError("The interactive demo runs only with APP_ENV=local")
    present = [name for name in CLOUD_HOSTING_MARKERS if env.get(name)]
    if present:
        raise DemoStartupError(
            "Cloud-hosting or cloud-credential variables are set; refusing to start the "
            "role switcher: " + ", ".join(present)
        )


def build_runtime(
    analysis_dir: Path,
    database_url: str,
    workdir: Path,
    *,
    expected_evidence_sha256: str = PINNED_EVIDENCE_SHA256,
    expected_policy_sha256: str = PINNED_POLICY_SHA256,
) -> DemoRuntime:
    """Verify the package and pins, then build the real services over a fresh store."""
    assert_local_environment()
    analysis_dir = Path(analysis_dir)
    manifest = analysis_dir / "aggregate_manifest.json"
    if not manifest.is_file():
        raise DemoStartupError(f"Approved aggregate package missing: {manifest}")
    try:
        aggregate = VerifiedAggregateService(
            AggregateArtifactVerifier.from_file(analysis_dir, manifest)
        )
    except Exception as error:  # noqa: BLE001 - any verification defect blocks startup.
        raise DemoStartupError(f"Aggregate manifest verification failed: {error}") from error
    if not check_evidence_contract_pin(aggregate.served_evidence, expected_evidence_sha256):
        raise DemoStartupError(
            "EV-02: the served evidence contract does not match the pinned SHA-256"
        )
    policy = aggregate.guardrail_policy
    if (
        policy.version != expected_policy_sha256
        or policy.policy_schema_version != PINNED_POLICY_SCHEMA_VERSION
    ):
        raise DemoStartupError("EV-04: the guardrail policy does not match the pinned version")

    try:
        ollama_model = configured_ollama_model()
        ollama_base_url = configured_ollama_base_url()
    except ModelProviderError as error:
        raise DemoStartupError(f"Local model configuration rejected: {error}") from error

    database = Database(database_url)
    service = WorkflowService(database, aggregate.evidence)
    if service.guardrails.policy.version != expected_policy_sha256:
        raise DemoStartupError("The workflow service does not enforce the pinned policy")
    return DemoRuntime(
        analysis_dir=analysis_dir,
        workdir=Path(workdir),
        database=database,
        baseline_service=service,
        aggregate_service=aggregate,
        identities=synthetic_identities(),
        evidence_sha256=evidence_contract_sha256(aggregate.served_evidence),
        expected_evidence_sha256=expected_evidence_sha256,
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url,
    )


# ---------------------------------------------------------------------------- helpers


def _runtime(request: Request) -> DemoRuntime:
    runtime = getattr(request.app.state, "demo_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail={"code": "DEMO_NOT_CONFIGURED"})
    return runtime


def _current_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service


def _require(principal: Principal, permission: Permission) -> None:
    try:
        require_permission(principal, permission)
    except AuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INSUFFICIENT_PERMISSION"},
        ) from error


def _derived_recommendation_id(original: str) -> str:
    """A fresh, schema-valid ID per draft so every demo submission is a new recommendation."""
    digest = hashlib.sha256(f"{original}:{uuid.uuid4().hex}".encode()).hexdigest()
    return f"recommendation_{digest[:24]}"


# Variants reuse the exact wording of scripts/local_e2e_demo.py checks 9, 10 and the
# documented known gap, so the interactive outcomes match the regression suite.
VARIANTS: dict[str, dict[str, Any]] = {
    "as_generated": {
        "label": "As generated by the investigation",
        "expectation": "Validated; requires human approval",
        "update": {},
    },
    "causal_lead_to": {
        "label": "Unsafe: causal claim ('lead to')",
        "expectation": "Blocked by guardrails",
        "update": {
            "problem_statement": (
                "Repeated group returns lead to longer case spans for these incidents."
            )
        },
    },
    "effect_30_percent": {
        "label": "Unsafe: unsupported effect ('will reduce ... by 30 percent')",
        "expectation": "Blocked by guardrails",
        "update": {
            "expected_outcome_with_uncertainty": (
                "This change will reduce the median case span by 30 percent."
            )
        },
    },
    "unknown_evidence": {
        "label": "Unsafe: cites an evidence ID that does not exist",
        "expectation": "Blocked by the validator",
        "update": {"evidence_ids": ("finding_does_not_exist",)},
    },
    "known_gap_cause": {
        "label": "KNOWN GAP: plain 'cause' wording (OPEN_NOT_SAFE)",
        "expectation": (
            "NOT blocked by policy 2.0.0: reaches human review; the Process Owner must reject it"
        ),
        "update": {
            "problem_statement": (
                "Repeated group returns cause longer case spans and delay closure."
            )
        },
    },
    "custom": {
        "label": "Custom problem statement (your own wording)",
        "expectation": "Decided by the real validator and guardrails",
        "update": {},
    },
}


# ----------------------------------------------------------------------------- schemas


class InvestigationBodyV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: Literal["group_returns", "completed_lifecycle"]
    goal: str | None = Field(default=None, min_length=1, max_length=2000)
    variant: Literal[
        "as_generated",
        "causal_lead_to",
        "effect_30_percent",
        "unknown_evidence",
        "known_gap_cause",
        "custom",
    ] = "as_generated"
    custom_problem_statement: str | None = Field(default=None, min_length=1, max_length=2000)
    # Explicit opt-in; the deterministic mock stays the default.
    model_mode: Literal["deterministic", "ollama"] = "deterministic"


class PolicySimulationBodyV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


# ------------------------------------------------------------------------------ router

router = APIRouter(prefix="/demo/v2", tags=["interactive local demo"])


@router.get("/session")
def session(request: Request) -> dict[str, Any]:
    """Role switcher bootstrap: synthetic local identities, environment and pins.

    Served only by this local app, only for APP_ENV=local, and only on loopback hosts
    (TrustedHostMiddleware). The tokens are random per launch and valid only here.
    """
    runtime = _runtime(request)
    try:
        assert_local_environment()
    except DemoStartupError as error:
        raise HTTPException(status_code=403, detail={"code": "ROLE_SWITCHER_DISABLED"}) from error
    service = _current_service(request)
    policy = service.guardrails.policy
    evidence = runtime.aggregate_service.served_evidence
    return {
        "environment": "local",
        "execution_label": EXECUTION_LABEL,
        "role_switcher": "LOCAL_SYNTHETIC_IDENTITIES_ONLY",
        "identities": [
            {
                "key": item.key,
                "label": item.label,
                "description": item.description,
                "actor_id": item.principal.actor_id,
                "roles": sorted(role.value for role in item.principal.roles),
                "permissions": sorted(p.value for p in item.principal.permissions),
                "token": item.token,
            }
            for item in runtime.identities
        ],
        "dataset": {
            "name": evidence.source_dataset.name,
            "source_url": evidence.source_dataset.source_url,
            # The synthetic generator labels its provenance with this prefix; anything else
            # is treated as a package built from a real event log.
            "synthetic": evidence.source_dataset.name.upper().startswith("SYNTHETIC"),
        },
        "integrity": {
            "analysis_run_id": evidence.analysis_run_id,
            "evidence_contract_version": evidence.schema_version,
            "evidence_sha256": runtime.evidence_sha256,
            "evidence_pin_matches": runtime.evidence_sha256 == runtime.expected_evidence_sha256,
            "policy_id": policy.policy_id,
            "policy_schema_version": policy.policy_schema_version,
            "policy_version": policy.version,
            "policy_pin_matches": policy.version == PINNED_POLICY_SHA256,
            "policy_mode": runtime.policy_mode,
            "simulated_for_approval": runtime.simulated_for_approval,
        },
        "isolation": {
            "database": "fresh temporary SQLite file, deleted on exit",
            "database_dialect": service.database.engine.dialect.name,
            "ticket_adapter": "LOCAL_DEMO_ONLY (LocalImprovementExecutor)",
            "notification_adapter": "LOCAL_DEMO_ONLY (LocalDemoNotificationAdapter)",
            "model_provider": (
                "DeterministicMockProvider (default); OllamaLocalProvider only when selected"
            ),
            "local_model_endpoint": f"{runtime.ollama_base_url} (loopback only, no pull)",
            "outbound_network": "refused except loopback",
            "azure_resources_used": False,
            "paid_model_calls": False,
        },
        "known_limitations": [
            {
                "id": "OPEN_NOT_SAFE",
                "summary": (
                    "Guardrail policy 2.0.0 does not block a plain 'cause' claim (for example "
                    "'Repeated group returns cause longer case spans and delay closure'). It "
                    "reaches human review, and only the Process Owner's rejection stops it."
                ),
            },
            {
                "id": "NUMERICAL_NOT_SEMANTIC",
                "summary": (
                    "Numerical grounding checks values, not meaning: a correct number "
                    "attached to the wrong subject is not detected."
                ),
            },
            {
                "id": "NO_PROCESS_VARIANTS",
                "summary": (
                    "The aggregate package contains no process-variant artifact, so "
                    "variants are not shown."
                ),
            },
            {
                "id": "REAL_MODEL_NOT_APPROVABLE_ON_SYNTHETIC_DATA",
                "summary": (
                    "Real local AI (Ollama) performs genuine inference, but in the recorded "
                    "runs on the synthetic public package it produced no recommendation that "
                    "passed the guardrails. The complete approval and ticket flow is "
                    "demonstrated with the deterministic provider."
                ),
            },
            *(
                [
                    {
                        "id": "SYNTHETIC_PUBLIC_DATA",
                        "summary": (
                            "All evidence comes from invented, seeded records processed by "
                            "the real pipeline. The figures describe no real organisation "
                            "and are not results from the BPI Challenge 2013 log."
                        ),
                    }
                ]
                if evidence.source_dataset.name.upper().startswith("SYNTHETIC")
                else []
            ),
        ],
        "default_model_mode": "deterministic",
        "model_options": _model_options(runtime),
        "variants": [
            {"key": key, "label": value["label"], "expectation": value["expectation"]}
            for key, value in VARIANTS.items()
        ],
        "scenarios": [{"key": key, "default_goal": goal} for key, goal in _SCENARIO_GOALS.items()],
    }


StepState = Literal["COMPLETED", "BLOCKED", "FAILED", "NOT_EXECUTED"]
# Graph stages shown in the run summary, in execution order.
_RUN_STEPS: tuple[tuple[str, str], ...] = (
    ("initialize", "Input guardrail"),
    ("analyst", "Analyst selects evidence"),
    ("investigator", "Investigator plans"),
    ("tool", "Aggregate tools"),
    ("optimizer", "Optimizer proposes"),
    ("validator", "Evidence validator"),
    ("output_guardrail", "Output guardrail"),
)
_BLOCKING_EVENTS = {"guardrail_blocked", "budget_exhausted"}
_LIMIT_OR_GUARDRAIL_CODES = {
    "GUARDRAIL_BLOCKED",
    "ITERATION_LIMIT_REACHED",
    "TOOL_BUDGET_EXHAUSTED",
    "MODEL_CALL_LIMIT_REACHED",
    "MODEL_TOKEN_BUDGET_EXHAUSTED",
    "RUN_DEADLINE_EXCEEDED",
}


def investigation_steps(result: Any) -> list[dict[str, Any]]:
    """Per-step state derived only from the graph's own audit events.

    A step is COMPLETED only when the backend recorded its successful completion;
    BLOCKED when a guardrail or safety budget stopped it; FAILED when it ran and failed
    (provider error, invalid output, failed tool, rejected evidence); NOT_EXECUTED when
    no event exists for it.
    """
    events = list(result.audit_events)
    steps: list[dict[str, Any]] = []
    for key, label in _RUN_STEPS:
        stage = "validator" if key == "output_guardrail" else key
        mine = [event for event in events if event.stage == stage]
        state: StepState = "NOT_EXECUTED"
        detail = ""
        stop = next(
            (
                event
                for event in reversed(mine)
                if event.event_type in _BLOCKING_EVENTS or event.event_type == "provider_failed"
            ),
            None,
        )
        if key == "initialize":
            if any(event.event_type == "investigation_started" for event in mine):
                state, detail = "COMPLETED", "goal accepted (ALLOW)"
        elif key in ("validator", "output_guardrail"):
            validated = next(
                (event for event in mine if event.event_type == "evidence_validated"), None
            )
            if validated is not None:
                details = validated.safe_details
                decision = str(details.get("guardrail_decision") or "")
                codes = str(details.get("guardrail_reason_codes") or "")
                if key == "output_guardrail":
                    if decision == "REQUIRE_APPROVAL":
                        state, detail = "COMPLETED", "REQUIRE_APPROVAL (human decision required)"
                    else:
                        state, detail = "BLOCKED", f"{decision or 'BLOCK'} {codes}".strip()
                else:
                    valid = details.get("valid_count")
                    total = details.get("validation_count")
                    if valid == total and total:
                        state, detail = "COMPLETED", f"{valid}/{total} valid"
                    else:
                        failed_codes = sorted(
                            {
                                code
                                for item in result.validation_results
                                for code in item.error_codes
                            }
                        )
                        state = "BLOCKED"
                        detail = f"{valid}/{total} valid: " + ", ".join(failed_codes)
        elif stop is not None:
            blocked = stop.event_type in _BLOCKING_EVENTS or stop.outcome == "GUARDRAIL_BLOCKED"
            state = "BLOCKED" if blocked else "FAILED"
            reason = stop.safe_details.get("guardrail_reason_codes") or stop.outcome
            detail = str(reason)
        elif key == "tool":
            calls = [event for event in mine if event.event_type == "tool_called"]
            if calls:
                bad = [event for event in calls if event.outcome != "success"]
                state = "FAILED" if bad else "COMPLETED"
                detail = f"{len(calls) - len(bad)}/{len(calls)} calls succeeded"
        elif mine:
            last = mine[-1]
            if last.outcome == "success":
                state = "COMPLETED"
                detail = {
                    "analyst": f"{last.safe_details.get('selected_count')} finding(s) selected",
                    "investigator": f"{len(mine)} planning round(s)",
                    "optimizer": last.event_type.replace("_", " "),
                }.get(key, "")
            else:
                state, detail = "FAILED", f"{last.event_type}: {last.outcome}"
        # Graph errors recorded without an audit event still override a COMPLETED state.
        errors = [error for error in result.errors if error.stage == stage]
        if key != "output_guardrail" and errors and state in ("COMPLETED", "NOT_EXECUTED"):
            code = errors[-1].code
            state = "BLOCKED" if code in _LIMIT_OR_GUARDRAIL_CODES else "FAILED"
            detail = code
        steps.append({"key": key, "label": label, "state": state, "detail": detail})
    return steps


def _model_options(runtime: DemoRuntime) -> list[dict[str, Any]]:
    return [
        {
            "key": "deterministic",
            "label": "Deterministic Demo",
            "execution_label": EXECUTION_LABEL,
            "provider": "DeterministicMockProvider",
            "model": None,
            "real_ai": False,
        },
        {
            "key": "ollama",
            "label": "Real Local AI (Ollama)",
            "execution_label": REAL_EXECUTION_LABEL,
            "provider": f"OllamaLocalProvider ({PROVIDER_LABEL})",
            "model": runtime.ollama_model,
            "endpoint": runtime.ollama_base_url,
            "real_ai": True,
        },
    ]


@router.get("/model/status")
def model_status(request: Request) -> dict[str, Any]:
    """Read-only local Ollama availability (version and tag list only; never pulls)."""
    runtime = _runtime(request)
    status_ = ollama_status(runtime.ollama_base_url, runtime.ollama_model)
    status_["busy"] = runtime.model_lock.locked()
    return status_


def _run_real_local_model(
    runtime: DemoRuntime, goal: str, scenario: str
) -> tuple[Any, dict[str, Any]]:
    """Run the unchanged graph with the local Ollama provider; never falls back to a mock."""
    if not runtime.model_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={"code": "LOCAL_MODEL_BUSY"})
    try:
        status_ = ollama_status(runtime.ollama_base_url, runtime.ollama_model)
        if not status_["available"]:
            raise HTTPException(
                status_code=503,
                detail={"code": "LOCAL_MODEL_UNAVAILABLE", "reason": status_["reason"]},
            )
        try:
            provider = OllamaLocalProvider(
                model=runtime.ollama_model,
                base_url=runtime.ollama_base_url,
                timeout_seconds=REAL_MODEL_TIMEOUT_SECONDS,
            )
            runner = InvestigationRunner(runtime.analysis_dir, provider=provider)
            result = runner.run(
                InvestigationRequestV1(
                    analysis_run_id=runner.analysis_run_id,
                    investigation_goal=goal,
                    execution_mode="real_model",
                    scenario=scenario,
                )
            )
        except ModelProviderError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": "LOCAL_MODEL_UNAVAILABLE", "reason": error.category},
            ) from error
    finally:
        runtime.model_lock.release()
    summary = invocation_summary(provider.invocation_records)
    reported = summary["service_reported_models"]
    # Provenance comes from the server-reported model of each reply, not the request label.
    if any(name != runtime.ollama_model for name in reported):
        raise HTTPException(status_code=502, detail={"code": "MODEL_PROVENANCE_MISMATCH"})
    succeeded = sum(1 for call in summary["calls"] if not call["error_category"])
    model_info = {
        "mode": "ollama",
        "real_ai": True,
        "provider": f"OllamaLocalProvider ({PROVIDER_LABEL})",
        "model": runtime.ollama_model,
        "endpoint": runtime.ollama_base_url,
        "ollama_version": status_.get("ollama_version"),
        "model_digest": status_.get("digest"),
        "parameter_size": status_.get("parameter_size"),
        "quantization_level": status_.get("quantization_level"),
        "local_protocol_version": LOCAL_PROTOCOL_VERSION,
        "genuine_model_response": succeeded > 0 and reported == [runtime.ollama_model],
        "invocations": summary,
    }
    return result, model_info


@router.get("/evidence/provenance")
def evidence_provenance(request: Request, principal: PrincipalDependency) -> dict[str, Any]:
    """Package provenance and suppression counts (never the suppression threshold)."""
    _require(principal, Permission.AGGREGATE_READ)
    runtime = _runtime(request)
    aggregate = runtime.aggregate_service
    try:
        aggregate.verify()
        manifest = json.loads(
            (runtime.analysis_dir / "aggregate_manifest.json").read_text(encoding="utf-8")
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail={"code": "AGGREGATE_OPERATION_UNAVAILABLE"}
        ) from error
    total = len(aggregate.evidence.findings)
    visible = len(aggregate.served_evidence.findings)
    by_metric: dict[str, int] = {}
    for finding in aggregate.served_evidence.findings:
        by_metric[finding.metric.name] = by_metric.get(finding.metric.name, 0) + 1
    return {
        "analysis_run_id": aggregate.analysis_run_id,
        "source_dataset": aggregate.evidence.source_dataset.model_dump(mode="json"),
        "contract_interpretation": aggregate.evidence.contract_interpretation,
        "manifest_status": "VERIFIED",
        "artifacts": manifest.get("artifacts", []),
        "suppression": {
            "rule": "small-group suppression from the guardrail policy (threshold not served)",
            "findings_in_contract": total,
            "findings_visible": visible,
            "findings_withheld": total - visible,
        },
        "visible_findings_by_metric": by_metric,
    }


@router.post("/investigations")
def run_investigation(
    body: InvestigationBodyV2, request: Request, principal: PrincipalDependency
) -> dict[str, Any]:
    """Run the real LangGraph investigation with the selected provider.

    ``model_mode="deterministic"`` (default) uses the deterministic mock; ``"ollama"`` uses
    real local inference and fails visibly (503/409/502) instead of falling back.
    Returns the typed result, a submission draft with a fresh recommendation ID (and the
    selected wording variant), and a read-only guardrail preflight computed exactly as
    ``WorkflowService.submit`` computes it. Nothing is persisted.
    """
    _authorize(principal, Role.ANALYST, permissions=(Permission.WORKFLOW_SUBMIT,))
    _require(principal, Permission.AGGREGATE_READ)
    runtime = _runtime(request)
    service = _current_service(request)
    if body.variant == "custom" and not body.custom_problem_statement:
        raise HTTPException(status_code=422, detail={"code": "CUSTOM_TEXT_REQUIRED"})

    goal = body.goal or _SCENARIO_GOALS[body.scenario]
    screened = GuardrailEngine().check_invocation_input(goal=goal)
    if screened.decision != Decision.ALLOW:
        # Fixed enum codes only; the caller's text is never echoed.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INPUT_GUARDRAIL_BLOCKED",
                "checkpoint": screened.checkpoint.value,
                "reason_codes": list(screened.reason_codes),
            },
        )
    model_info: dict[str, Any] = {
        "mode": "deterministic",
        "real_ai": False,
        "provider": "DeterministicMockProvider",
        "model": None,
    }
    try:
        if body.model_mode == "ollama":
            result, model_info = _run_real_local_model(runtime, goal, body.scenario)
            expected_label = REAL_EXECUTION_LABEL
        else:
            runner = InvestigationRunner(
                runtime.analysis_dir, provider=DeterministicMockProvider()
            )
            result = runner.run(
                InvestigationRequestV1(
                    analysis_run_id=runner.analysis_run_id,
                    investigation_goal=goal,
                    scenario=body.scenario,
                )
            )
            expected_label = EXECUTION_LABEL
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=503, detail={"code": "INVESTIGATION_UNAVAILABLE"}
        ) from error
    if result.execution_label != expected_label:
        raise HTTPException(status_code=500, detail={"code": "UNEXPECTED_PROVIDER"})

    payload: dict[str, Any] = {
        "execution_label": result.execution_label,
        "model": model_info,
        "scenario": body.scenario,
        "investigation": result.model_dump(mode="json"),
        "steps": investigation_steps(result),
        "draft": None,
        "preflight": None,
    }
    if not result.recommendations:
        return payload

    original = result.recommendations[0]
    update = dict(VARIANTS[body.variant]["update"])
    if body.variant == "custom":
        update["problem_statement"] = body.custom_problem_statement
    candidate = original.model_copy(
        update={
            "recommendation_id": _derived_recommendation_id(original.recommendation_id),
            **update,
        }
    )
    validated, results = validate_recommendations(
        (candidate,),
        service.evidence,
        result.tool_call_records,
        policy=service.guardrails.policy,
    )
    decision = service.guardrails.check_submission(
        recommendation=validated[0],
        validation_result=results[0],
        identity_type=principal.identity_type,
    )
    payload["draft"] = {
        "variant": body.variant,
        "variant_label": VARIANTS[body.variant]["label"],
        "expectation": VARIANTS[body.variant]["expectation"],
        "derived_from_recommendation_id": original.recommendation_id,
        "generated_by": model_info["model"] or "DeterministicMockProvider",
        # Any variant other than as_generated rewrites model text on the server, so the
        # result is no longer the model's own output and is labelled as modified.
        "modified_after_generation": body.variant != "as_generated",
        "graph_final_decision": str(result.final_decision),
        "submission": {
            "analysis_run_id": result.analysis_run_id,
            "recommendation": candidate.model_dump(mode="json"),
            "tool_call_records": [r.model_dump(mode="json") for r in result.tool_call_records],
            "proposed_owner": "incident-process-team",
            "priority": "MEDIUM",
        },
    }
    payload["preflight"] = {
        "checkpoint": decision.checkpoint.value,
        "decision": decision.decision.value,
        "reason_codes": list(decision.reason_codes),
        "validator_valid": results[0].valid,
        "validator_error_codes": list(results[0].error_codes),
        "policy_version": decision.policy_version,
        "note": "Read-only preflight. The submit endpoint re-runs the same checks server-side.",
    }
    return payload


@router.get("/approvals")
def list_approvals(request: Request, principal: PrincipalDependency) -> list[dict[str, Any]]:
    """All approval requests in any state, scoped exactly like ``/workflow/approvals/{id}``."""
    _authorize(
        principal,
        Role.ANALYST,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE),
    )
    service = _current_service(request)
    submitted_by = None if Role.PROCESS_OWNER in principal.roles else principal.actor_id
    with service.database.session() as session_:
        ids = session_.scalars(
            select(ApprovalRequestRecord.id).order_by(ApprovalRequestRecord.created_at)
        ).all()
    items = []
    for approval_id in ids:
        try:
            items.append(
                service.get_approval(approval_id, submitted_by=submitted_by).model_dump(mode="json")
            )
        except WorkflowError:
            continue
    return items


@router.get("/approvals/{approval_id}/recommendation")
def approval_recommendation(
    approval_id: str, request: Request, principal: PrincipalDependency
) -> dict[str, Any]:
    """The stored, immutable recommendation version a reviewer decides on."""
    _authorize(
        principal,
        Role.ANALYST,
        Role.PROCESS_OWNER,
        permissions=(Permission.WORKFLOW_SUBMIT, Permission.WORKFLOW_APPROVE),
    )
    service = _current_service(request)
    submitted_by = None if Role.PROCESS_OWNER in principal.roles else principal.actor_id
    try:
        detail = service.get_approval(approval_id, submitted_by=submitted_by)
    except WorkflowError as error:
        code = getattr(error, "code", "NOT_FOUND")
        raise HTTPException(
            status_code=404 if code == "NOT_FOUND" else 403, detail={"code": code}
        ) from error
    with service.database.session() as session_:
        row = session_.get(ApprovalRequestRecord, approval_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        provenance = row.provenance
        return {
            "approval": detail.model_dump(mode="json"),
            "recommendation": json.loads(row.recommendation.payload_json),
            "decisions": [
                {
                    "decision": item.decision,
                    "actor_id": item.actor_id,
                    "reason": item.reason,
                    "created_at": item.created_at.isoformat(),
                }
                for item in row.decisions
            ],
            "provenance": (
                {
                    "policy_id": provenance.policy_id,
                    "policy_schema_version": provenance.policy_schema_version,
                    "policy_version": provenance.policy_version,
                    "evidence_contract_sha256": provenance.evidence_contract_sha256,
                    "analysis_run_id": provenance.analysis_run_id,
                }
                if provenance is not None
                else None
            ),
        }


@router.get("/audit/latest")
def latest_audit(
    request: Request, principal: PrincipalDependency, limit: int = 100
) -> list[dict[str, Any]]:
    """Newest audit events first; same authorization as ``/workflow/audit`` (Admin only)."""
    _authorize(principal, Role.ADMIN, permissions=(Permission.WORKFLOW_AUDIT,))
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail={"code": "INVALID_LIMIT"})
    events = _current_service(request).list_audit(100_000)
    return [event.model_dump(mode="json") for event in reversed(events[-limit:])]


@router.post("/notifications/deliver")
def deliver_notifications(request: Request, principal: PrincipalDependency) -> dict[str, Any]:
    """Deliver queued notifications to the local adapter only (no external send)."""
    _authorize(principal, Role.ADMIN, permissions=(Permission.WORKFLOW_AUDIT,))
    delivered, failed = _current_service(request).deliver_notifications(
        LocalDemoNotificationAdapter(), principal
    )
    return {"adapter": "LOCAL_DEMO_ONLY_NO_EXTERNAL_SEND", "delivered": delivered, "failed": failed}


@router.post("/policy/simulate-stricter")
def simulate_stricter_policy(
    body: PolicySimulationBodyV2, request: Request, principal: PrincipalDependency
) -> dict[str, Any]:
    """SIMULATED policy upgrade for the REVISION_REQUIRED demonstration (Admin only).

    Mirrors scripts/local_e2e_demo.py check 22: the packaged policy plus one extra
    pattern derived from the chosen pending recommendation, written to the temporary
    run directory and never packaged. Every later submission and approval is checked
    against it until ``/demo/v2/policy/restore``.
    """
    _authorize(principal, Role.ADMIN, permissions=(Permission.WORKFLOW_AUDIT,))
    runtime = _runtime(request)
    baseline = runtime.baseline_service
    with baseline.database.session() as session_:
        row = session_.get(ApprovalRequestRecord, body.approval_request_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        if row.state != "PENDING_APPROVAL":
            raise HTTPException(status_code=409, detail={"code": "APPROVAL_NOT_PENDING"})
        text = json.loads(row.recommendation.payload_json)["suggested_process_improvement"]
    packaged = Path(load_policy.__code__.co_filename).with_name("policy.v1.json")
    raw = json.loads(packaged.read_text(encoding="utf-8"))
    phrase = re.escape(" ".join(text.split()[:3]))
    raw["output"]["assertive_causal_patterns"].append(rf"\b{phrase}\b")
    derived = runtime.workdir / "simulated_stricter_policy.json"
    derived.write_text(json.dumps(raw), encoding="utf-8")
    stricter = GuardrailEngine(load_policy(derived))
    request.app.state.workflow_service = WorkflowService(
        runtime.database, baseline.evidence, guardrails=stricter
    )
    runtime.policy_mode = "SIMULATED_STRICTER"
    runtime.simulated_for_approval = body.approval_request_id
    return {
        "policy_mode": runtime.policy_mode,
        "label": "SIMULATED_POLICY_UPGRADE_NOT_PACKAGED",
        "policy": policy_metadata(stricter.policy).model_dump(mode="json"),
        "pinned_policy_version": PINNED_POLICY_SHA256,
    }


@router.post("/policy/restore")
def restore_policy(request: Request, principal: PrincipalDependency) -> dict[str, Any]:
    """Return to the pinned, packaged policy."""
    _authorize(principal, Role.ADMIN, permissions=(Permission.WORKFLOW_AUDIT,))
    runtime = _runtime(request)
    request.app.state.workflow_service = runtime.baseline_service
    runtime.policy_mode = "PINNED"
    runtime.simulated_for_approval = None
    return {
        "policy_mode": runtime.policy_mode,
        "policy": policy_metadata(runtime.baseline_service.guardrails.policy).model_dump(
            mode="json"
        ),
    }


# --------------------------------------------------------------------------- app factory


class _SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


def create_interactive_app(runtime: DemoRuntime) -> FastAPI:
    """Build the LOCAL interactive demo app around an already verified runtime."""
    assert_local_environment()
    app = FastAPI(
        title="IT Service Process Optimizer: interactive LOCAL demo (not production)",
        version=__version__,
        docs_url=None,
        redoc_url=None,
    )
    app.state.demo_runtime = runtime
    app.state.workflow_service = runtime.baseline_service
    app.state.aggregate_service = runtime.aggregate_service
    app.state.authentication_adapter = LocalAuthenticationAdapter(
        {item.token: item.principal for item in runtime.identities}
    )
    app.include_router(health_router)
    app.include_router(workflow_router)
    app.include_router(aggregate_router)
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=_STATIC_DIR, html=True), name="interactive-ui")
    app.add_middleware(CorrelationAndTelemetryMiddleware)
    app.add_middleware(_SecurityHeaders)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOOPBACK_HOSTS))
    return app
