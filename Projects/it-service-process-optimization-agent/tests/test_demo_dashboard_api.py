"""Phase 8A local demo dashboard: authenticated smoke tests over its own app."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from tests.test_demo_launcher import analysis_dir  # noqa: F401  (shared fixture)

from process_optimizer.demo.dashboard import create_dashboard_app
from process_optimizer.security.auth import LocalAuthenticationAdapter, Permission, Principal, Role
from process_optimizer.tools.manifest import build_aggregate_manifest

ANALYST = Principal(
    actor_id="demo.analyst",
    roles=frozenset({Role.ANALYST}),
    permissions=frozenset(
        {Permission.WORKFLOW_SUBMIT, Permission.AGGREGATE_READ, Permission.SIMULATION_EXECUTE}
    ),
)
OWNER = Principal(
    actor_id="demo.owner",
    roles=frozenset({Role.PROCESS_OWNER}),
    permissions=frozenset({Permission.WORKFLOW_APPROVE, Permission.AGGREGATE_READ}),
)


@pytest.fixture
def dashboard_client(
    analysis_dir: Path,  # noqa: F811  (parameter name matches the imported fixture)
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = build_aggregate_manifest(analysis_dir)
    (analysis_dir / "aggregate_manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    monkeypatch.setenv("PROCESS_OPTIMIZER_ANALYSIS_DIR", str(analysis_dir))
    monkeypatch.setenv("AGGREGATE_MANIFEST_PATH", str(analysis_dir / "aggregate_manifest.json"))

    app = create_dashboard_app()
    app.state.authentication_adapter = LocalAuthenticationAdapter(
        {"analyst-token": ANALYST, "owner-token": OWNER}
    )
    from process_optimizer.persistence import Database
    from process_optimizer.workflow.service import WorkflowService

    app.state.workflow_service = WorkflowService.from_analysis_dir(
        Database(f"sqlite:///{tmp_path / 'dashboard.sqlite3'}"), analysis_dir
    )
    return app


def test_demo_status_is_public_and_labeled(dashboard_client) -> None:
    async def run() -> None:
        transport = ASGITransport(app=dashboard_client)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/demo/status")
            assert response.status_code == 200
            body = response.json()
            assert body["azure_resources_used"] is False
            assert body["paid_model_calls_made"] is False
            assert body["external_notifications_sent"] is False

    asyncio.run(run())


def test_demo_investigation_requires_authentication_and_returns_recommendation(
    dashboard_client,
) -> None:
    async def run() -> None:
        transport = ASGITransport(app=dashboard_client)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get("/demo/investigation/group_returns")
            assert missing.status_code == 401

            unknown_scenario = await client.get(
                "/demo/investigation/not_a_real_scenario",
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert unknown_scenario.status_code == 422

            response = await client.get(
                "/demo/investigation/group_returns",
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["execution_label"] == "DETERMINISTIC_MOCK_NOT_REAL_AI"
            assert body["recommendations"]

    asyncio.run(run())


def test_demo_simulation_is_hypothetical_and_permission_gated(dashboard_client) -> None:
    async def run() -> None:
        transport = ASGITransport(app=dashboard_client)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            forbidden = await client.get(
                "/demo/simulation/conservative",
                headers={"Authorization": "Bearer owner-token"},
            )
            # OWNER has Aggregate.Read but not Simulation.Execute in this fixture.
            assert forbidden.status_code == 403

            response = await client.get(
                "/demo/simulation/conservative",
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["demo_headline_scenario"] == "conservative"
            assert body["status"] in {"ESTIMATED", "NOT_ESTIMABLE"}

    asyncio.run(run())


def test_end_to_end_submit_self_approval_blocked_then_owner_approves(
    dashboard_client,
) -> None:
    async def run() -> None:
        transport = ASGITransport(app=dashboard_client)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            investigation = (
                await client.get(
                    "/demo/investigation/group_returns",
                    headers={"Authorization": "Bearer analyst-token"},
                )
            ).json()
            submitted = await client.post(
                "/workflow/recommendations/submit",
                json={
                    "analysis_run_id": investigation["analysis_run_id"],
                    "recommendation": investigation["recommendations"][0],
                    "tool_call_records": investigation["tool_call_records"],
                    "proposed_owner": "incident-process-team",
                    "priority": "MEDIUM",
                    "idempotency_key": "dashboard-submit-1",
                    "correlation_id": "dashboard-corr-1",
                },
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert submitted.status_code == 201
            approval = submitted.json()

            self_attempt = await client.post(
                f"/workflow/approvals/{approval['approval_request_id']}/approve",
                json={
                    "recommendation_version_hash": approval["recommendation_version_hash"],
                    "idempotency_key": "dashboard-self-approve",
                    "correlation_id": approval["correlation_id"],
                },
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert self_attempt.status_code == 403

            approved = await client.post(
                f"/workflow/approvals/{approval['approval_request_id']}/approve",
                json={
                    "recommendation_version_hash": approval["recommendation_version_hash"],
                    "idempotency_key": "dashboard-owner-approve",
                    "correlation_id": approval["correlation_id"],
                },
                headers={"Authorization": "Bearer owner-token"},
            )
            assert approved.status_code == 200
            assert approved.json()["state"] == "EXECUTED"

            tickets = await client.get(
                "/workflow/tickets", headers={"Authorization": "Bearer owner-token"}
            )
            assert len(tickets.json()) == 1

    asyncio.run(run())


def test_dashboard_ui_is_served_labeled_and_keeps_tokens_out_of_urls(dashboard_client) -> None:
    async def run() -> None:
        transport = ASGITransport(app=dashboard_client)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/demo/ui/")
            assert response.status_code == 200
            page = response.text
            assert "AI-Powered IT Service Process Optimizer" in page
            assert "/demo/status" in page
            # Mock-generated recommendations stay labelled as such.
            assert "DETERMINISTIC MOCK PROVIDER" in page
            # Development disclaimers are no longer part of the visible UI.
            for removed in (
                "No Azure resources",
                "This dashboard makes no paid model calls",
                "No external notifications",
                "MOCK_LOCAL_DEMO_NOT_REAL_AI_NOT_PRODUCTION",
                "generated earlier by the real Azure gpt-4.1-mini model",
            ):
                assert removed not in page
            # Tokens travel only in the Authorization header: never in a URL,
            # the console, or browser storage. API text is rendered via
            # textContent, never innerHTML.
            assert '"Bearer " + ROLES[roleKey()].token' in page
            for forbidden in ("?token", "&token", "console.log", "localStorage", ".innerHTML"):
                assert forbidden not in page

    asyncio.run(run())


def test_existing_pending_request_is_listed_and_approved_without_resubmission(
    dashboard_client,
) -> None:
    """A request stored outside the dashboard (e.g. the real-model run) is decided in place."""

    async def run() -> None:
        transport = ASGITransport(app=dashboard_client)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            investigation = (
                await client.get(
                    "/demo/investigation/group_returns",
                    headers={"Authorization": "Bearer analyst-token"},
                )
            ).json()
            submission = {
                "analysis_run_id": investigation["analysis_run_id"],
                "recommendation": investigation["recommendations"][0],
                "tool_call_records": investigation["tool_call_records"],
                "proposed_owner": "incident-process-team",
                "priority": "MEDIUM",
                "idempotency_key": "external-submit-1",
                "correlation_id": "external-corr-1",
            }
            # Stored before the dashboard is opened, as the real-model command does.
            from process_optimizer.workflow.schemas import RecommendationSubmissionV1

            dashboard_client.state.workflow_service.submit(
                RecommendationSubmissionV1.model_validate(submission), ANALYST
            )

            pending = await client.get(
                "/workflow/approvals/pending", headers={"Authorization": "Bearer owner-token"}
            )
            assert pending.status_code == 200
            assert len(pending.json()) == 1
            approval = pending.json()[0]

            resubmitted = await client.post(
                "/workflow/recommendations/submit",
                json={**submission, "idempotency_key": "dashboard-resubmit"},
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert resubmitted.status_code == 409
            assert resubmitted.json()["detail"]["code"] == "WORKFLOW_CONFLICT"

            decision = {
                "recommendation_version_hash": approval["recommendation_version_hash"],
                "idempotency_key": "dashboard-owner-approve",
                "correlation_id": approval["correlation_id"],
            }
            url = f"/workflow/approvals/{approval['approval_request_id']}/approve"
            self_attempt = await client.post(
                url,
                json={**decision, "idempotency_key": "dashboard-self-approve"},
                headers={"Authorization": "Bearer analyst-token"},
            )
            assert self_attempt.status_code == 403

            approved = await client.post(
                url, json=decision, headers={"Authorization": "Bearer owner-token"}
            )
            assert approved.json()["state"] == "EXECUTED"
            replay = await client.post(
                url, json=decision, headers={"Authorization": "Bearer owner-token"}
            )
            assert replay.status_code == 200
            assert replay.json()["state"] == "EXECUTED"
            second = await client.post(
                url,
                json={**decision, "idempotency_key": "dashboard-owner-approve-2"},
                headers={"Authorization": "Bearer owner-token"},
            )
            assert second.status_code == 409

            tickets = await client.get(
                "/workflow/tickets", headers={"Authorization": "Bearer owner-token"}
            )
            assert len(tickets.json()) == 1
            refreshed = await client.get(
                "/workflow/approvals/pending", headers={"Authorization": "Bearer owner-token"}
            )
            assert refreshed.json() == []

            page = (await client.get("/demo/ui/")).text
            assert 'callApi("/workflow/approvals/pending")' in page
            assert "currentApproval.decisionKeys[targetPath] ||=" in page

    asyncio.run(run())
