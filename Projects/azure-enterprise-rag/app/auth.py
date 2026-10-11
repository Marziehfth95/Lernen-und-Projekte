from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    scopes: tuple[str, ...]


def _configuration() -> Tuple[str, str, Optional[str], Optional[str]]:
    tenant_id = os.getenv("AUTH_TENANT_ID", "").strip()
    audience = os.getenv("AUTH_CLIENT_ID", "").strip()
    required_role = os.getenv("AUTH_REQUIRED_ROLE", "").strip() or None
    required_scope = os.getenv("AUTH_REQUIRED_SCOPE", "").strip() or None
    if not tenant_id or not audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    return tenant_id, audience, required_role, required_scope


def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A bearer access token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tenant_id, audience, required_role, required_scope = _configuration()
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"

    try:
        signing_key = PyJWKClient(jwks_url, cache_keys=True).get_signing_key_from_jwt(
            credentials.credentials
        )
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud", "tid"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The access token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if claims.get("tid") != tenant_id:
        raise HTTPException(status_code=403, detail="The token tenant is not allowed.")

    user_id = claims.get("oid") or claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="The token has no user identity.")

    roles = tuple(claims.get("roles", []))
    scopes = tuple(filter(None, str(claims.get("scp", "")).split()))
    if required_role and required_role not in roles:
        raise HTTPException(status_code=403, detail="The required application role is missing.")
    if required_scope and required_scope not in scopes:
        raise HTTPException(status_code=403, detail="The required delegated scope is missing.")

    groups = claims.get("groups", [])
    if not isinstance(groups, list):
        groups = []

    return Principal(
        tenant_id=tenant_id,
        user_id=str(user_id),
        groups=tuple(str(group) for group in groups),
        roles=roles,
        scopes=scopes,
    )
