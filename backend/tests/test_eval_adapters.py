"""Real eval adapters: the offline golden adapter and the http endpoint adapter.

The http path is exercised end to end against a tiny local model server, so the
adapter genuinely POSTs prompts and scores the responses — no mocking of the
transport. Both adapters run through the hub (the same seam the scheduler and
gate use), and a config-defined http suite is registered and run by name.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import Settings
from app.engines import EngineHub
from app.evals import load_suites


def _settings(tmp_path, **engines) -> Settings:
    base = {"policy": {"default": "allow", "rules": []}}
    base.update(engines)
    return Settings(
        database={"url": f"sqlite+aiosqlite:///{tmp_path}/e.db"},
        auth={"jwt_secret": secrets.token_hex(16)},
        data_dir=str(tmp_path / "data"),
        engines=base,
    )


# --- golden (offline) ------------------------------------------------------ #
def test_golden_adapter_scores_offline(tmp_path):
    hub = EngineHub(_settings(tmp_path))
    try:
        result, _ = hub.run_eval("assistant-smoke", "offline")
        assert result.adapter == "golden"
        assert result.pass_rate == 1.0
        assert len(result.cases) == 4
    finally:
        hub.close()


def test_unknown_suite_raises(tmp_path):
    hub = EngineHub(_settings(tmp_path))
    try:
        with pytest.raises(KeyError):
            hub.run_eval("does-not-exist")
    finally:
        hub.close()


# --- http endpoint --------------------------------------------------------- #
_ANSWERS = {
    "capital of france": "answer: paris",
    "2+3": "answer: 5",
}


class _ModelHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def log_message(self, *a):  # keep the test output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append(body)
        out = _ANSWERS.get(body.get("input"), "answer: unknown")
        payload = json.dumps({
            "output": out,
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def model_server():
    _ModelHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _http_suite(base_url: str) -> dict:
    return {
        "name": "qa-http",
        "version": "1",
        "cases": [
            {"id": "cap", "input": "capital of france",
             "assert": [{"type": "contains", "value": "paris"}]},
            {"id": "sum", "input": "2+3",
             "assert": [{"type": "contains", "value": "5"}]},
        ],
        "adapter": {"type": "http", "model": "local-model", "base_url": base_url},
    }


def test_http_adapter_posts_and_scores(tmp_path, model_server):
    hub = EngineHub(_settings(tmp_path))
    try:
        result, _ = hub.run_eval(_http_suite(model_server), "http")
        assert result.adapter == "http"
        assert result.pass_rate == 1.0
        # The adapter actually POSTed each case input to the endpoint.
        inputs = {r.get("input") for r in _ModelHandler.received}
        assert inputs == {"capital of france", "2+3"}
    finally:
        hub.close()


def test_config_http_suite_is_registered_and_runnable(tmp_path, model_server):
    settings = _settings(tmp_path, eval={"suites": [_http_suite(model_server)]})
    hub = EngineHub(settings)
    hub.set_eval_suites(load_suites(settings))
    try:
        names = hub.eval_suite_names()
        assert "qa-http" in names and "assistant-smoke" in names
        meta = {m["name"]: m for m in hub.eval_suites_meta()}
        assert meta["qa-http"]["adapter"] == "http"
        assert meta["qa-http"]["runnable"] is True
        # Run it by name through the same seam the scheduler uses.
        result, _ = hub.run_eval("qa-http", "by-name")
        assert result.pass_rate == 1.0
    finally:
        hub.close()
