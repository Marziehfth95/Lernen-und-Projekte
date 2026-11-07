"""Demo-only read endpoints: run the deterministic mock agent, nothing else.

This router is mounted only by ``process_optimizer.demo.dashboard`` and is
never included in the production application (``process_optimizer.main``).
It adds no new business logic: it calls the same ``InvestigationRunner`` used
by ``process-optimizer-agent-demo`` and returns its typed result. Submitting,
approving, rejecting, listing tickets, and reading audit events are not
duplicated here; the dashboard calls the existing authenticated
``/workflow/*`` and ``/aggregate/v1/*`` routes directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from process_optimizer.agents.provider import DeterministicMockProvider
from process_optimizer.agents.schemas import InvestigationRequestV1
from process_optimizer.agents.workflow import InvestigationRunner
from process_optimizer.api.workflow import PrincipalDependency
from process_optimizer.demo.launcher import (
    _SCENARIO_GOALS,
    DemoError,
    run_conservative_simulation,
)
from process_optimizer.process_mining.constants import DEFAULT_ANALYSIS_DIR
from process_optimizer.security.auth import (
    AuthorizationError,
    Permission,
    require_permission,
)

router = APIRouter(prefix="/demo", tags=["local demo"])


def _configured_analysis_dir() -> Path:
    """Honor the same environment variable the workflow and aggregate routers use."""
    return Path(os.environ.get("PROCESS_OPTIMIZER_ANALYSIS_DIR", str(DEFAULT_ANALYSIS_DIR)))


class DemoStatusV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["MOCK_LOCAL_DEMO_NOT_REAL_AI_NOT_PRODUCTION"] = (
        "MOCK_LOCAL_DEMO_NOT_REAL_AI_NOT_PRODUCTION"
    )
    azure_resources_used: Literal[False] = False
    paid_model_calls_made: Literal[False] = False
    external_notifications_sent: Literal[False] = False


@router.get("/status", response_model=DemoStatusV1)
def demo_status() -> DemoStatusV1:
    return DemoStatusV1()


@router.get("/investigation/{scenario}")
def demo_investigation(scenario: str, principal: PrincipalDependency) -> dict:
    """Run the deterministic mock investigation and return its typed result.

    Requires ``Aggregate.Read`` because the output is derived entirely from
    the governed aggregate evidence contract; it reads no record-level data.
    """
    try:
        require_permission(principal, Permission.AGGREGATE_READ)
    except AuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INSUFFICIENT_PERMISSION"},
        ) from error
    if scenario not in _SCENARIO_GOALS:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNKNOWN_SCENARIO", "allowed": sorted(_SCENARIO_GOALS)},
        )
    try:
        runner = InvestigationRunner(
            _configured_analysis_dir(), provider=DeterministicMockProvider()
        )
        result = runner.run(
            InvestigationRequestV1(
                analysis_run_id=runner.analysis_run_id,
                investigation_goal=_SCENARIO_GOALS[scenario],
                scenario=scenario,
            )
        )
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AGGREGATE_PACKAGE_NOT_READY"},
        ) from error
    return result.model_dump(mode="json")


@router.get("/simulation/conservative")
def demo_conservative_simulation(principal: PrincipalDependency) -> dict:
    """Return one conservative, clearly hypothetical what-if simulation.

    Thin wrapper over ``run_conservative_simulation`` (the same function the
    CLI launcher uses); it adds no new simulation logic.
    """
    try:
        require_permission(principal, Permission.SIMULATION_EXECUTE)
    except AuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INSUFFICIENT_PERMISSION"},
        ) from error
    try:
        return run_conservative_simulation(_configured_analysis_dir())
    except (OSError, ValueError, DemoError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AGGREGATE_PACKAGE_NOT_READY"},
        ) from error
