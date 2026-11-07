"""Phase 7B static Foundry deployment and authentication guardrail tests."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from process_optimizer.agents.provider import (
    FoundryModelProvider,
    ModelProviderError,
    model_max_output_tokens,
    model_max_retries,
)
from process_optimizer.deployment.validation import (
    AGENT_SERVICE_DIRECTORY,
    DeploymentValidationError,
    validate_deployment,
)
from process_optimizer.security.auth import (
    AuthenticationError,
    EntraAuthenticationAdapter,
    EntraAuthenticationSettings,
)
from process_optimizer.tools.client import AuthenticatedAggregateApiTools

ROOT = Path(__file__).resolve().parents[1]


def _copy_deployment_files(target: Path) -> Path:
    for relative in ("azure.yaml", "infra/terraform", "deploy"):
        source = ROOT / relative
        destination = target / relative
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(".terraform", "generated"),
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return target


def test_repository_deployment_configuration_passes_static_validation() -> None:
    assert "exclusive_resource_ownership" in validate_deployment(ROOT)


def test_agent_service_directory_cannot_build_any_dockerfile(tmp_path: Path) -> None:
    root = _copy_deployment_files(tmp_path)
    (root / AGENT_SERVICE_DIRECTORY / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(DeploymentValidationError, match="buildable Dockerfile"):
        validate_deployment(root)


def test_agent_service_must_not_point_at_repository_root(tmp_path: Path) -> None:
    root = _copy_deployment_files(tmp_path)
    azure = root / "azure.yaml"
    azure.write_text(
        azure.read_text(encoding="utf-8").replace(
            f"project: {AGENT_SERVICE_DIRECTORY}", "project: ."
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentValidationError, match="project must be"):
        validate_deployment(root)


@pytest.mark.parametrize(
    "replacement",
    ["", "    docker:\n      imagePassthrough: false\n", "    docker:\n      remoteBuild: true\n"],
)
def test_agent_service_requires_image_passthrough(tmp_path: Path, replacement: str) -> None:
    root = _copy_deployment_files(tmp_path)
    azure = root / "azure.yaml"
    azure.write_text(
        azure.read_text(encoding="utf-8").replace(
            "    docker:\n      imagePassthrough: true\n", replacement
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentValidationError, match="imagePassthrough"):
        validate_deployment(root)


def test_azd_must_not_provision_model_deployments(tmp_path: Path) -> None:
    root = _copy_deployment_files(tmp_path)
    azure = root / "azure.yaml"
    azure.write_text(
        azure.read_text(encoding="utf-8").replace(
            "    endpoint: ${AZURE_AI_PROJECT_ENDPOINT}\n",
            "    endpoint: ${AZURE_AI_PROJECT_ENDPOINT}\n    deployments: []\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentValidationError, match="Terraform-owned"):
        validate_deployment(root)


def test_terraform_must_own_foundry_resources(tmp_path: Path) -> None:
    root = _copy_deployment_files(tmp_path)
    (root / "infra/terraform/foundry.tf").unlink()
    with pytest.raises(DeploymentValidationError, match="Foundry account"):
        validate_deployment(root)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        ('"/readiness"]', '"/readiness", "/workflow/*"]', "only the health"),
        ('"Return401"', '"AllowAnonymous"', "reject unauthenticated"),
    ],
)
def test_easy_auth_exempts_only_probe_paths(
    tmp_path: Path, original: str, replacement: str, message: str
) -> None:
    root = _copy_deployment_files(tmp_path)
    main = root / "infra/terraform/main.tf"
    main.write_text(
        main.read_text(encoding="utf-8").replace(original, replacement), encoding="utf-8"
    )
    with pytest.raises(DeploymentValidationError, match=message):
        validate_deployment(root)


def test_empty_service_allowlist_fails_closed() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = EntraAuthenticationSettings(
        tenant_id="test-tenant",
        audience="00000000-0000-0000-0000-000000000001",
        issuer="https://login.microsoftonline.com/test-tenant/v2.0",
        jwks_url="https://example.invalid/keys",
        clock_skew_seconds=0,
    )

    class StaticKeyProvider:
        def get_signing_key(self, token: str) -> Any:
            assert token
            return private_key.public_key()

    token = jwt.encode(
        {
            "iss": settings.issuer,
            "aud": settings.audience,
            "tid": settings.tenant_id,
            "exp": int(time.time()) + 300,
            "oid": "agent-object",
            "idtyp": "app",
            "azp": "any-agent",
            "roles": ["Aggregate.Read"],
        },
        private_key,
        algorithm="RS256",
    )
    adapter = EntraAuthenticationAdapter(settings, StaticKeyProvider())
    with pytest.raises(AuthenticationError, match="not allow-listed"):
        adapter.authenticate(token)


@pytest.mark.parametrize("scope", ["api://workflow", "api://workflow/Aggregate.Read"])
def test_aggregate_scope_must_be_application_default_scope(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    monkeypatch.setenv("AGGREGATE_API_BASE_URL", "https://workflow.example")
    monkeypatch.setenv("AGGREGATE_API_SCOPE", scope)
    with pytest.raises(ValueError, match="/.default"):
        AuthenticatedAggregateApiTools.from_environment()


@pytest.mark.parametrize(
    ("value", "expected"), [(None, None), ("", None), ("2048", 2048), (" 512 ", 512)]
)
def test_model_output_token_cap_is_optional(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int | None
) -> None:
    if value is None:
        monkeypatch.delenv("MODEL_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", value)
    assert model_max_output_tokens() == expected


@pytest.mark.parametrize("value", ["0", "-1", "abc", "40000"])
def test_model_output_token_cap_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", value)
    with pytest.raises(ModelProviderError, match="MODEL_MAX_OUTPUT_TOKENS"):
        model_max_output_tokens()


@pytest.mark.parametrize(
    ("value", "expected"), [(None, 0), ("", 0), ("0", 0), (" 2 ", 2), ("5", 5)]
)
def test_model_max_retries_defaults_to_zero(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    if value is None:
        monkeypatch.delenv("MODEL_MAX_RETRIES", raising=False)
    else:
        monkeypatch.setenv("MODEL_MAX_RETRIES", value)
    assert model_max_retries() == expected


@pytest.mark.parametrize("value", ["-1", "abc", "1.5", "6"])
def test_model_max_retries_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MODEL_MAX_RETRIES", value)
    with pytest.raises(ModelProviderError, match="MODEL_MAX_RETRIES"):
        model_max_retries()


class _FakeOpenAIClient:
    base_url = "https://example.services.ai.azure.com/api/projects/demo/openai/v1/"


class _FakeProjectClient:
    def __init__(self, endpoint: str, credential: Any) -> None:
        del endpoint, credential

    def get_openai_client(self) -> _FakeOpenAIClient:
        return _FakeOpenAIClient()


class _FakeCredential:
    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        raise AssertionError("Model client construction must not request a token")


def _patch_foundry_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langchain_openai")
    pytest.importorskip("azure.ai.projects")
    monkeypatch.setattr("azure.ai.projects.AIProjectClient", _FakeProjectClient)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", _FakeCredential)
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo"
    )
    monkeypatch.setenv("MODEL_DEPLOYMENT_NAME", "synthetic-deployment")
    monkeypatch.delenv("MODEL_MAX_OUTPUT_TOKENS", raising=False)


@pytest.mark.parametrize(("value", "expected"), [(None, 0), ("0", 0), ("1", 1)])
def test_foundry_model_client_receives_explicit_max_retries(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    _patch_foundry_clients(monkeypatch)
    if value is None:
        monkeypatch.delenv("MODEL_MAX_RETRIES", raising=False)
    else:
        monkeypatch.setenv("MODEL_MAX_RETRIES", value)

    model = FoundryModelProvider()._model

    assert model.max_retries == expected
    assert model.root_client.max_retries == expected


def test_foundry_model_provider_fails_closed_on_invalid_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_foundry_clients(monkeypatch)
    monkeypatch.setenv("MODEL_MAX_RETRIES", "many")

    with pytest.raises(ModelProviderError, match="MODEL_MAX_RETRIES"):
        FoundryModelProvider()
