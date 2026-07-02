"""SIEM forwarding surface: sink health, delivery stats, presets, and DLQ replay."""

from __future__ import annotations

import json
from pathlib import Path


def _ingest(client, auth):
    client.post("/api/v1/projects", headers=auth,
                json={"slug": "research", "name": "Research", "budget_usd": 50.0})
    client.post("/api/v1/runs/ingest", headers=auth, json={
        "project_slug": "research",
        "usage": [{"model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}],
        "tool_calls": [{"server": "web", "tool": "search"}],
        "status": "completed",
    })


def test_status_reports_default_file_sink_and_stats(client, auth):
    _ingest(client, auth)
    status = client.get("/api/v1/siem/status", headers=auth).json()
    names = {s["name"] for s in status["sinks"]}
    assert "local-audit" in names
    assert status["sinks"][0]["type"] == "file"
    # Forwarding actually happened during the ingest above.
    assert status["stats"]["processed"] > 0
    assert status["stats"]["delivered"] > 0
    assert status["dlq_count"] == 0
    # Presets cover the four network sinks plus file.
    preset_types = {p["type"] for p in status["presets"]}
    assert {"file", "splunk_hec", "elasticsearch", "datadog", "webhook"} <= preset_types


def test_sink_test_is_admin_only_and_reports_health(client, auth):
    _ingest(client, auth)
    # File sink self-test writes a synthetic event and reports ready.
    r = client.post("/api/v1/siem/sinks/local-audit/test", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    unknown = client.post("/api/v1/siem/sinks/does-not-exist/test", headers=auth)
    assert unknown.status_code == 200
    assert unknown.json()["ok"] is False

    # Not admin (no token) -> rejected before touching the sink.
    assert client.post("/api/v1/siem/sinks/local-audit/test").status_code == 401


def test_dlq_lists_and_replays_dead_letters(client, auth, settings):
    _ingest(client, auth)
    # Seed a dead-letter entry the way the pipeline would, through the engine's DLQ.
    hub = client.app.state.hub
    hub.siem.dlq.put(
        "local-audit",
        [{"event_id": "x", "message": "replay me"}],
        RuntimeError("sink was down"),
        attempts=3,
    )
    entries = client.get("/api/v1/siem/dlq", headers=auth).json()
    assert len(entries) == 1
    assert entries[0]["sink"] == "local-audit"
    assert entries[0]["event_count"] == 1
    assert entries[0]["attempts"] == 3

    # Replaying re-delivers to the (now healthy) file sink and clears the queue.
    result = client.post("/api/v1/siem/dlq/replay", headers=auth).json()
    assert result["replayed"] == 1
    assert client.get("/api/v1/siem/dlq", headers=auth).json() == []

    # The replayed event really landed in the audit file.
    audit_file = Path(settings.data_dir, "siem", "audit.ndjson")
    body = audit_file.read_text()
    assert "replay me" in body
    # And it is valid NDJSON.
    assert all(json.loads(line) for line in body.splitlines() if line.strip())
