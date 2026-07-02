"""WORM anchoring: Merkle roots, the hash-chained anchor log, drift detection,
the SigV4 S3 upload, the routes, and the CLI."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import anchor
from app.anchor import (
    AnchorStore,
    Anchorer,
    build_anchor_record,
    merkle_root,
    sign_s3_put,
    verify_anchor_log,
    verify_runs_against_anchor,
)
from app.config import Settings
from app.engines import EngineHub
from app.main import create_app
from flight_recorder.hashchain import GENESIS_HASH


def _settings(tmp_path) -> Settings:
    return Settings(
        database={"url": f"sqlite+aiosqlite:///{tmp_path}/a.db"},
        auth={"jwt_secret": secrets.token_hex(16)},
        data_dir=str(tmp_path / "data"),
        engines={"policy": {"default": "allow", "rules": []}},
        eval_scheduler_seconds=3600,
        bootstrap={"tenant_slug": "acme", "tenant_name": "Acme",
                   "admin_email": "admin@acme.test",
                   "admin_password": "correct horse battery staple"},
    )


# --- Merkle ---------------------------------------------------------------- #
def test_merkle_root_properties():
    assert merkle_root([]) == GENESIS_HASH
    a = "a" * 64
    assert merkle_root([a]) == a  # a single leaf is the root
    # Order matters, and the root is deterministic.
    r1 = merkle_root(["11" * 32, "22" * 32, "33" * 32])
    r2 = merkle_root(["11" * 32, "22" * 32, "33" * 32])
    r3 = merkle_root(["33" * 32, "22" * 32, "11" * 32])
    assert r1 == r2 != r3
    assert len(r1) == 64


# --- anchor store chain ---------------------------------------------------- #
def test_anchor_store_chains_and_detects_tampering(tmp_path):
    store = AnchorStore(str(tmp_path / "anchors.ndjson"))
    r0 = store.append(build_anchor_record({"log_dir": str(tmp_path / "empty")}, now_iso="t0"))
    r1 = store.append(build_anchor_record({"log_dir": str(tmp_path / "empty")}, now_iso="t1"))
    assert r0["seq"] == 0 and r0["prev_anchor_hash"] == GENESIS_HASH
    assert r1["prev_anchor_hash"] == r0["anchor_hash"]
    assert verify_anchor_log(store)["ok"] is True

    # Tamper with the first record on disk: the log no longer verifies.
    lines = Path(store.path).read_text().splitlines()
    rec = json.loads(lines[0])
    rec["run_count"] = 999
    lines[0] = json.dumps(rec, sort_keys=True)
    Path(store.path).write_text("\n".join(lines) + "\n")
    assert verify_anchor_log(store)["ok"] is False


# --- build over real runs + drift ------------------------------------------ #
def _make_runs(hub: EngineHub, *ids: str) -> None:
    for rid in ids:
        hub.open_run(rid, input={"task": rid})
        hub.emit(rid, "model_response", {"m": "x"}, tokens={"total": 10}, cost_usd=0.01)
        hub.end_run(rid, status="ok", output={"done": True})


def test_build_anchor_is_stable_over_runs(tmp_path):
    hub = EngineHub(_settings(tmp_path))
    try:
        _make_runs(hub, "run-a", "run-b")
        rec1 = build_anchor_record(hub.recorder_config, now_iso="t")
        rec2 = build_anchor_record(hub.recorder_config, now_iso="t")
        assert rec1["run_count"] == 2
        assert rec1["merkle_root"] == rec2["merkle_root"] != GENESIS_HASH
    finally:
        hub.close()


def test_anchor_verify_detects_run_drift(tmp_path):
    settings = _settings(tmp_path)
    hub = EngineHub(settings)
    try:
        _make_runs(hub, "run-a", "run-b")
        anchorer = Anchorer(settings, hub.recorder_config)
        anchorer.run_once(now_iso="t0")
        assert anchorer.verify()["ok"] is True

        # Edit a run file after anchoring: its head no longer matches the anchor.
        run_file = Path(hub.recorder_config["log_dir"]) / "run-a.jsonl"
        with open(run_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": 999, "type": "log", "hash": "de" * 32}) + "\n")

        result = anchorer.verify()
        assert result["ok"] is False
        assert any(d["run_id"] == "run-a" for d in result["drift"])
    finally:
        hub.close()


def test_verify_flags_a_deleted_run(tmp_path):
    settings = _settings(tmp_path)
    hub = EngineHub(settings)
    try:
        _make_runs(hub, "run-a")
        anchor_rec = build_anchor_record(hub.recorder_config, now_iso="t")
        (Path(hub.recorder_config["log_dir"]) / "run-a.jsonl").unlink()
        drift = verify_runs_against_anchor(hub.recorder_config, anchor_rec)
        assert len(drift) == 1 and drift[0]["run_id"] == "run-a"
    finally:
        hub.close()


# --- SigV4 S3 upload ------------------------------------------------------- #
def test_sigv4_signature_shape_and_determinism():
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    body = b'{"merkle_root":"abc"}'
    url, headers = sign_s3_put(
        endpoint_url="https://s3.example.com", region="us-east-1", bucket="audit",
        key="anchors/anchor-00000000.json", body=body,
        access_key="AKIDEXAMPLE", secret_key="secretkey", now=when,
    )
    assert url == "https://s3.example.com/audit/anchors/anchor-00000000.json"
    auth = headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260102/us-east-1/s3/aws4_request")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in auth
    sig = auth.split("Signature=")[1]
    assert len(sig) == 64 and int(sig, 16) >= 0  # 64 hex chars
    assert headers["x-amz-content-sha256"] == __import__("hashlib").sha256(body).hexdigest()
    # Deterministic for identical inputs.
    _, headers2 = sign_s3_put(
        endpoint_url="https://s3.example.com", region="us-east-1", bucket="audit",
        key="anchors/anchor-00000000.json", body=body,
        access_key="AKIDEXAMPLE", secret_key="secretkey", now=when,
    )
    assert headers == headers2


class _S3Handler(BaseHTTPRequestHandler):
    captured: dict = {}

    def log_message(self, *a):
        pass

    def do_PUT(self):
        length = int(self.headers.get("content-length", 0))
        type(self).captured = {
            "path": self.path,
            "authorization": self.headers.get("authorization"),
            "body": self.rfile.read(length),
        }
        self.send_response(200)
        self.end_headers()


def test_s3_put_uploads_signed_object(tmp_path, monkeypatch):
    _S3Handler.captured = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _S3Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        monkeypatch.setenv("ACP_S3_KEY", "AKIDEXAMPLE")
        monkeypatch.setenv("ACP_S3_SECRET", "secretkey")
        settings = Settings(
            auth={"jwt_secret": "x"},
            anchor={
                "s3_endpoint_url": f"http://{host}:{port}", "s3_bucket": "audit",
                "s3_region": "us-east-1", "s3_prefix": "anchors",
                "s3_access_key_env": "ACP_S3_KEY", "s3_secret_key_env": "ACP_S3_SECRET",
            },
        )
        result = anchor.s3_put(settings.anchor, "anchors/x.json", b'{"a":1}')
        assert result["ok"] is True and result["status"] == 200
        assert _S3Handler.captured["path"] == "/audit/anchors/x.json"
        assert _S3Handler.captured["authorization"].startswith("AWS4-HMAC-SHA256")
        assert _S3Handler.captured["body"] == b'{"a":1}'
    finally:
        server.shutdown()
        server.server_close()


# --- routes ---------------------------------------------------------------- #
def test_anchor_routes(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        tok = c.post("/api/v1/auth/login", json={
            "email": "admin@acme.test", "password": "correct horse battery staple"}).json()
        auth = {"Authorization": f"Bearer {tok['access_token']}"}
        # Create a project + a run so the anchor has something to cover.
        c.post("/api/v1/projects", headers=auth,
               json={"slug": "research", "name": "Research", "budget_usd": 50})
        ing = c.post("/api/v1/runs/ingest", headers=auth,
                     json={"project_slug": "research", "agent_name": "a", "status": "completed",
                           "usage": [{"model": "m", "input_tokens": 5, "output_tokens": 5, "cost_usd": 0.01}]})
        assert ing.status_code == 201, ing.text
        created = c.post("/api/v1/anchors", headers=auth)
        assert created.status_code == 201, created.text
        assert created.json()["run_count"] >= 1
        listed = c.get("/api/v1/anchors", headers=auth).json()
        assert listed["count"] == 1
        verified = c.get("/api/v1/anchors/verify", headers=auth).json()
        assert verified["ok"] is True


# --- CLI ------------------------------------------------------------------- #
def test_cli_anchor_and_verify(tmp_path, monkeypatch, capsys):
    cfg = {
        "database": {"url": f"sqlite+aiosqlite:///{tmp_path}/c.db"},
        "auth": {"jwt_secret": secrets.token_hex(16)},
        "data_dir": str(tmp_path / "data"),
        "engines": {"policy": {"default": "allow", "rules": []}},
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setenv("ACP_CONFIG", str(cfg_path))

    from app.cli import main
    assert main(["anchor"]) == 0
    assert "merkle root" in capsys.readouterr().out
    assert main(["anchor-verify"]) == 0
    assert "OK" in capsys.readouterr().out
