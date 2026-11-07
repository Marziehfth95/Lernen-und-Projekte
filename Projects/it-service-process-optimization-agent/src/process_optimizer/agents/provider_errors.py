"""Sanitized classification of model-provider failures.

Maps SDK exceptions to a fixed category plus metadata that is safe to log and return:
exception type, HTTP status, provider request IDs and the SDK error code. Exception
messages, response bodies, headers and tokens are never read into the result.
Classification is duck-typed so the optional openai/azure SDKs need not be importable.
"""

from __future__ import annotations

import re
from typing import Any, Literal

ProviderErrorCategory = Literal[
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_BAD_REQUEST",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_TIMEOUT",
    "PROVIDER_CONNECTION_FAILED",
    "PROVIDER_CONFIGURATION_ERROR",
    "MODEL_OUTPUT_INVALID",
    "INTERNAL_ERROR",
]

# Informational only: the provider never retries (MODEL_MAX_RETRIES must be 0).
RETRYABLE_CATEGORIES = frozenset(
    {"PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT",
     "PROVIDER_CONNECTION_FAILED"}
)

_TIMEOUT_TYPES = {"APITimeoutError", "TimeoutException", "ReadTimeout", "ConnectTimeout",
                  "WriteTimeout", "PoolTimeout", "ServiceResponseTimeoutError"}
_CONNECTION_TYPES = {"APIConnectionError", "ConnectError", "RemoteProtocolError",
                     "ServiceRequestError", "ServiceResponseError", "NetworkError"}
_AUTH_TYPES = {"AuthenticationError", "PermissionDeniedError", "ClientAuthenticationError"}
_CONFIGURATION_TYPES = {"NotFoundError"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_SAFE_CODE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_REQUEST_ID_HEADERS = ("x-request-id", "apim-request-id", "x-ms-request-id")


def _type_names(error: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(error).__mro__}


def _status(error: BaseException) -> int | None:
    for candidate in (getattr(error, "status_code", None),
                      getattr(getattr(error, "response", None), "status_code", None)):
        if isinstance(candidate, int) and 100 <= candidate <= 599:
            return candidate
    return None


def _safe(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _request_ids(error: BaseException) -> dict[str, str]:
    ids: dict[str, str] = {}
    direct = _safe(getattr(error, "request_id", None), _SAFE_ID)
    if direct:
        ids["provider_request_id"] = direct
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is not None:
        for name in _REQUEST_ID_HEADERS:
            try:
                value = _safe(headers.get(name), _SAFE_ID)
            except Exception:  # noqa: BLE001 - foreign header containers; skip quietly
                value = None
            if value:
                ids.setdefault("provider_request_id", value)
                if name == "apim-request-id":
                    ids["provider_apim_request_id"] = value
    return ids


def classify_provider_exception(
    error: BaseException,
) -> tuple[ProviderErrorCategory, dict[str, str | int]]:
    """Return ``(category, diagnostics)``; diagnostics never contain message text."""
    names = _type_names(error)
    status = _status(error)
    if isinstance(error, TimeoutError) or names & _TIMEOUT_TYPES:
        category: ProviderErrorCategory = "PROVIDER_TIMEOUT"
    elif "CredentialUnavailableError" in names:
        # Subclass of ClientAuthenticationError, but no credential exists to be refused.
        category = "PROVIDER_CONFIGURATION_ERROR"
    elif status in (401, 403) or names & _AUTH_TYPES:
        category = "PROVIDER_AUTH_FAILED"
    elif status == 429 or "RateLimitError" in names:
        category = "PROVIDER_RATE_LIMITED"
    elif status == 404 or names & _CONFIGURATION_TYPES:
        # A missing route or deployment is a configuration fault, not a model answer.
        category = "PROVIDER_CONFIGURATION_ERROR"
    elif status in (400, 409, 413, 422) or names & {"BadRequestError", "UnprocessableEntityError"}:
        category = "PROVIDER_BAD_REQUEST"
    elif (status is not None and status >= 500) or "InternalServerError" in names:
        category = "PROVIDER_UNAVAILABLE"
    elif isinstance(error, ConnectionError) or names & _CONNECTION_TYPES:
        category = "PROVIDER_CONNECTION_FAILED"
    else:
        category = "INTERNAL_ERROR"
    diagnostics: dict[str, str | int] = {
        "exception_type": f"{type(error).__module__}.{type(error).__qualname__}"[:160],
    }
    if status is not None:
        diagnostics["http_status"] = status
    code = _safe(getattr(error, "code", None), _SAFE_CODE)
    if code:
        diagnostics["provider_error_code"] = code
    diagnostics.update(_request_ids(error))
    return category, diagnostics
