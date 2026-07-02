"""Per-run cost timeline / burn rate, and budget alerts pushed over the feed."""

from __future__ import annotations


def _make_project(client, auth, slug, name, budget_usd):
    r = client.post("/api/v1/projects", headers=auth,
                    json={"slug": slug, "name": name, "budget_usd": budget_usd})
    assert r.status_code == 201, r.text
    return r.json()


def test_run_timeline_accumulates_cost_and_reports_burn_rate(client, auth):
    _make_project(client, auth, "research", "Research", 50.0)
    run = client.post("/api/v1/runs/ingest", headers=auth, json={
        "project_slug": "research",
        "usage": [
            {"model": "m", "input_tokens": 1000, "output_tokens": 400, "cost_usd": 0.25},
            {"model": "m", "input_tokens": 500, "output_tokens": 200, "cost_usd": 0.15},
        ],
        "status": "completed",
    }).json()["run"]

    tl = client.get(f"/api/v1/runs/{run['id']}/timeline", headers=auth).json()
    # Cumulative cost is monotonic and ends at the run total ($0.40 = 400000 micro).
    cums = [p["cumulative_micro"] for p in tl["points"]]
    assert cums == sorted(cums)
    assert tl["total_cost_micro"] == 400_000
    assert tl["total_tokens"] == 2100
    assert "burn_rate_usd_per_min" in tl
    assert tl["burn_rate_usd_per_min"] >= 0


def test_budget_threshold_and_cap_alerts_reach_the_live_feed(client, auth, token):
    # A $5 cap with an 80% warn threshold: the first charge warns, the second caps.
    r = client.post("/api/v1/projects", headers=auth, json={
        "slug": "alpha", "name": "Alpha", "budget_usd": 5.0, "budget_warn_threshold": 0.8,
    })
    assert r.status_code == 201, r.text

    with client.websocket_connect(f"/api/v1/live?token={token}") as ws:
        assert ws.receive_json()["type"] == "connected"
        client.post("/api/v1/runs/ingest", headers=auth, json={
            "project_slug": "alpha",
            "usage": [
                {"model": "big", "input_tokens": 9000, "output_tokens": 4000, "cost_usd": 4.20},
                {"model": "big", "input_tokens": 9000, "output_tokens": 4000, "cost_usd": 1.50},
            ],
            "status": "completed",
        })
        kinds = []
        for _ in range(30):
            msg = ws.receive_json()
            if msg["type"] == "budget.alert":
                kinds.append(msg["kind"])
                assert msg["project"] == "alpha"
                assert msg["message"]
            if "cap" in kinds:
                break
    # The threshold warning fired first (at 84%), then the cap when it blew past $5.
    assert "threshold" in kinds
    assert "cap" in kinds
