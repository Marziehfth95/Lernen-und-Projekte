"""Static deployment checks that do not contact Azure or mutate cloud state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

from process_optimizer.deployment.aggregate_package import verify_deployable_package

AGENT_SERVICE_DIRECTORY = "deploy/agent-service"


class DeploymentValidationError(ValueError):
    """Raised when local deployment policy checks fail."""


def validate_deployment(project_root: Path, *, require_package: bool = False) -> tuple[str, ...]:
    root = Path(project_root)
    errors: list[str] = []
    azure_path = root / "azure.yaml"
    azure_text = azure_path.read_text(encoding="utf-8")
    azure = yaml.safe_load(azure_text)
    services = azure.get("services", {})
    project = services.get("ai-project", {})
    agent = services.get("process-optimizer-agent", {})

    required_versions = azure.get("requiredVersions", {})
    if not azure_text.startswith("# yaml-language-server: $schema="):
        errors.append("azure.yaml has no official schema annotation")
    if required_versions.get("azd") != ">=1.27.1":
        errors.append("azure.yaml does not pin the documented minimum azd version")
    extensions = required_versions.get("extensions", {})
    for name in ("azure.ai.agents", "azure.ai.projects"):
        if name not in extensions:
            errors.append(f"azure.yaml is missing the {name} extension constraint")
    if set(services) != {"ai-project", "process-optimizer-agent"}:
        errors.append("azd may own only the adopted Foundry project and Hosted Agent")
    if (
        project.get("host") != "azure.ai.project"
        or project.get("endpoint") != "${AZURE_AI_PROJECT_ENDPOINT}"
    ):
        errors.append("azd must adopt an explicitly configured existing Foundry project")
    if "deployments" in project or "network" in project:
        errors.append("azd must not provision Terraform-owned/shared Foundry infrastructure")
    required_agent_values = {
        "host": "azure.ai.agent",
        "project": AGENT_SERVICE_DIRECTORY,
        "language": "docker",
        "kind": "hosted",
        "image": "${HOSTED_AGENT_IMAGE}",
    }
    for key, expected in required_agent_values.items():
        if agent.get(key) != expected:
            errors.append(f"Hosted Agent {key} must be {expected}")
    if agent.get("startupCommand") != "python -m process_optimizer.agents.hosting":
        errors.append("Hosted Agent startup command must reference the packaged Python module")
    if agent.get("uses") != ["ai-project"]:
        errors.append("Hosted Agent must depend only on the adopted Foundry project")
    if agent.get("protocols") != [{"protocol": "invocations", "version": "2.0.0"}]:
        errors.append("Hosted Agent must declare Invocations protocol 2.0.0")
    env = agent.get("env", {})
    for name in (
        "MODEL_DEPLOYMENT_NAME",
        "AGGREGATE_API_BASE_URL",
        "AGGREGATE_API_SCOPE",
        "ALLOW_PAID_MODEL_CALLS",
    ):
        if name not in env:
            errors.append(f"Hosted Agent environment is missing {name}")
    if "FOUNDRY_PROJECT_ENDPOINT" in env:
        errors.append("Foundry-reserved project endpoint must be platform-injected")
    if "infra" in azure:
        errors.append("azure.yaml must not make azd an owner of Terraform infrastructure")
    if "codeConfiguration" in agent:
        errors.append("Hosted Agent must deploy only the prebuilt image, not codeConfiguration")
    # Without passthrough, azd's Docker framework falls back to pack/Oryx when the
    # service directory has no Dockerfile, even though the image is prebuilt.
    if agent.get("docker") != {"imagePassthrough": True}:
        errors.append("Hosted Agent docker settings must be exactly imagePassthrough: true")
    service_dir = root / AGENT_SERVICE_DIRECTORY
    if not service_dir.is_dir():
        errors.append("Hosted Agent service directory is missing")
    elif any(
        path.name.lower().startswith("dockerfile") or path.name.lower() == "containerfile"
        for path in service_dir.rglob("*")
    ):
        errors.append("Hosted Agent service directory must not contain a buildable Dockerfile")

    terraform = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "infra/terraform").glob("*.tf"))
    )
    if 'managed-by  = "terraform"' not in terraform:
        errors.append("Terraform resources lack an explicit ownership tag")
    if 'resource "azurerm_container_app" "workflow"' not in terraform:
        errors.append("Terraform must remain the Workflow API owner")
    if 'resource "azurerm_role_assignment" "foundry_project_acr_reader"' not in terraform:
        errors.append("Terraform must own the Foundry project identity ACR assignment")
    if 'path      = "/readiness"' not in terraform:
        errors.append("Workflow Container App must use the canonical readiness probe")
    if 'unauthenticatedClientAction = "Return401"' not in terraform:
        errors.append("Workflow Easy Auth must reject unauthenticated requests")
    excluded_paths = re.findall(r"excludedPaths\s*=\s*\[([^\]]*)\]", terraform)
    if any(
        set(re.findall(r'"([^"]*)"', paths)) - {"/health", "/readiness"}
        for paths in excluded_paths
    ):
        errors.append("Workflow Easy Auth may exclude only the health and readiness probes")
    if "LOCAL_AUTH_IDENTITIES_JSON" in terraform or 'value = "local"' in terraform:
        errors.append("Terraform production configuration enables development authentication")
    for resource, purpose in (
        ('resource "azapi_resource" "foundry_account"', "Foundry account"),
        ('resource "azapi_resource" "foundry_project"', "Foundry project"),
        ('resource "azapi_resource" "model_deployment"', "model deployment"),
        ('resource "azurerm_consumption_budget_resource_group" "demo"', "cost budget"),
    ):
        if resource not in terraform:
            errors.append(f"Terraform must own the {purpose}")
    if "disableLocalAuth = true" not in terraform:
        errors.append("Foundry account must disable key-based data-plane authentication")
    if 'versionUpgradeOption = "NoAutoUpgrade"' not in terraform:
        errors.append("Model deployment must not auto-upgrade the evaluated model version")
    if "daily_quota_gb" not in terraform:
        errors.append("Log Analytics ingestion must have a daily cost cap")
    # Azure adds the implicit Consumption profile; any dedicated (billed) profile is rejected.
    profile_types = re.findall(r'workload_profile_type\s*=\s*"([^"]*)"', terraform)
    if terraform.count("workload_profile {") != len(profile_types) or any(
        profile_type != "Consumption" for profile_type in profile_types
    ):
        errors.append("Container Apps environment must stay consumption-only for the demo")
    if not re.search(
        r'variable "workflow_persistence" \{[^}]*default\s*=\s*"postgresql"', terraform
    ):
        errors.append("Durable PostgreSQL must remain the default workflow persistence")

    for service in ("agent", "workflow"):
        dockerfile = (root / f"deploy/{service}/Dockerfile").read_text(encoding="utf-8")
        if "USER app" not in dockerfile:
            errors.append(f"{service} image does not drop root privileges")
        if "HEALTHCHECK" not in dockerfile:
            errors.append(f"{service} image has no health check")
        if f"deploy/{service}/requirements.lock" not in dockerfile:
            errors.append(f"{service} image does not use its dependency lock")
        if dockerfile.count("--require-hashes") < 2:
            errors.append(f"{service} image does not enforce locked dependencies")
        if "deploy/pip-bootstrap.lock" not in dockerfile:
            errors.append(f"{service} image does not use the pinned pip bootstrap lock")
        if "SECRET" in dockerfile.upper() or "PASSWORD=" in dockerfile.upper():
            errors.append(f"{service} image appears to embed a secret")
    workflow_docker = (root / "deploy/workflow/Dockerfile").read_text(encoding="utf-8")
    agent_docker = (root / "deploy/agent/Dockerfile").read_text(encoding="utf-8")
    if "deploy/aggregate-package/generated/" not in workflow_docker:
        errors.append("Workflow image does not copy the generated governed package")
    if "aggregate-package" in agent_docker:
        errors.append("Hosted Agent image must not contain aggregate artifacts")

    sql = (root / "deploy/postgresql/bootstrap.sql.template").read_text(encoding="utf-8")
    forbidden_sql = ("GRANT ALL", "SUPERUSER", "CREATEDB", "CREATEROLE")
    if any(token in sql.upper() for token in forbidden_sql):
        errors.append("PostgreSQL template contains unnecessary elevated permissions")
    for required_sql in (
        "pgaadauth_create_principal_with_oid",
        "runtime_principal_name",
        "migration_principal_name",
        "GRANT SELECT, INSERT ON TABLE",
        "GRANT SELECT, INSERT, UPDATE ON TABLE",
    ):
        if required_sql not in sql:
            errors.append(f"PostgreSQL template is missing {required_sql}")
    if " ON ALL TABLES " in sql.upper() or " DELETE " in sql.upper():
        errors.append("PostgreSQL runtime grants are broader than the current workflow requires")

    if require_package:
        try:
            verify_deployable_package(root / "deploy/aggregate-package/generated")
        except (OSError, ValueError) as error:
            errors.append(f"Aggregate deployment package is invalid: {error}")
    if errors:
        raise DeploymentValidationError("; ".join(errors))
    checks = [
        "unified_foundry_yaml",
        "exclusive_resource_ownership",
        "hardened_container_definitions",
        "production_authentication_only",
        "least_privilege_database_template",
    ]
    if require_package:
        checks.append("verified_aggregate_package")
    return tuple(checks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--require-aggregate-package", action="store_true")
    args = parser.parse_args()
    checks = validate_deployment(
        args.project_root,
        require_package=args.require_aggregate_package,
    )
    print(json.dumps({"status": "pass", "checks": checks}, sort_keys=True))


if __name__ == "__main__":
    main()
