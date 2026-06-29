"""Auth and tenant-scoping behaviour."""

from __future__ import annotations


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_returns_token_and_me(client, auth):
    r = client.get("/api/v1/auth/me", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "admin@acme.test"
    assert body["role"] == "admin"


def test_bad_password_is_rejected(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@acme.test", "password": "wrong"},
    )
    assert r.status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/api/v1/overview").status_code == 401
    assert client.get("/api/v1/runs").status_code == 401


def test_tampered_token_is_rejected(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
