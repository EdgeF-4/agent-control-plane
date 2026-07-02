"""Admin surface: tenants (superadmin), users (admin), project update/delete."""

from __future__ import annotations


def test_superadmin_can_create_and_list_tenants(client, auth):
    r = client.post("/api/v1/admin/tenants", headers=auth, json={
        "slug": "globex", "name": "Globex", "admin_email": "root@globex.test",
        "admin_password": "another-strong-pw",
    })
    assert r.status_code == 201, r.text
    tenants = client.get("/api/v1/admin/tenants", headers=auth).json()
    slugs = {t["slug"] for t in tenants}
    assert {"acme", "globex"} <= slugs

    # The new tenant's admin can log in and is isolated to its own tenant.
    login = client.post("/api/v1/auth/login", json={
        "email": "root@globex.test", "password": "another-strong-pw"})
    assert login.status_code == 200
    globex_auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    # Not a superadmin -> cannot list tenants.
    assert client.get("/api/v1/admin/tenants", headers=globex_auth).status_code == 403


def test_admin_manages_users_within_tenant(client, auth):
    created = client.post("/api/v1/admin/users", headers=auth, json={
        "email": "analyst@acme.test", "password": "analyst-strong-pw",
        "name": "Analyst", "role": "member",
    })
    assert created.status_code == 201, created.text
    uid = created.json()["id"]
    assert created.json()["superadmin"] is False

    users = client.get("/api/v1/admin/users", headers=auth).json()
    assert any(u["email"] == "analyst@acme.test" for u in users)

    # Promote, then the new user (still member creds) can log in.
    promoted = client.patch(f"/api/v1/admin/users/{uid}", headers=auth, json={"role": "admin"})
    assert promoted.json()["role"] == "admin"

    login = client.post("/api/v1/auth/login",
                        json={"email": "analyst@acme.test", "password": "analyst-strong-pw"})
    assert login.status_code == 200

    deleted = client.delete(f"/api/v1/admin/users/{uid}", headers=auth)
    assert deleted.status_code == 200
    assert not any(u["id"] == uid for u in client.get("/api/v1/admin/users", headers=auth).json())


def test_admin_cannot_delete_or_demote_self(client, auth):
    me = client.get("/api/v1/auth/me", headers=auth).json()
    assert client.delete(f"/api/v1/admin/users/{me['id']}", headers=auth).status_code == 409
    assert client.patch(f"/api/v1/admin/users/{me['id']}", headers=auth,
                        json={"role": "member"}).status_code == 409


def test_project_update_and_delete(client, auth):
    client.post("/api/v1/projects", headers=auth,
                json={"slug": "research", "name": "Research", "budget_usd": 50.0})
    upd = client.patch("/api/v1/projects/research", headers=auth,
                       json={"budget_usd": 120.0, "name": "Research (raised)"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["budget_limit_micro"] == 120_000_000
    assert upd.json()["name"] == "Research (raised)"
    # The raised cap is reflected in the live budget view.
    budgets = client.get("/api/v1/budgets", headers=auth).json()
    research = next(b for b in budgets if b["scope_id"].endswith("/research"))
    assert research["limit_micro"] == 120_000_000

    d = client.delete("/api/v1/projects/research", headers=auth)
    assert d.status_code == 200
    assert client.get("/api/v1/projects", headers=auth).json() == []


def test_user_routes_require_admin(client):
    assert client.get("/api/v1/admin/users").status_code == 401
    assert client.get("/api/v1/admin/tenants").status_code == 401
