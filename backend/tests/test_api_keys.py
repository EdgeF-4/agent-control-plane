"""Per-project API-key ingest: an agent authenticates and posts a run without
the admin token, and is confined to the one project the key belongs to."""

from __future__ import annotations


def _make_project(client, auth, slug, name, budget_usd=50.0):
    r = client.post("/api/v1/projects", headers=auth,
                    json={"slug": slug, "name": name, "budget_usd": budget_usd})
    assert r.status_code == 201, r.text
    return r.json()


def _mint_key(client, auth, slug, name="ci-agent"):
    r = client.post(f"/api/v1/projects/{slug}/keys", headers=auth, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_key_is_returned_once_and_only_as_prefix_afterward(client, auth):
    _make_project(client, auth, "research", "Research")
    created = _mint_key(client, auth, "research")
    assert created["key"].startswith("acp_")
    assert created["key_prefix"] and created["key_prefix"] in created["key"]

    listed = client.get("/api/v1/projects/research/keys", headers=auth).json()
    assert len(listed) == 1
    # The plaintext is never returned again — only the display prefix survives.
    assert "key" not in listed[0]
    assert listed[0]["key_prefix"] == created["key_prefix"]


def test_agent_ingests_a_run_with_only_its_key(client, auth):
    _make_project(client, auth, "research", "Research")
    key = _mint_key(client, auth, "research")["key"]
    agent = {"Authorization": f"Bearer {key}"}

    # No admin token here — just the project key. project_slug is implied.
    r = client.post("/api/v1/runs/ingest", headers=agent, json={
        "agent_name": "ci-runner",
        "usage": [{"model": "m", "input_tokens": 100, "output_tokens": 40, "cost_usd": 0.05}],
        "tool_calls": [{"server": "web", "tool": "search"}],
        "status": "completed",
    })
    assert r.status_code == 201, r.text
    assert r.json()["run"]["status"] == "completed"

    # The X-API-Key header form works too, via the gateway's parser.
    r2 = client.post("/api/v1/runs/ingest", headers={"X-API-Key": key}, json={
        "usage": [{"model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}],
        "status": "completed",
    })
    assert r2.status_code == 201, r2.text


def test_key_is_scoped_to_its_project(client, auth):
    _make_project(client, auth, "research", "Research")
    _make_project(client, auth, "alpha", "Alpha")
    key = _mint_key(client, auth, "research")["key"]
    agent = {"Authorization": f"Bearer {key}"}

    # Pointing a research key at another project is refused.
    r = client.post("/api/v1/runs/ingest", headers=agent,
                    json={"project_slug": "alpha", "status": "completed"})
    assert r.status_code == 403, r.text


def test_revoked_key_stops_working(client, auth):
    _make_project(client, auth, "research", "Research")
    created = _mint_key(client, auth, "research")
    key = created["key"]
    agent = {"Authorization": f"Bearer {key}"}

    assert client.post("/api/v1/runs/ingest", headers=agent,
                       json={"status": "completed"}).status_code == 201

    d = client.delete(f"/api/v1/projects/research/keys/{created['id']}", headers=auth)
    assert d.status_code == 200, d.text

    after = client.post("/api/v1/runs/ingest", headers=agent, json={"status": "completed"})
    assert after.status_code == 401, after.text


def test_bad_key_is_rejected(client):
    r = client.post("/api/v1/runs/ingest", headers={"Authorization": "Bearer acp_not_a_real_key"},
                    json={"status": "completed"})
    assert r.status_code == 401


def test_creating_keys_requires_admin(client, auth):
    _make_project(client, auth, "research", "Research")
    # A member token cannot mint keys. Build one by... there is only an admin in
    # the bootstrap tenant, so assert the endpoint is admin-guarded via no-auth.
    r = client.post("/api/v1/projects/research/keys", json={"name": "x"})
    assert r.status_code == 401
