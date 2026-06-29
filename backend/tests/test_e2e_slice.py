"""The phase-1 end-to-end slice, exercised through the public API.

Ingest a run, enforce a cost cap (kill switch), record allow/deny decisions,
write a tamper-evident audit trail, and surface all of it.
"""

from __future__ import annotations

import json
from pathlib import Path


def _make_project(client, auth, slug, name, budget_usd):
    r = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"slug": slug, "name": name, "budget_usd": budget_usd},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_healthy_run_records_allow_and_deny_decisions(client, auth):
    _make_project(client, auth, "research", "Research", 50.0)
    r = client.post(
        "/api/v1/runs/ingest",
        headers=auth,
        json={
            "project_slug": "research",
            "agent_name": "researcher",
            "usage": [
                {"provider": "local", "model": "assistant", "input_tokens": 1000,
                 "output_tokens": 400, "cost_usd": 0.25}
            ],
            "tool_calls": [
                {"server": "web", "tool": "search", "arguments": {"q": "x"}},
                {"server": "db", "tool": "delete_records", "arguments": {"t": "leads"}},
            ],
            "status": "completed",
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()["run"]
    assert run["status"] == "completed"

    decisions = client.get(f"/api/v1/runs/{run['id']}/decisions", headers=auth).json()
    by_tool = {d["tool"]: d["decision"] for d in decisions}
    assert by_tool["search"] == "allow"
    assert by_tool["delete_records"] == "deny"  # matches the *__delete* deny rule


def test_budget_cap_kills_the_run(client, auth):
    _make_project(client, auth, "alpha", "Alpha Pilot", 5.0)
    r = client.post(
        "/api/v1/runs/ingest",
        headers=auth,
        json={
            "project_slug": "alpha",
            "agent_name": "pilot",
            "usage": [
                {"model": "big", "input_tokens": 9000, "output_tokens": 4000, "cost_usd": 3.10},
                {"model": "big", "input_tokens": 9000, "output_tokens": 4000, "cost_usd": 3.40},
            ],
            "status": "completed",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run"]["status"] == "killed"
    # The second usage report is the one that trips the cap.
    last = body["outcomes"][-1]
    assert last["allowed"] is False
    assert last["state"] == "deny"
    assert "kill" in last["alerts"]

    budgets = client.get("/api/v1/budgets", headers=auth).json()
    alpha = next(b for b in budgets if b["scope_id"].endswith("/alpha"))
    assert alpha["state"] == "deny"
    assert alpha["spent_micro"] >= alpha["limit_micro"]


def test_audit_trail_is_tamper_evident(client, auth, settings):
    _make_project(client, auth, "research", "Research", 50.0)
    r = client.post(
        "/api/v1/runs/ingest",
        headers=auth,
        json={
            "project_slug": "research",
            "usage": [{"model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}],
            "status": "completed",
        },
    )
    run = r.json()["run"]
    external_id = run["external_run_id"]

    # The chain verifies cleanly to start with.
    v = client.get(f"/api/v1/runs/{run['id']}/verify", headers=auth).json()
    assert v["ok"] is True
    assert v["event_count"] >= 3

    # Tamper with the on-disk record and the chain must report a break.
    log_path = Path(settings.data_dir, "runs", f"{external_id}.jsonl")
    lines = log_path.read_text().splitlines()
    record = json.loads(lines[1])
    record["payload"] = {"tampered": True}
    lines[1] = json.dumps(record)
    log_path.write_text("\n".join(lines) + "\n")

    v2 = client.get(f"/api/v1/runs/{run['id']}/verify", headers=auth).json()
    assert v2["ok"] is False
    assert v2["broken_index"] is not None


def test_audit_endpoint_and_overview(client, auth):
    _make_project(client, auth, "research", "Research", 50.0)
    client.post(
        "/api/v1/runs/ingest",
        headers=auth,
        json={
            "project_slug": "research",
            "usage": [{"model": "m", "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.05}],
            "tool_calls": [{"server": "web", "tool": "search"}],
            "status": "completed",
        },
    )
    audit = client.get("/api/v1/audit", headers=auth).json()
    assert len(audit) >= 3
    assert all(entry["hash"] for entry in audit)

    overview = client.get("/api/v1/overview", headers=auth).json()
    assert overview["runs"]["total"] == 1
    assert overview["runs"]["completed"] == 1
    assert overview["total_spend_usd"] > 0
    assert overview["audit_event_count"] >= 3


def test_eval_run_and_baseline(client, auth):
    r = client.post("/api/v1/evals/run", headers=auth)
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["total"] == 4
    assert first["pass_rate"] == 1.0

    client.post("/api/v1/evals/run", headers=auth)
    evals = client.get("/api/v1/evals", headers=auth).json()
    assert len(evals) == 2


def test_live_websocket_receives_run_frames(client, auth, token):
    _make_project(client, auth, "research", "Research", 50.0)
    with client.websocket_connect(f"/api/v1/live?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"
        client.post(
            "/api/v1/runs/ingest",
            headers=auth,
            json={
                "project_slug": "research",
                "usage": [{"model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}],
                "status": "completed",
            },
        )
        kinds = set()
        for _ in range(6):
            msg = ws.receive_json()
            kinds.add(msg["type"])
            if "run.completed" in kinds:
                break
        assert "run.created" in kinds
