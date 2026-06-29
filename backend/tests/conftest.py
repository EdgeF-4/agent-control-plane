"""Test fixtures: a fresh app, store and engine set per test."""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

_POLICY = {
    "default": "allow",
    "rules": [
        {"client_id": "*", "roles": [], "allow": [], "deny": ["*__delete*", "shell__*"]}
    ],
}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database={"url": f"sqlite+aiosqlite:///{tmp_path}/cp.db"},
        auth={"jwt_secret": secrets.token_hex(32)},
        data_dir=str(tmp_path / "data"),
        engines={"policy": _POLICY},
        bootstrap={
            "tenant_slug": "acme",
            "tenant_name": "Acme Corp",
            "admin_email": "admin@acme.test",
            "admin_password": "correct horse battery staple",
        },
    )


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@acme.test", "password": "correct horse battery staple"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def auth(token):
    return {"Authorization": f"Bearer {token}"}
