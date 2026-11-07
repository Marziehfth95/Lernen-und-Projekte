"""Fail-closed authentication with an explicitly configured local adapter."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    ANALYST = "ANALYST"
    PROCESS_OWNER = "PROCESS_OWNER"
    ADMIN = "ADMIN"


class Permission(StrEnum):
    WORKFLOW_SUBMIT = "Workflow.Submit"
    WORKFLOW_APPROVE = "Workflow.Approve"
    WORKFLOW_AUDIT = "Workflow.Audit"
    AGGREGATE_READ = "Aggregate.Read"
    SIMULATION_EXECUTE = "Simulation.Execute"


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@-]+$")
    roles: frozenset[Role]
    permissions: frozenset[Permission] = frozenset()
    identity_type: Literal["human", "service", "local"] = "local"
    tenant_id: str | None = None
    client_id: str | None = None


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


class AuthenticationAdapter(Protocol):
    def authenticate(self, bearer_token: str | None) -> Principal: ...


class EntraAuthenticationSettings(BaseModel):
    """Environment-driven token-validation configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    jwks_url: str = Field(min_length=1)
    allowed_service_client_ids: frozenset[str] = frozenset()
    clock_skew_seconds: int = Field(default=60, ge=0, le=300)

    @classmethod
    def from_environment(cls) -> EntraAuthenticationSettings:
        tenant_id = os.environ.get("ENTRA_TENANT_ID", "")
        default_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        return cls(
            tenant_id=tenant_id,
            audience=os.environ.get("ENTRA_AUDIENCE", ""),
            issuer=os.environ.get("ENTRA_ISSUER", default_issuer),
            jwks_url=os.environ.get(
                "ENTRA_JWKS_URL",
                f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            ),
            allowed_service_client_ids=frozenset(
                item.strip()
                for item in os.environ.get("ENTRA_ALLOWED_SERVICE_CLIENT_IDS", "").split(",")
                if item.strip()
            ),
        )


class SigningKeyProvider(Protocol):
    def get_signing_key(self, token: str) -> Any: ...


class JwksSigningKeyProvider:
    """Resolve and cache Entra signing keys through PyJWT's documented JWKS client."""

    def __init__(self, jwks_url: str) -> None:
        try:
            import jwt
        except ImportError as error:
            raise RuntimeError("Install the project cloud extra for Entra validation") from error
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True)

    def get_signing_key(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


class EntraAuthenticationAdapter:
    """Validate Entra access tokens in application code as defense in depth."""

    def __init__(
        self,
        settings: EntraAuthenticationSettings,
        key_provider: SigningKeyProvider | None = None,
    ) -> None:
        self.settings = settings
        self._key_provider = key_provider or JwksSigningKeyProvider(settings.jwks_url)

    def authenticate(self, bearer_token: str | None) -> Principal:
        if not bearer_token:
            raise AuthenticationError("Authentication required")
        try:
            import jwt

            key = self._key_provider.get_signing_key(bearer_token)
            claims = jwt.decode(
                bearer_token,
                key=key,
                algorithms=["RS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=self.settings.clock_skew_seconds,
                options={"require": ["exp", "iss", "aud", "tid"]},
            )
            return self._principal_from_claims(claims)
        except AuthenticationError:
            raise
        except Exception as error:
            raise AuthenticationError("Access token validation failed") from error

    def _principal_from_claims(self, claims: dict[str, Any]) -> Principal:
        tenant_id = str(claims.get("tid", ""))
        if tenant_id != self.settings.tenant_id:
            raise AuthenticationError("Access token tenant is not allowed")
        client_id = str(claims.get("azp") or claims.get("appid") or "") or None
        identity_type: Literal["human", "service"] = (
            "service"
            if claims.get("idtyp") == "app" or (client_id is not None and "scp" not in claims)
            else "human"
        )
        # Fail closed: an empty allowlist admits no service identity at all.
        if identity_type == "service" and client_id not in self.settings.allowed_service_client_ids:
            raise AuthenticationError("Service identity is not allow-listed")
        role_claims = claims.get("roles", [])
        if not isinstance(role_claims, list):
            raise AuthenticationError("Token roles claim is invalid")
        scope_claims = str(claims.get("scp", "")).split() if identity_type == "human" else []
        permission_values = set(role_claims) | set(scope_claims)
        permissions = frozenset(
            permission for permission in Permission if permission.value in permission_values
        )
        roles = frozenset(role for role in Role if role.value in set(role_claims))
        actor_id = str(claims.get("oid") or claims.get("sub") or "")
        if not actor_id:
            raise AuthenticationError("Token has no stable actor identifier")
        return Principal(
            actor_id=actor_id,
            roles=roles,
            permissions=permissions,
            identity_type=identity_type,
            tenant_id=tenant_id,
            client_id=client_id,
        )


class LocalAuthenticationAdapter:
    """Development-only opaque bearer tokens mapped to configured test identities."""

    def __init__(self, identities: dict[str, Principal]) -> None:
        if not identities:
            raise ValueError("At least one explicit local test identity is required")
        self._identities = dict(identities)

    @classmethod
    def from_json(cls, value: str) -> LocalAuthenticationAdapter:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError("LOCAL_AUTH_IDENTITIES_JSON must be an object")
        identities = {
            token: Principal.model_validate(identity)
            for token, identity in raw.items()
            if isinstance(token, str) and token
        }
        return cls(identities)

    def authenticate(self, bearer_token: str | None) -> Principal:
        principal = self._identities.get(bearer_token or "")
        if principal is None:
            raise AuthenticationError("Authentication required")
        return principal


class FailClosedAuthenticationAdapter:
    """Production placeholder until verified Microsoft Entra ID validation exists."""

    def authenticate(self, bearer_token: str | None) -> Principal:
        del bearer_token
        raise AuthenticationError("Production authentication is not configured")


def require_role(principal: Principal, *allowed: Role) -> None:
    if not principal.roles.intersection(allowed):
        raise AuthorizationError("The authenticated identity lacks the required role")


def require_permission(principal: Principal, *allowed: Permission) -> None:
    if not principal.permissions.intersection(allowed):
        raise AuthorizationError("The authenticated identity lacks the required permission")
