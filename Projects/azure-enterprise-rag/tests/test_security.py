from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def test_missing_token_is_unauthorized():
    from app.auth import get_current_principal

    with pytest.raises(HTTPException) as exc:
        get_current_principal(None)

    assert exc.value.status_code == 401


def test_claims_are_mapped_to_principal(monkeypatch):
    from app import auth

    monkeypatch.setenv("AUTH_TENANT_ID", "tenant-a")
    monkeypatch.setenv("AUTH_CLIENT_ID", "api-a")
    monkeypatch.setenv("AUTH_REQUIRED_SCOPE", "access_as_user")
    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key="key")
        ),
    )
    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "tid": "tenant-a",
            "oid": "user-a",
            "groups": ["group-a"],
            "scp": "access_as_user",
        },
    )

    principal = auth.get_current_principal(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    )

    assert principal.tenant_id == "tenant-a"
    assert principal.user_id == "user-a"
    assert principal.groups == ("group-a",)


def test_retrieval_query_enforces_tenant_and_acl(monkeypatch):
    from app import main
    from app.auth import Principal

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_connection():
        yield Connection()

    monkeypatch.setattr(main, "db_connection", fake_connection)
    principal = Principal("tenant-a", "user-a", ("group-a",), (), ())
    request = main.AskRequest(question="test", filename="allowed.txt")

    main.retrieve_authorized_chunks(principal, [0.1, 0.2], request)

    assert "c.tenant_id = %s" in captured["query"]
    assert "d.owner_id = %s" in captured["query"]
    assert "rag_document_permissions" in captured["query"]
    assert "d.filename = %s" in captured["query"]
    assert captured["params"][:4] == [
        "tenant-a",
        "user-a",
        "user-a",
        ["group-a"],
    ]


def test_ask_without_authorized_results_returns_http_404(monkeypatch):
    from app import main
    from app.auth import Principal

    openai_client = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1, 0.2])]
            )
        )
    )
    monkeypatch.setattr(main, "retrieve_authorized_chunks", lambda *_args: [])
    request = SimpleNamespace(state=SimpleNamespace(request_id="request-1"))

    with pytest.raises(HTTPException) as exc:
        main.ask_question(
            main.AskRequest(question="test"),
            request,
            Principal("tenant-a", "user-a", (), (), ()),
            openai_client,
        )

    assert exc.value.status_code == 404
    assert "WAHRER_FEHLER" not in str(exc.value.detail)
