"""API contract tests."""

import asyncio
import json
import os
import subprocess
import sys

from httpx import ASGITransport, AsyncClient, Response

from process_optimizer.main import app


async def request_health() -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/health")


async def request_readiness(path: str = "/readiness") -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


def test_health_returns_explicit_healthy_status() -> None:
    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert response.headers["content-type"].startswith("application/json")


def test_readiness_has_canonical_path_and_compatibility_alias() -> None:
    canonical = asyncio.run(request_readiness())
    compatibility = asyncio.run(request_readiness("/ready"))

    assert canonical.status_code == 200
    assert canonical.json() == {
        "status": "ready",
        "environment": "local",
        "persistence": "local",
    }
    assert compatibility.json() == canonical.json()
    openapi = app.openapi()
    assert "/readiness" in openapi["paths"]
    assert "/ready" not in openapi["paths"]


def test_production_serves_no_unauthenticated_docs_or_schema() -> None:
    script = (
        "import json; from process_optimizer.main import app; "
        "print(json.dumps([app.openapi_url, app.docs_url, app.redoc_url, "
        "sorted(r.path for r in app.router.routes if hasattr(r, 'path'))]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "APP_ENV": "production"},
        capture_output=True,
        text=True,
        check=True,
    )
    openapi_url, docs_url, redoc_url, paths = json.loads(result.stdout)

    assert (openapi_url, docs_url, redoc_url) == (None, None, None)
    assert not {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"} & set(paths)
