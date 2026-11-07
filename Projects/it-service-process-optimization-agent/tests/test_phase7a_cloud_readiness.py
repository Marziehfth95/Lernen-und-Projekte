"""Phase 7A security and cloud-boundary regression tests."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from process_optimizer.api.aggregate import router as aggregate_router
from process_optimizer.api.workflow import router as workflow_router
from process_optimizer.observability import CorrelationAndTelemetryMiddleware
from process_optimizer.persistence import cloud
from process_optimizer.security.auth import (
    AuthenticationError,
    EntraAuthenticationAdapter,
    EntraAuthenticationSettings,
    LocalAuthenticationAdapter,
    Permission,
    Principal,
    Role,
)
from process_optimizer.tools.analytics import AggregateToolResult, ProcessOverviewInput
from process_optimizer.tools.client import AuthenticatedAggregateApiTools


class StaticKeyProvider:
    def __init__(self, key: Any) -> None:
        self.key = key

    def get_signing_key(self, token: str) -> Any:
        assert token
        return self.key


@pytest.fixture
def token_material() -> tuple[Any, Any, EntraAuthenticationSettings]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = EntraAuthenticationSettings(
        tenant_id="test-tenant",
        audience="api://workflow",
        issuer="https://login.microsoftonline.com/test-tenant/v2.0",
        jwks_url="https://example.invalid/keys",
        allowed_service_client_ids=frozenset({"foundry-agent"}),
        clock_skew_seconds=0,
    )
    return private_key, private_key.public_key(), settings


def _token(private_key: Any, settings: EntraAuthenticationSettings, **overrides: Any) -> str:
    claims = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "tid": settings.tenant_id,
        "exp": int(time.time()) + 300,
        "oid": "actor-1",
        "scp": "Aggregate.Read",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})


def test_entra_validates_signature_claims_and_service_allowlist(
    token_material: tuple[Any, Any, EntraAuthenticationSettings],
) -> None:
    private_key, public_key, settings = token_material
    adapter = EntraAuthenticationAdapter(settings, StaticKeyProvider(public_key))
    human = adapter.authenticate(_token(private_key, settings))
    assert human.identity_type == "human"
    assert Permission.AGGREGATE_READ in human.permissions

    service = adapter.authenticate(
        _token(
            private_key,
            settings,
            oid="service-object",
            idtyp="app",
            azp="foundry-agent",
            roles=["Aggregate.Read"],
            scp=None,
        )
    )
    assert service.identity_type == "service"
    assert service.client_id == "foundry-agent"

    for invalid in (
        {"aud": "api://wrong"},
        {"iss": "https://issuer.invalid"},
        {"exp": int(time.time()) - 1},
        {"tid": "other-tenant"},
        {"idtyp": "app", "azp": "unknown-client", "roles": ["Aggregate.Read"], "scp": None},
    ):
        with pytest.raises(
            AuthenticationError, match="validation failed|not allowed|not allow-listed"
        ):
            adapter.authenticate(_token(private_key, settings, **invalid))


class StubTools:
    def get_process_overview(self, body: object) -> AggregateToolResult:
        del body
        return AggregateToolResult(
            returned_count=1,
            truncated=False,
            items=({"case_count": 7500, "unit": "incidents"},),
        )


class StubAggregateService:
    analysis_run_id = "run_000000000000000000000000"
    contract_version = "1.0.0"
    tools = StubTools()

    def verify(self) -> None:
        return None


def _aggregate_app(principal: Principal) -> FastAPI:
    app = FastAPI()
    app.include_router(aggregate_router)
    app.add_middleware(CorrelationAndTelemetryMiddleware)
    app.state.authentication_adapter = LocalAuthenticationAdapter({"token": principal})
    app.state.aggregate_service = StubAggregateService()
    return app


def test_aggregate_api_requires_scoped_permission_and_propagates_correlation() -> None:
    allowed = Principal(
        actor_id="foundry-service",
        roles=frozenset(),
        permissions=frozenset({Permission.AGGREGATE_READ}),
        identity_type="service",
    )
    with TestClient(_aggregate_app(allowed)) as client:
        response = client.get(
            "/aggregate/v1/process-overview",
            headers={"Authorization": "Bearer token", "X-Correlation-ID": "phase7-test"},
        )
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "phase7-test"
    assert response.json()["result"]["items"] == [
        {"case_count": 7500, "unit": "incidents"}
    ]

    denied = allowed.model_copy(update={"permissions": frozenset()})
    with TestClient(_aggregate_app(denied)) as client:
        response = client.get(
            "/aggregate/v1/process-overview",
            headers={"Authorization": "Bearer token"},
        )
    assert response.status_code == 403


def test_service_identity_cannot_use_human_workflow_endpoints() -> None:
    service = Principal(
        actor_id="service",
        roles=frozenset({Role.ADMIN}),
        permissions=frozenset(),
        identity_type="service",
    )
    app = FastAPI()
    app.include_router(workflow_router)
    app.state.authentication_adapter = LocalAuthenticationAdapter({"token": service})
    app.state.workflow_service = SimpleNamespace()
    with TestClient(app) as client:
        response = client.get(
            "/workflow/configuration", headers={"Authorization": "Bearer token"}
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "HUMAN_IDENTITY_REQUIRED"


def test_production_never_accepts_local_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "LOCAL_AUTH_IDENTITIES_JSON",
        '{"local":{"actor_id":"local","roles":["ADMIN"]}}',
    )
    for setting in ("ENTRA_TENANT_ID", "ENTRA_AUDIENCE", "ENTRA_ISSUER", "ENTRA_JWKS_URL"):
        monkeypatch.delenv(setting, raising=False)
    app = FastAPI()
    app.include_router(workflow_router)
    with TestClient(app) as client:
        response = client.get(
            "/workflow/configuration", headers={"Authorization": "Bearer local"}
        )
    assert response.status_code == 401


def test_platform_identity_headers_are_not_trusted_without_a_validated_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CONTAINER_APPS_AUTH_ENABLED", "true")
    allowed = Principal(
        actor_id="foundry-service",
        roles=frozenset(),
        permissions=frozenset({Permission.AGGREGATE_READ}),
        identity_type="service",
    )
    app = _aggregate_app(allowed)
    with TestClient(app) as client:
        spoofed = client.get(
            "/aggregate/v1/process-overview",
            headers={"X-MS-CLIENT-PRINCIPAL": "untrusted"},
        )
        forwarded_token = client.get(
            "/aggregate/v1/process-overview",
            headers={"X-MS-TOKEN-AAD-ACCESS-TOKEN": "token"},
        )
    assert spoofed.status_code == 401
    assert forwarded_token.status_code == 200


def test_authenticated_client_uses_only_fixed_https_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://workflow.example/aggregate/v1/process-overview"
        assert request.headers["authorization"] == "Bearer controlled-token"
        return httpx.Response(
            200,
            json={
                "correlation_id": "corr",
                "analysis_run_id": "run_000000000000000000000000",
                "evidence_contract_version": "1.0.0",
                "result": {
                    "contract_version": "1.0.0",
                    "returned_count": 1,
                    "truncated": False,
                    "items": [{"metric": "aggregate"}],
                },
            },
        )

    tools = AuthenticatedAggregateApiTools(
        "https://workflow.example",
        lambda: "controlled-token",
        transport=httpx.MockTransport(handler),
    )
    result = tools.get_process_overview(ProcessOverviewInput())
    assert result.returned_count == 1
    with pytest.raises(ValueError, match="HTTPS"):
        AuthenticatedAggregateApiTools("http://unsafe.example", lambda: "token")

    with pytest.raises(ValueError, match="maximum serialized size"):
        AggregateToolResult(
            returned_count=1,
            truncated=False,
            items=({"aggregate_text": "x" * (257 * 1024)},),
        )


def test_production_database_configuration_rejects_sqlite_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_DATABASE_URL", "sqlite:///shared.sqlite3")
    with pytest.raises(ValueError, match="postgresql"):
        cloud.database_from_environment(environment="production", token_provider=lambda: "token")

    monkeypatch.setenv(
        "WORKFLOW_DATABASE_URL", "postgresql+psycopg://user:secret@db.example/workflow"
    )
    with pytest.raises(ValueError, match="must not contain a password"):
        cloud.database_from_environment(environment="production", token_provider=lambda: "token")

    monkeypatch.setenv(
        "WORKFLOW_DATABASE_URL",
        "postgresql+psycopg://workflow-id@db.example/process_optimizer?sslmode=require",
    )
    monkeypatch.setenv("WORKFLOW_DATABASE_AUTH_MODE", "entra")
    database = cloud.database_from_environment(
        environment="production", token_provider=lambda: "short-lived-token"
    )
    assert database.engine.dialect.name == "postgresql"


def test_deployment_files_do_not_embed_raw_data_or_credentials() -> None:
    root = Path(__file__).parents[1]
    paths = [
        root / "azure.yaml",
        root / "Dockerfile",
        *sorted((root / "infra" / "terraform").rglob("*.tf")),
        *sorted((root / "deploy").rglob("Dockerfile")),
    ]
    content = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    assert "bpi_challenge_2013_incidents.xes" not in content
    assert "client_secret" not in content
    assert "administrator_password" not in content
